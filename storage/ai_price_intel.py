# storage/ai_price_intel.py
"""
Claude Price Intelligence — Call 4 in the production pipeline.

Takes extracted menu items + Google Places competitor context + cuisine type
+ region and asks Claude to assess pricing: underpriced / fair / overpriced
for each item, with suggested price ranges based on local market data.

Usage:
    from storage.ai_price_intel import analyze_menu_prices

    result = analyze_menu_prices(
        draft_id=42,
        restaurant_id=7,
    )
    # result = {
    #     "assessments":     [...],   # per-item price assessments
    #     "category_avgs":   {...},   # avg prices per category
    #     "market_context":  {...},   # summary of local market
    #     "model":           "...",
    #     "total_items":     25,
    #     "items_assessed":  25,
    #     "skipped":         False,
    # }

Requires ANTHROPIC_API_KEY in environment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Reuse shared Anthropic client
from .ai_menu_extract import _get_client

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_MODEL = "claude-sonnet-4-5"
_MAX_TOKENS = 8_000    # Per-batch token limit (batched by category)
_MAX_ITEMS_PER_BATCH = 40  # Split large categories into sub-batches

# DB path (same as other storage modules)
DB_PATH = Path(__file__).resolve().parents[1] / "storage" / "servline.db"

# Valid price assessments from Claude
VALID_ASSESSMENTS = frozenset({
    "underpriced", "fair", "slightly_underpriced",
    "slightly_overpriced", "overpriced", "unknown",
})

# Min items required to run price intelligence
MIN_ITEMS_FOR_ANALYSIS = 3


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_schema() -> None:
    """Create the price_intelligence_results table if it doesn't exist."""
    with _db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_intelligence_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id        INTEGER NOT NULL,
                restaurant_id   INTEGER NOT NULL,
                item_id         INTEGER,
                item_name       TEXT NOT NULL,
                item_category   TEXT,
                current_price   INTEGER NOT NULL DEFAULT 0,
                assessment      TEXT NOT NULL DEFAULT 'unknown',
                suggested_low   INTEGER,
                suggested_high  INTEGER,
                regional_avg    INTEGER,
                reasoning       TEXT,
                confidence      REAL DEFAULT 0.0,
                price_sources   TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (draft_id) REFERENCES drafts(id) ON DELETE CASCADE
            )
        """)
        # Migration: add price_sources if missing
        try:
            conn.execute("ALTER TABLE price_intelligence_results ADD COLUMN price_sources TEXT")
        except Exception:
            pass
        # Day 141.7: comparison_count tracks how many REAL competitor
        # matches drove the aggregated range. Helps the UI say "range
        # based on 7 similar items across competitors" vs one lonely hit.
        try:
            conn.execute("ALTER TABLE price_intelligence_results ADD COLUMN comparison_count INTEGER DEFAULT 0")
        except Exception:
            pass
        # Day 141.8: median is the primary stat for the market-range UX
        try:
            conn.execute("ALTER TABLE price_intelligence_results ADD COLUMN median_price INTEGER")
        except Exception:
            pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_intelligence_summary (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id        INTEGER NOT NULL UNIQUE,
                restaurant_id   INTEGER NOT NULL,
                cuisine_type    TEXT,
                zip_code        TEXT,
                competitor_count INTEGER DEFAULT 0,
                avg_market_tier TEXT,
                total_items     INTEGER DEFAULT 0,
                items_assessed  INTEGER DEFAULT 0,
                underpriced     INTEGER DEFAULT 0,
                fair_priced     INTEGER DEFAULT 0,
                overpriced      INTEGER DEFAULT 0,
                category_avgs   TEXT,
                model_used      TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_intel_draft "
            "ON price_intelligence_results(draft_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_intel_item "
            "ON price_intelligence_results(item_id)"
        )
        conn.commit()


# Run on import
_ensure_schema()


# ---------------------------------------------------------------------------
# Competitor Menu Fetching (Day 141.5 overhaul)
# ---------------------------------------------------------------------------

# Common category synonyms for matching
_CATEGORY_SYNONYMS = {
    "entrees": {"mains", "main courses", "dinner", "lunch"},
    "appetizers": {"starters", "apps", "small plates", "shareables"},
    "salads": {"greens"},
    "sandwiches": {"subs", "heroes", "hoagies", "hot sandwiches"},
    "burgers": {"hamburgers"},
    "pizza": {"pizzas", "pies"},
    "wraps": {"burritos", "rolls"},
    "desserts": {"sweets"},
    "beverages": {"drinks", "cocktails", "beer", "wine"},
    "soups": {"soup"},
    "sides": {"side dishes", "side orders"},
    "breakfast": {"brunch", "morning"},
    "pasta": {"pastas", "italian"},
    "seafood": {"fish"},
    "wings": {"chicken wings", "wing"},
    "calzones": {"stromboli"},
}

# Build reverse lookup: "mains" -> "entrees", etc.
_CAT_NORMALIZE = {}
for canonical, synonyms in _CATEGORY_SYNONYMS.items():
    _CAT_NORMALIZE[canonical] = canonical
    for syn in synonyms:
        _CAT_NORMALIZE[syn] = canonical


def _normalize_category(cat: str) -> str:
    """Normalize a category name for matching."""
    c = cat.lower().strip()
    # Strip common suffixes
    for suffix in [" menu", " items", " options", " specials", " platters"]:
        if c.endswith(suffix):
            c = c[:-len(suffix)].strip()
    return _CAT_NORMALIZE.get(c, c)


def _categories_overlap(our_cats: set, their_cats: set) -> set:
    """Find overlapping categories between two sets (normalized)."""
    our_norm = {_normalize_category(c) for c in our_cats}
    their_norm = {_normalize_category(c) for c in their_cats}
    return our_norm & their_norm


def _fetch_competitor_menus(
    competitor_data: List[Dict[str, Any]],
    items: List[Dict[str, Any]],
    max_competitors: int = 5,
    max_fresh_searches: int = 3,
    user_tier: str = "free",
) -> List[Dict[str, Any]]:
    """
    Fetch real menu data for the top same-tier competitors.
    Checks cache first, Apify-scrapes if miss. Filters out competitors
    with no category overlap.

    Gated by tier (Day 141.7): free-tier users get no real competitor data —
    Claude Call 4 falls back to market-rate estimates only. Paid tiers
    (premium) get full Apify-scraped competitor menus.

    Returns list of:
        {
            "place_name": str,
            "place_id": str,
            "price_label": str,
            "rating": float,
            "categories": [str],
            "matching_categories": [str],
            "items": [{name, price_cents, category}, ...],
            "source": "scraped" | "web_search" | "not_found",
        }
    """
    from storage.price_intel import scrape_competitor_menu

    # Tier gate: free users get no competitor scraping
    if (user_tier or "free").lower() != "premium":
        log.info("Price intel: skipping competitor menu fetch for non-premium tier (%s)", user_tier)
        return []

    # Extract our categories
    our_cats = set()
    for it in items:
        cat = (it.get("category") or "").strip()
        if cat:
            our_cats.add(cat)

    # Compute our tier
    priced = [it["price_cents"] for it in items if it.get("price_cents") and it["price_cents"] > 0]
    our_avg = sum(priced) / len(priced) if priced else 0
    our_tier = 1 if our_avg < 1000 else (2 if our_avg < 2000 else (3 if our_avg < 3500 else 4))

    # Sort by tier similarity, then by rating
    def _sort_key(c):
        tier_dist = abs((c.get("price_level") or 2) - our_tier)
        rating = -(c.get("rating") or 0)
        return (tier_dist, rating)

    sorted_comps = sorted(competitor_data, key=_sort_key)
    candidates = [c for c in sorted_comps[:max_competitors] if c.get("place_id")]

    # Day 141.7: parallel Playwright scraping. Earlier parallel attempt
    # failed because menus-r-us (Apify) deduped concurrent calls to the
    # same actor — gave identical datasets to every worker. We've since
    # moved the primary scrape path to Playwright-in-process + Claude
    # Vision: each Chromium runs in its own process, each Vision call is
    # an independent Anthropic request. No shared-dataset risk.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _scrape_one(comp):
        place_id = comp["place_id"]
        place_name = comp.get("place_name", "Unknown")
        try:
            return comp, scrape_competitor_menu(place_id, place_name, force_refresh=False)
        except Exception as e:
            log.warning("Failed to fetch menu for %s: %s", place_name, e)
            return comp, None

    fetched: List[tuple] = []
    # 3 concurrent scrapes — balances parallelism against memory. Each
    # Playwright + Vision run uses ~500MB-1GB peak, so 3x = ~2-3 GB peak.
    max_workers = min(3, len(candidates))
    if max_workers > 0:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for fut in as_completed([pool.submit(_scrape_one, c) for c in candidates]):
                fetched.append(fut.result())

    # Preserve original ordering (distance-sorted) in the output
    order = {c["place_id"]: i for i, c in enumerate(candidates)}
    fetched.sort(key=lambda p: order.get(p[0]["place_id"], 9999))

    fresh_count = sum(1 for _, md in fetched if md and not md.get("from_cache"))
    log.info("Scraped %d competitors in parallel (%d fresh)", len(fetched), fresh_count)

    results = []
    for comp, menu_data in fetched:
        place_id = comp["place_id"]
        place_name = comp.get("place_name", "Unknown")

        raw_items = menu_data.get("items", []) if menu_data else []
        try:
            from storage.menu_classifier import filter_comparison_items
            their_items = filter_comparison_items(raw_items)
        except Exception:
            their_items = raw_items
        their_cats = {it.get("category", "Other") for it in their_items}
        overlap = _categories_overlap(our_cats, their_cats)

        if their_items and not overlap:
            log.info("No exact category overlap for %s, but keeping", place_name)

        results.append({
            "place_name": place_name,
            "place_id": place_id,
            "price_label": comp.get("price_label", "N/A"),
            "rating": comp.get("rating"),
            "categories": list(their_cats),
            "matching_categories": list(overlap),
            "items": their_items,
            "source": menu_data.get("source", "not_found") if menu_data else "not_found",
        })

    log.info(
        "Fetched %d competitor menus in parallel (%d with items, %d fresh)",
        len(results),
        sum(1 for r in results if r["items"]),
        fresh_count,
    )
    return results


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def _build_prompt(
    items: List[Dict[str, Any]],
    cuisine_type: str,
    zip_code: str,
    competitor_data: List[Dict[str, Any]],
    market_summary: Dict[str, Any],
    competitor_menus: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Build the Claude Call 4 prompt with menu items + real competitor prices."""

    # Format our items
    items_text = []
    for item in items:
        price_str = f"${item['price_cents'] / 100:.2f}" if item.get("price_cents") else "no price"
        cat = item.get("category") or "Uncategorized"
        sub = item.get("subcategory") or ""
        cat_label = f"{cat} > {sub}" if sub else cat
        items_text.append(f"- {item['name']} | {cat_label} | {price_str}")
    items_block = "\n".join(items_text)

    # Market summary
    market_lines = []
    if market_summary.get("has_data"):
        market_lines.append(f"Competitors found: {market_summary.get('competitor_count', 0)}")
        if market_summary.get("avg_rating"):
            market_lines.append(f"Average rating: {market_summary['avg_rating']}")
        dist = market_summary.get("price_distribution", {})
        if dist:
            market_lines.append("Price tier distribution: " + ", ".join(f"{k}: {v}" for k, v in dist.items()))
    market_block = "\n".join(market_lines) if market_lines else "No market summary available."

    # Format real competitor menus (the key improvement)
    real_menu_block = ""
    no_menu_block = ""
    menus_with_data = []
    menus_without_data = []

    if competitor_menus:
        for cm in competitor_menus:
            if cm.get("items"):
                menus_with_data.append(cm)
            else:
                menus_without_data.append(cm)

    if menus_with_data:
        menu_sections = []
        for cm in menus_with_data:
            lines = [f"\n{cm['place_name']} ({cm.get('price_label', 'N/A')}, ★{cm.get('rating', 'N/A')}):"]
            # Group items by matching categories, cap at 50 items per competitor
            by_cat: Dict[str, List] = {}
            for it in cm["items"][:50]:
                cat = it.get("category", "Other")
                by_cat.setdefault(cat, []).append(it)
            for cat, cat_items in by_cat.items():
                lines.append(f"  {cat}:")
                for it in cat_items:
                    if it.get("price_cents") and it["price_cents"] > 0:
                        lines.append(f"    - {it['name']}: ${it['price_cents'] / 100:.2f}")
            menu_sections.append("\n".join(lines))
        real_menu_block = "\n".join(menu_sections)

    if menus_without_data:
        no_menu_lines = []
        for cm in menus_without_data:
            no_menu_lines.append(f"- {cm['place_name']} ({cm.get('price_label', 'N/A')}, ★{cm.get('rating', 'N/A')}) — menu not available")
        no_menu_block = "\n".join(no_menu_lines)

    # Build the full prompt
    has_real_data = bool(menus_with_data)

    prompt = f"""\
You are a restaurant pricing analyst. Analyze each menu item's price against
the local market for a {cuisine_type} restaurant in zip code {zip_code}.

LOCAL MARKET CONTEXT:
{market_block}

MENU ITEMS TO ANALYZE:
{items_block}
"""

    if has_real_data:
        prompt += f"""
COMPETITOR MENUS WITH REAL PRICES:
Below are ACTUAL menu items and prices from nearby competitors, verified from
their online ordering platforms. Use these as your PRIMARY reference.
{real_menu_block}
"""
        if no_menu_block:
            prompt += f"""
COMPETITORS WITHOUT MENU DATA:
{no_menu_block}
"""
        prompt += """
INSTRUCTIONS:
For each of our items, compare against the REAL competitor prices above.
- Match each item against the closest equivalent at each competitor.
- Base your suggested_low, suggested_high, and regional_avg on the REAL prices you see.
- Include "price_sources" — which specific competitor items you used for comparison.
- If no competitor has a matching item, base your assessment on category averages
  from the competitor menus and set confidence to 0.4-0.6.
- Do NOT guess or estimate competitor prices — use only what's provided above.
"""
    else:
        prompt += """
NOTE: No real competitor menu data was available. Provide your best market-based
estimates but set confidence to 0.3-0.5 for all assessments.
"""

    prompt += f"""
Return a JSON object with this exact structure:
{{
  "assessments": [
    {{
      "item_name": "exact item name from the list",
      "assessment": "underpriced|slightly_underpriced|fair|slightly_overpriced|overpriced|unknown",
      "suggested_low": cents (integer, low end of suggested range),
      "suggested_high": cents (integer, high end of suggested range),
      "regional_avg": cents (integer, estimated regional average for this type of item),
      "reasoning": "brief explanation referencing specific competitor prices when available",
      "confidence": 0.0-1.0,
      "price_sources": [
        {{"competitor": "Restaurant Name", "item": "Their Item Name", "price_cents": 1499}}
      ]
    }}
  ],
  "category_averages": {{
    "Category Name": {{
      "avg_price_cents": integer,
      "typical_range_low": integer,
      "typical_range_high": integer,
      "item_count": integer
    }}
  }},
  "market_context": {{
    "market_tier": "$|$$|$$$|$$$$",
    "price_pressure": "low|moderate|high",
    "summary": "1-2 sentence market summary"
  }}
}}

RULES:
- Return ONLY valid JSON, no markdown fencing.
- Every item from the input must appear in assessments (same order).
- All prices in cents (e.g., $12.99 = 1299).
- If an item has no price (0 cents), set assessment to "unknown".
- "price_sources" should list the real competitor items you compared against (empty array if none).
- Be practical: a $2 difference on a $15 entree is "fair", not "overpriced"."""

    return prompt


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------
def _call_claude(
    prompt: str,
    *,
    model: str = _DEFAULT_MODEL,
) -> Optional[Dict[str, Any]]:
    """Send prompt to Claude and parse JSON response."""
    client = _get_client()
    if not client:
        log.error("No Anthropic client available for price intelligence")
        return None

    try:
        t0 = time.time()
        response = client.messages.create(
            model=model,
            max_tokens=_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = time.time() - t0
        log.info("Price intel Claude call: %.1fs, model=%s", elapsed, model)

        # Extract text content
        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        if not text.strip():
            log.error("Empty response from Claude for price intelligence")
            return None

        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)

        data = json.loads(text)
        data["_model"] = model
        data["_elapsed"] = elapsed
        return data

    except json.JSONDecodeError as exc:
        log.error("Failed to parse price intel JSON: %s", exc)
        return None
    except Exception as exc:
        log.error("Price intel Claude API error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Batching: split items by category, sub-batch if too large
# ---------------------------------------------------------------------------
def _make_batches(items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group items by category, then split any group that exceeds the batch limit."""
    from collections import OrderedDict

    by_cat: OrderedDict[str, List[Dict[str, Any]]] = OrderedDict()
    for item in items:
        cat = item.get("category") or "Uncategorized"
        by_cat.setdefault(cat, []).append(item)

    batches: List[List[Dict[str, Any]]] = []
    for cat, cat_items in by_cat.items():
        # Sub-batch large categories
        for i in range(0, len(cat_items), _MAX_ITEMS_PER_BATCH):
            batches.append(cat_items[i : i + _MAX_ITEMS_PER_BATCH])

    # If total items fit in one batch, just send everything together
    if len(items) <= _MAX_ITEMS_PER_BATCH:
        return [items]

    return batches


def _merge_batch_results(
    batch_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge multiple batch results into a single unified result."""
    merged_assessments: List[Dict[str, Any]] = []
    merged_cat_avgs: Dict[str, Any] = {}
    market_context: Dict[str, Any] = {}
    total_elapsed = 0.0
    model_used = _DEFAULT_MODEL

    for result in batch_results:
        merged_assessments.extend(result.get("assessments", []))
        merged_cat_avgs.update(result.get("category_averages", {}))
        # Take the last non-empty market context
        if result.get("market_context"):
            market_context = result["market_context"]
        total_elapsed += result.get("_elapsed", 0)
        if result.get("_model"):
            model_used = result["_model"]

    return {
        "assessments": merged_assessments,
        "category_averages": merged_cat_avgs,
        "market_context": market_context,
        "_model": model_used,
        "_elapsed": total_elapsed,
    }


# ---------------------------------------------------------------------------
# Result validation + normalization
# ---------------------------------------------------------------------------
def _normalize_assessment(raw: str) -> str:
    """Normalize assessment string to valid value."""
    raw = (raw or "").strip().lower().replace(" ", "_")
    return raw if raw in VALID_ASSESSMENTS else "unknown"


def _validate_results(
    data: Dict[str, Any],
    items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate and normalize Claude's response."""
    assessments = data.get("assessments", [])

    # Build lookup by normalized name
    item_by_name: Dict[str, Dict[str, Any]] = {}
    for item in items:
        key = (item.get("name") or "").strip().lower()
        item_by_name[key] = item

    validated = []
    for a in assessments:
        name = (a.get("item_name") or "").strip()
        name_key = name.lower()

        # Match to original item
        matched_item = item_by_name.get(name_key)
        item_id = matched_item["id"] if matched_item else None

        validated.append({
            "item_name": name,
            "item_id": item_id,
            "item_category": matched_item.get("category") if matched_item else None,
            "current_price": matched_item.get("price_cents", 0) if matched_item else 0,
            "assessment": _normalize_assessment(a.get("assessment", "")),
            "suggested_low": int(a["suggested_low"]) if a.get("suggested_low") else None,
            "suggested_high": int(a["suggested_high"]) if a.get("suggested_high") else None,
            "regional_avg": int(a["regional_avg"]) if a.get("regional_avg") else None,
            "reasoning": (a.get("reasoning") or "")[:500],
            "confidence": min(1.0, max(0.0, float(a.get("confidence", 0)))),
            "price_sources": a.get("price_sources", []),
        })

    category_avgs = data.get("category_averages", {})
    market_context = data.get("market_context", {})

    return {
        "assessments": validated,
        "category_avgs": category_avgs,
        "market_context": market_context,
    }


# ---------------------------------------------------------------------------
# Storage: save results to DB
# ---------------------------------------------------------------------------
def _save_results(
    draft_id: int,
    restaurant_id: int,
    validated: Dict[str, Any],
    cuisine_type: str,
    zip_code: str,
    competitor_count: int,
    model: str,
) -> None:
    """Persist price intelligence results to the database."""
    now = _now()

    assessments = validated["assessments"]
    category_avgs = validated.get("category_avgs", {})
    market_ctx = validated.get("market_context", {})

    # Count assessments by type
    under = sum(1 for a in assessments if a["assessment"] in ("underpriced", "slightly_underpriced"))
    fair = sum(1 for a in assessments if a["assessment"] == "fair")
    over = sum(1 for a in assessments if a["assessment"] in ("overpriced", "slightly_overpriced"))

    with _db_connect() as conn:
        # Clear old results for this draft
        conn.execute(
            "DELETE FROM price_intelligence_results WHERE draft_id = ?",
            (draft_id,),
        )

        # Insert per-item results
        for a in assessments:
            conn.execute(
                """INSERT INTO price_intelligence_results
                   (draft_id, restaurant_id, item_id, item_name, item_category,
                    current_price, assessment, suggested_low, suggested_high,
                    regional_avg, reasoning, confidence, price_sources, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    draft_id, restaurant_id, a.get("item_id"),
                    a["item_name"], a.get("item_category"),
                    a.get("current_price", 0), a["assessment"],
                    a.get("suggested_low"), a.get("suggested_high"),
                    a.get("regional_avg"), a.get("reasoning"),
                    a.get("confidence", 0.0),
                    json.dumps(a.get("price_sources", [])),
                    now,
                ),
            )

        # Upsert summary
        conn.execute(
            """INSERT INTO price_intelligence_summary
               (draft_id, restaurant_id, cuisine_type, zip_code,
                competitor_count, avg_market_tier, total_items, items_assessed,
                underpriced, fair_priced, overpriced, category_avgs,
                model_used, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(draft_id)
               DO UPDATE SET
                   competitor_count = excluded.competitor_count,
                   avg_market_tier = excluded.avg_market_tier,
                   total_items = excluded.total_items,
                   items_assessed = excluded.items_assessed,
                   underpriced = excluded.underpriced,
                   fair_priced = excluded.fair_priced,
                   overpriced = excluded.overpriced,
                   category_avgs = excluded.category_avgs,
                   model_used = excluded.model_used,
                   updated_at = excluded.updated_at""",
            (
                draft_id, restaurant_id, cuisine_type, zip_code,
                competitor_count, market_ctx.get("market_tier", "$$"),
                len(assessments),
                sum(1 for a in assessments if a["assessment"] != "unknown"),
                under, fair, over,
                json.dumps(category_avgs),
                model, now, now,
            ),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Preload helpers (Day 141.7)
# ---------------------------------------------------------------------------
def _preload_comparisons_async(
    draft_id: int,
    restaurant_id: int,
    competitors: List[Dict[str, Any]],
    max_workers: int = 4,
) -> None:
    """Fire background threads that precompute per-competitor comparisons.

    When the user clicks "Compare" on a competitor in the editor, the
    `compare_with_competitor` call takes 10-30s (cached menu + Claude
    Sonnet match). Running it eagerly here — in parallel — means most
    clicks hit the cache instantly. Costs ~$0.30-0.50 total in Claude
    tokens per full preload, worth it for the UX.

    Errors are swallowed per-task: a failed preload just means that one
    competitor's click will run on-demand like before.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    def _worker():
        try:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(_preload_one_comparison, draft_id, restaurant_id, comp)
                    for comp in competitors
                ]
                # Wait for all comparisons before aggregating. The pool's
                # __exit__ blocks until every submitted task finishes.
        except Exception as e:
            log.warning("Comparison preload pool failed: %s", e)

        # Day 141.7: once every per-competitor comparison is cached, roll
        # up the matched prices and replace Claude's invented ranges with
        # real computed stats. Runs last so it has all the data.
        try:
            n = _aggregate_price_ranges(draft_id)
            log.info("Aggregated price ranges updated %d items for draft %d", n, draft_id)
        except Exception as e:
            log.warning("Price range aggregation failed for draft %d: %s", draft_id, e)

    threading.Thread(target=_worker, daemon=True).start()


def _gemini_search_prices(items: List[Dict[str, Any]], city: str, state: str,
                          zip_code: str, cuisine: str) -> Dict[int, Dict[str, Any]]:
    """Use Gemini with Google Search grounding to get real market prices.

    Batches items and asks Gemini to search Google for actual local pricing.
    Returns {item_id: {low, high, median, sizes: {...}}} in cents.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key or not items:
        return {}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.warning("google-genai not installed")
        return {}

    client = genai.Client(api_key=api_key)
    out: Dict[int, Dict[str, Any]] = {}
    batch_size = 10

    def _run_batch(batch):
        item_lines = ""
        for it in batch:
            variants = it.get("variants", [])
            if variants:
                sizes_str = ", ".join(v["label"] for v in variants if v.get("label"))
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]}) [sizes: {sizes_str}]\n'
            else:
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]})\n'

        prompt = f"""For each item below, give me a low-high price range using price data from 5 restaurants in {city}, {state}.

For items WITHOUT sizes, search: "(item name) (category) price in {city}, {state}"
For items WITH sizes, search EACH size separately: "(size) (item name) (category) price in {city}, {state}"

Items:
{item_lines}

Return JSON only — an array:
[{{"id": 123, "low_cents": 800, "high_cents": 1400, "median_cents": 1100, "sizes": null,
   "sources": [{{"restaurant": "Joe's Pizza", "price_cents": 899}}, {{"restaurant": "Main St Pizzeria", "price_cents": 1200}}]
}}]

For items with [sizes], include per-size ranges AND sources per size:
{{"id": 123, "low_cents": 800, "high_cents": 2500, "median_cents": 1500,
  "sources": [{{"restaurant": "Joe's Pizza", "price_cents": 899}}],
  "sizes": {{"12\\" Sml": {{"low_cents": 800, "high_cents": 1400, "median_cents": 1100,
    "sources": [{{"restaurant": "Joe's Pizza", "price_cents": 899}}, {{"restaurant": "Main St Pizzeria", "price_cents": 1200}}]
  }}}}
}}

Rules:
- Use real price data from 5 restaurants in {city}, {state}
- Prices in US cents (e.g. $9.00 = 900)
- Include the actual restaurant name and price for each source you find
- low_cents and high_cents MUST be different — if you only find one price, widen the range by +/- 15%
- If you can't find real data for an item, set low_cents to 0
- Use the item ID numbers exactly as given
- Return ONLY the JSON array, no other text"""

        try:
            # Retry up to 2 times on transient errors (503, 429)
            response = None
            for attempt in range(3):
                try:
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[types.Tool(google_search=types.GoogleSearch())],
                            temperature=0.1,
                        ),
                    )
                    break
                except Exception as retry_err:
                    err_str = str(retry_err)
                    if ("503" in err_str or "429" in err_str or "UNAVAILABLE" in err_str) and attempt < 2:
                        time.sleep(5 * (attempt + 1))
                        continue
                    raise
            if not response:
                return {}
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            results = json.loads(text)
            batch_out = {}
            for r in results:
                iid = r.get("id")
                low = r.get("low_cents", 0)
                high = r.get("high_cents", 0)
                med = r.get("median_cents", 0)
                if iid and low > 0 and high > 0 and med > 0:
                    entry = {"low": int(low), "high": int(high), "median": int(med)}
                    # Capture source restaurants
                    raw_sources = r.get("sources")
                    if isinstance(raw_sources, list):
                        entry["sources"] = [
                            {"restaurant": s.get("restaurant", ""), "price_cents": s.get("price_cents", 0)}
                            for s in raw_sources if isinstance(s, dict) and s.get("restaurant")
                        ]
                    raw_sizes = r.get("sizes")
                    if isinstance(raw_sizes, dict):
                        sizes_out = {}
                        for slabel, sdata in raw_sizes.items():
                            if isinstance(sdata, dict):
                                sl = sdata.get("low_cents", 0)
                                sh = sdata.get("high_cents", 0)
                                sm = sdata.get("median_cents", 0)
                                if sl > 0 and sh > 0 and sm > 0:
                                    size_entry = {"low": int(sl), "high": int(sh), "median": int(sm)}
                                    ss = sdata.get("sources")
                                    if isinstance(ss, list):
                                        size_entry["sources"] = [
                                            {"restaurant": s.get("restaurant", ""), "price_cents": s.get("price_cents", 0)}
                                            for s in ss if isinstance(s, dict) and s.get("restaurant")
                                        ]
                                    sizes_out[slabel] = size_entry
                        if sizes_out:
                            entry["sizes"] = sizes_out
                    batch_out[int(iid)] = entry
            log.info("Gemini search batch: got %d items with real prices", len(batch_out))
            return batch_out
        except Exception as e:
            log.warning("Gemini search batch failed: %s", e)
            return {}

    # Run ALL batches in parallel — Gemini allows ~1000 RPM on paid tier
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(batches)) as pool:
        futures = [pool.submit(_run_batch, b) for b in batches]
        for f in futures:
            try:
                out.update(f.result())
            except Exception:
                pass

    log.info("Gemini search: got real prices for %d/%d items", len(out), len(items))
    return out


def _search_item_prices(items: List[Dict[str, Any]], city: str, state: str,
                        zip_code: str, cuisine: str) -> Dict[int, Dict[str, Any]]:
    """Google-search real prices for each menu item, then extract with Haiku.

    For each item (+ size variants), searches Google for actual local pricing,
    then passes the search results to Haiku to extract low/high/median.

    Returns {item_id: {low, high, median, sizes: {label: {low, high, median}}}} in cents.
    """
    if not items:
        return {}

    client = _get_client()
    if not client:
        return {}

    # Step 1: Google search for each unique item (dedupe by name+category)
    # Items with sizes get one search per size
    search_tasks = []  # (item_id, search_query, size_label_or_None)
    seen_queries = {}  # query -> [(item_id, size_label)]

    for it in items:
        name = it["item_name"]
        category = it.get("category", "")
        variants = it.get("variants", [])

        if variants:
            for v in variants:
                slabel = v.get("label", "")
                q = f"{name} {slabel} {cuisine} price in {city} {state}"
                if q not in seen_queries:
                    seen_queries[q] = []
                    search_tasks.append((it["item_id"], q, slabel))
                seen_queries[q].append((it["item_id"], slabel))
        else:
            q = f"{name} {cuisine} price in {city} {state}"
            if q not in seen_queries:
                seen_queries[q] = []
                search_tasks.append((it["item_id"], q, None))
            seen_queries[q].append((it["item_id"], None))

    # Step 2: Run Google searches in parallel
    from concurrent.futures import ThreadPoolExecutor
    search_results = {}  # query -> text

    def _do_search(task):
        iid, query, slabel = task
        text = _web_search_text(query)
        return query, text

    log.info("Running %d Google searches for price data...", len(search_tasks))
    with ThreadPoolExecutor(max_workers=5) as pool:
        for query, text in pool.map(lambda t: _do_search(t), search_tasks):
            if text:
                search_results[query] = text

    log.info("Got %d/%d search results", len(search_results), len(search_tasks))

    # Step 3: Batch the search results to Haiku for extraction
    # Group items into batches of 15 for extraction
    out: Dict[int, Dict[str, Any]] = {}
    batch_size = 15
    extraction_items = []

    for it in items:
        name = it["item_name"]
        category = it.get("category", "")
        variants = it.get("variants", [])
        item_searches = {}

        if variants:
            for v in variants:
                slabel = v.get("label", "")
                q = f"{name} {slabel} {cuisine} price in {city} {state}"
                if q in search_results:
                    item_searches[slabel] = search_results[q]
        else:
            q = f"{name} {cuisine} price in {city} {state}"
            if q in search_results:
                item_searches["_base"] = search_results[q]

        if item_searches:
            extraction_items.append({
                "item_id": it["item_id"],
                "item_name": name,
                "category": category,
                "searches": item_searches,
                "has_sizes": bool(variants),
            })

    def _extract_batch(batch):
        """Send a batch of search results to Haiku for price extraction."""
        prompt_parts = []
        for ei in batch:
            prompt_parts.append(f'## Item #{ei["item_id"]}: "{ei["item_name"]}" ({ei["category"]})')
            for label, text in ei["searches"].items():
                tag = f" [{label}]" if label != "_base" else ""
                prompt_parts.append(f"Search results{tag}:\n{text[:2500]}\n")

        prompt = f"""You are a restaurant pricing analyst. Extract REAL prices from the Google search
results below. These are actual prices from restaurants in {city}, {state}.

{chr(10).join(prompt_parts)}

For each item, return the price range you found in the search results.
Return JSON only — an array:
[{{"id": 123, "low_cents": 800, "high_cents": 1400, "median_cents": 1100, "sizes": null}}]

For items with size-specific search results ([12" Sml], [16" Lrg], etc.), include per-size ranges:
{{"id": 123, "low_cents": 800, "high_cents": 2500, "median_cents": 1500,
  "sizes": {{"12\\" Sml": {{"low_cents": 800, "high_cents": 1400, "median_cents": 1100}}}}
}}

Rules:
- ONLY use prices you can actually see in the search results text
- If no prices found for an item, use low_cents: 0 (we'll skip it)
- Prices in US cents
- Return ONLY the JSON array"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            results = json.loads(text)
            batch_out = {}
            for r in results:
                iid = r.get("id")
                low = r.get("low_cents", 0)
                high = r.get("high_cents", 0)
                med = r.get("median_cents", 0)
                if iid and low > 0 and high > 0 and med > 0:
                    entry = {"low": int(low), "high": int(high), "median": int(med)}
                    raw_sizes = r.get("sizes")
                    if isinstance(raw_sizes, dict):
                        sizes_out = {}
                        for slabel, sdata in raw_sizes.items():
                            if isinstance(sdata, dict):
                                sl = sdata.get("low_cents", 0)
                                sh = sdata.get("high_cents", 0)
                                sm = sdata.get("median_cents", 0)
                                if sl > 0 and sh > 0 and sm > 0:
                                    sizes_out[slabel] = {"low": int(sl), "high": int(sh), "median": int(sm)}
                        if sizes_out:
                            entry["sizes"] = sizes_out
                    batch_out[int(iid)] = entry
            return batch_out
        except Exception as e:
            log.warning("Price extraction batch failed: %s", e)
            return {}

    # Run extraction batches in parallel
    batches = [extraction_items[i:i + batch_size]
               for i in range(0, len(extraction_items), batch_size)]

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_extract_batch, b) for b in batches]
        for f in futures:
            try:
                out.update(f.result())
            except Exception:
                pass

    log.info("Extracted prices for %d/%d items from Google search data", len(out), len(items))
    return out


def _estimate_item_market_rates(items: List[Dict[str, Any]], city: str, state: str,
                                zip_code: str, cuisine: str) -> Dict[int, Dict[str, Any]]:
    """Ask Haiku for per-item market price ranges. Batches of 40.

    items: list of {item_id, item_name, category, variants: [{label, price}]}
    Returns {item_id: {low, high, median, sizes: {label: {low, high, median}}}} in cents.
    """
    if not items:
        return {}

    client = _get_client()
    if not client:
        return {}

    out: Dict[int, Dict[str, Any]] = {}
    batch_size = 25

    def _run_batch(batch, batch_idx):
        """Process one batch, return parsed results."""
        item_lines = ""
        for it in batch:
            variants = it.get("variants", [])
            if variants:
                sizes_str = ", ".join(v["label"] for v in variants if v.get("label"))
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]}) [sizes: {sizes_str}]\n'
            else:
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]})\n'

        prompt = f"""You are a restaurant pricing analyst. For each menu item below,
provide the typical price range a customer would expect at a casual/mid-range
{cuisine} restaurant in {city}, {state} ({zip_code}).

Items:
{item_lines}

Return JSON only — an array of objects:
[{{"id": 123, "low_cents": 800, "high_cents": 1400, "median_cents": 1100, "sizes": null}}]

For items with [sizes], include per-size price ranges:
{{"id": 123, "low_cents": 800, "high_cents": 3500, "median_cents": 1800,
  "sizes": {{"12\\" Sml": {{"low_cents": 1000, "high_cents": 1600, "median_cents": 1300}}}}
}}

Rules:
- Prices in US cents (e.g. $9.00 = 900)
- low/high/median should reflect THIS SPECIFIC ITEM, not the whole category
- "French Fries" should have a different range than "Loaded Cheese Fries"
- "Cheese Pizza" should have a different range than "Meat Lovers Pizza"
- Toppings/add-ons (items clearly priced $1-$5) should get topping-level pricing
- For items WITHOUT [sizes], set "sizes" to null
- Use the item ID number exactly as given
- Return ONLY the JSON array"""

        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            results = json.loads(text)
            batch_out = {}
            for r in results:
                iid = r.get("id")
                low = r.get("low_cents", 0)
                high = r.get("high_cents", 0)
                med = r.get("median_cents", 0)
                if iid and low > 0 and high > 0 and med > 0:
                    entry = {"low": int(low), "high": int(high), "median": int(med)}
                    raw_sizes = r.get("sizes")
                    if isinstance(raw_sizes, dict):
                        sizes_out = {}
                        for slabel, sdata in raw_sizes.items():
                            if isinstance(sdata, dict):
                                sl = sdata.get("low_cents", 0)
                                sh = sdata.get("high_cents", 0)
                                sm = sdata.get("median_cents", 0)
                                if sl > 0 and sh > 0 and sm > 0:
                                    sizes_out[slabel] = {"low": int(sl), "high": int(sh), "median": int(sm)}
                        if sizes_out:
                            entry["sizes"] = sizes_out
                    batch_out[int(iid)] = entry
            log.info("Item market estimates batch %d: got %d items from Haiku",
                     batch_idx, len(batch_out))
            return batch_out
        except Exception as e:
            log.warning("Item market estimation batch %d failed: %s", batch_idx, e)
            return {}

    # Run all batches in parallel
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as pool:
        futures = [pool.submit(_run_batch, b, idx) for idx, b in enumerate(batches)]
        for f in futures:
            try:
                out.update(f.result())
            except Exception:
                pass

    return out


def _estimate_category_market_rates(categories: List[str], city: str, state: str,
                                    zip_code: str, cuisine: str,
                                    size_categories: Optional[Dict[str, List[str]]] = None,
                                    ) -> Dict[str, Any]:
    """Ask Haiku for typical market price ranges PER CATEGORY.

    Returns {category_name: {low, high, median, sizes: {label: {low, high, median}}}} in cents.
    If size_categories is provided, categories with sizes get per-size breakdowns.
    One API call for all categories — cheap and fast.
    """
    if not categories:
        return {}

    client = _get_client()
    if not client:
        return {}

    cat_list = ""
    for c in categories:
        sizes = (size_categories or {}).get(c)
        if sizes:
            cat_list += f"- {c} (sizes: {', '.join(sizes)})\n"
        else:
            cat_list += f"- {c}\n"

    prompt = f"""You are a restaurant pricing analyst. For each menu CATEGORY below,
provide the typical price range a customer would expect at a casual/mid-range
{cuisine} restaurant in {city}, {state} ({zip_code}).

Categories:
{cat_list}

Return JSON only — an array of objects, one per category:
[{{"category": "exact category name", "low_cents": 800, "high_cents": 1400, "median_cents": 1100, "sizes": null}}]

IMPORTANT: For categories that list sizes in parentheses, also include a "sizes" object
with per-size price ranges:
{{"category": "Pizza", "low_cents": 800, "high_cents": 3500, "median_cents": 1800,
  "sizes": {{
    "10\\" Mini": {{"low_cents": 700, "high_cents": 1100, "median_cents": 900}},
    "12\\" Sml": {{"low_cents": 1000, "high_cents": 1600, "median_cents": 1300}},
    "16\\" Lrg": {{"low_cents": 1400, "high_cents": 2200, "median_cents": 1800}},
    "Family Size 17x24\\"": {{"low_cents": 2200, "high_cents": 3500, "median_cents": 2800}}
  }}
}}

Rules:
- Prices in US cents (e.g. $9.00 = 900)
- low = cheapest item you'd typically see in this category/size
- high = most expensive item you'd typically see
- median = most common price point
- For categories that are clearly toppings/add-ons, give typical add-on pricing ($1-$3 range)
- For categories WITHOUT sizes listed, set "sizes" to null
- Return ONLY the JSON array, no other text"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        results = json.loads(text)
        out: Dict[str, Any] = {}
        for r in results:
            cat = (r.get("category") or "").strip()
            # Strip any "(sizes: ...)" suffix Haiku may echo back
            if " (sizes:" in cat:
                cat = cat[:cat.index(" (sizes:")].strip()
            low = r.get("low_cents", 0)
            high = r.get("high_cents", 0)
            med = r.get("median_cents", 0)
            if cat and low > 0 and high > 0 and med > 0:
                entry: Dict[str, Any] = {"low": int(low), "high": int(high), "median": int(med)}
                # Parse per-size breakdowns if present
                raw_sizes = r.get("sizes")
                if isinstance(raw_sizes, dict):
                    sizes_out = {}
                    for slabel, sdata in raw_sizes.items():
                        if isinstance(sdata, dict):
                            sl = sdata.get("low_cents", 0)
                            sh = sdata.get("high_cents", 0)
                            sm = sdata.get("median_cents", 0)
                            if sl > 0 and sh > 0 and sm > 0:
                                sizes_out[slabel] = {"low": int(sl), "high": int(sh), "median": int(sm)}
                    if sizes_out:
                        entry["sizes"] = sizes_out
                out[cat] = entry
        log.info("Category market estimates: got %d/%d from Haiku", len(out), len(categories))
        return out
    except Exception as e:
        log.warning("Category market estimation failed: %s", e)
        return {}


def _aggregate_price_ranges(draft_id: int) -> int:
    """Set market ranges for all items using per-item Haiku estimates.

    Day 141.8: Per-item estimates — each item gets its own range based on
    what it actually is ("French Fries" vs "Loaded Cheese Fries"), not just
    its category. Items with size variants get per-size breakdowns too.
    ~$0.02 per menu, ~10 seconds for 150 items.

    Returns the number of rows updated.
    """
    with _db_connect() as conn:
        conn.row_factory = sqlite3.Row

        our_rows = conn.execute(
            """SELECT pi.item_id, pi.item_name, pi.item_category, pi.current_price,
                      di.subcategory
               FROM price_intelligence_results pi
               LEFT JOIN draft_items di ON di.id = pi.item_id
               WHERE pi.draft_id = ?""",
            (draft_id,),
        ).fetchall()
        if not our_rows:
            return 0

        updated = 0
        total_underpriced = 0
        total_fair = 0
        total_overpriced = 0
        total_assessed = 0

        # Get restaurant location
        rest_row = conn.execute(
            """SELECT r.city, r.state, r.zip_code, r.cuisine_type
               FROM restaurants r JOIN drafts d ON d.restaurant_id = r.id
               WHERE d.id = ?""",
            (draft_id,),
        ).fetchone()
        city = (rest_row["city"] or "Unknown") if rest_row else "Unknown"
        state = (rest_row["state"] or "") if rest_row else ""
        zip_code = (rest_row["zip_code"] or "") if rest_row else ""
        cuisine = (rest_row["cuisine_type"] or "restaurant") if rest_row else "restaurant"

        # Build per-item variant info for size breakdowns
        var_rows = conn.execute(
            """SELECT div.item_id, div.label, div.price_cents
               FROM draft_item_variants div
               JOIN draft_items di ON di.id = div.item_id
               WHERE di.draft_id = ? AND div.kind = 'size' AND div.label IS NOT NULL""",
            (draft_id,),
        ).fetchall()
        item_variants: Dict[int, List[Dict[str, Any]]] = {}
        for vr in var_rows:
            iid = vr["item_id"]
            if iid not in item_variants:
                item_variants[iid] = []
            item_variants[iid].append({"label": (vr["label"] or "").strip(),
                                       "price": vr["price_cents"] or 0})

        # Build items list for Gemini/Haiku — skip items with no price (sauce/bread choices)
        haiku_items = []
        for row in our_rows:
            if not row["current_price"] or row["current_price"] <= 0:
                continue
            cat = (row["item_category"] or "").strip()
            subcat = (row["subcategory"] or "").strip() if row.keys().__contains__("subcategory") and row["subcategory"] else ""
            full_cat = f"{cat} {subcat}" if subcat else cat
            entry = {
                "item_id": row["item_id"],
                "item_name": (row["item_name"] or "").strip(),
                "category": full_cat,
            }
            if row["item_id"] in item_variants:
                entry["variants"] = item_variants[row["item_id"]]
            haiku_items.append(entry)

        # Gemini with Google Search grounding — real prices from real menus
        # Falls back to Haiku estimates for items Gemini can't find
        item_market = _gemini_search_prices(
            haiku_items, city, state, zip_code, cuisine)
        # Fill gaps with Haiku estimates for items Google didn't cover
        if len(item_market) < len(haiku_items):
            missing = [it for it in haiku_items if it["item_id"] not in item_market]
            if missing:
                log.info("Gemini missed %d items, falling back to Haiku estimates", len(missing))
                fallback = _estimate_item_market_rates(missing, city, state, zip_code, cuisine)
                for iid, data in fallback.items():
                    if iid not in item_market:
                        item_market[iid] = data

        for row in our_rows:
            current_price = row["current_price"] or 0
            mr = item_market.get(row["item_id"])

            if mr:
                low, high, median = mr["low"], mr["high"], mr["median"]
                avg = int(round((low + high) / 2))
                if current_price and median:
                    if current_price < low:
                        assessment = "below_market"
                        total_underpriced += 1
                    elif current_price > high:
                        assessment = "above_market"
                        total_overpriced += 1
                    elif current_price < median * 0.90:
                        assessment = "lower_range"
                        total_underpriced += 1
                    elif current_price > median * 1.10:
                        assessment = "higher_range"
                        total_overpriced += 1
                    else:
                        assessment = "fair"
                        total_fair += 1
                    total_assessed += 1
                else:
                    assessment = "unknown"
                source_info: Dict[str, Any] = {
                    "source": "market_estimate",
                    "location": f"{city}, {state} {zip_code}"}
                if mr.get("sources"):
                    source_info["sources"] = mr["sources"]
                if mr.get("sizes"):
                    source_info["sizes"] = mr["sizes"]
                conn.execute(
                    """UPDATE price_intelligence_results
                       SET suggested_low = ?,
                           suggested_high = ?,
                           regional_avg = ?,
                           median_price = ?,
                           assessment = ?,
                           price_sources = ?,
                           comparison_count = -2,
                           confidence = 0.35
                       WHERE draft_id = ? AND item_id = ?""",
                    (low, high, avg, median, assessment,
                     json.dumps([source_info]),
                     draft_id, row["item_id"]),
                )
            else:
                conn.execute(
                    """UPDATE price_intelligence_results
                       SET suggested_low = NULL,
                           suggested_high = NULL,
                           regional_avg = NULL,
                           median_price = NULL,
                           assessment = 'unknown',
                           price_sources = ?,
                           comparison_count = 0
                       WHERE draft_id = ? AND item_id = ?""",
                    (json.dumps([]), draft_id, row["item_id"]),
                )
            updated += 1

        # Refresh summary counts from the real data we just computed
        conn.execute(
            """UPDATE price_intelligence_summary
               SET items_assessed = ?,
                   underpriced = ?,
                   fair_priced = ?,
                   overpriced = ?,
                   updated_at = ?
               WHERE draft_id = ?""",
            (total_assessed, total_underpriced, total_fair, total_overpriced,
             _now(), draft_id),
        )

        conn.commit()
        return updated


def _preload_one_comparison(draft_id: int, restaurant_id: int, comp: Dict[str, Any]) -> None:
    """Single-competitor preload step. Skips if already cached.

    After the comparison lands, re-runs aggregation so any already-finished
    work stays visible in the editor even if the rest of the pool dies.
    Cheap — aggregation is pure Python math against cached rows.
    """
    try:
        name = comp.get("place_name") or ""
        if not name:
            return
        existing = get_competitor_comparison(draft_id, name)
        if existing:
            return
        compare_with_competitor(
            draft_id=draft_id,
            restaurant_id=restaurant_id,
            competitor=comp,
        )
        # Incremental aggregation: every finished comparison updates the
        # editor's ranges. If the preload thread dies halfway through,
        # the user still sees real data for what completed.
        try:
            _aggregate_price_ranges(draft_id)
        except Exception as e:
            log.info("Incremental aggregation failed after %s: %s", name, e)
    except Exception as e:
        log.info("Preload compare failed for %s: %s", comp.get("place_name"), e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def analyze_menu_prices(
    draft_id: int,
    restaurant_id: int,
    *,
    model: str = _DEFAULT_MODEL,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Run Claude Call 4: price intelligence on a draft's menu items.

    Pulls items from the draft, fetches Google Places competitor context,
    sends to Claude for per-item price assessment, and stores results.

    Returns dict with assessments, category_avgs, market_context, and metadata.
    """
    from storage.drafts import get_draft_items
    from storage.users import get_restaurant, get_restaurant_users, get_user_tier
    from storage.price_intel import (
        get_cached_comparisons,
        get_market_summary,
    )

    # Check for existing results (unless force refresh)
    if not force_refresh:
        existing = get_price_intelligence(draft_id)
        if existing and existing.get("assessments"):
            log.info("Price intel already exists for draft %d, returning cached", draft_id)
            existing["skipped"] = False
            existing["from_cache"] = True
            return existing

    # Get restaurant info
    restaurant = get_restaurant(restaurant_id)
    if not restaurant:
        return {"error": "Restaurant not found", "skipped": True, "assessments": []}

    cuisine_type = (restaurant.get("cuisine_type") or "other").strip().lower()
    zip_code = (restaurant.get("zip_code") or "").strip()

    # Get draft items
    items = get_draft_items(draft_id, include_variants=False)
    if not items:
        return {
            "error": "No items in draft",
            "skipped": True,
            "assessments": [],
            "total_items": 0,
        }

    if len(items) < MIN_ITEMS_FOR_ANALYSIS:
        return {
            "error": f"Need at least {MIN_ITEMS_FOR_ANALYSIS} items for price analysis",
            "skipped": True,
            "assessments": [],
            "total_items": len(items),
        }

    # Day 141.7: Tier lookup (premium wins if multiple users linked)
    user_tier = "free"
    try:
        linked_users = get_restaurant_users(restaurant_id)
        for u in linked_users:
            t = (get_user_tier(u["user_id"]) or "free").lower()
            if t == "premium":
                user_tier = "premium"
                break
    except Exception as e:
        log.warning("Tier lookup failed for restaurant %d: %s", restaurant_id, e)

    # Get competitor data from Google Places (cached from Day 134).
    # Day 141.7: if cache is empty, auto-trigger the search here so the
    # post-wizard analyzer is fully self-contained. Premium-only — free
    # tier doesn't need competitor data since Apify scraping is gated.
    competitor_data = get_cached_comparisons(restaurant_id)
    if not competitor_data and user_tier == "premium":
        log.info(
            "Price intel: no cached nearby competitors for rest %d — "
            "running Google Places search now",
            restaurant_id,
        )
        try:
            from storage.price_intel import search_nearby_restaurants
            search_result = search_nearby_restaurants(restaurant_id, force_refresh=False)
            if search_result.get("error"):
                log.warning(
                    "Price intel: nearby search failed for rest %d: %s",
                    restaurant_id, search_result["error"],
                )
            else:
                competitor_data = get_cached_comparisons(restaurant_id)
                log.info(
                    "Price intel: nearby search populated %d competitors for rest %d",
                    len(competitor_data), restaurant_id,
                )
        except Exception as e:
            log.warning("Price intel: nearby search raised for rest %d: %s", restaurant_id, e)

    market_summary = get_market_summary(restaurant_id)

    competitor_menus = []
    if competitor_data:
        log.info(
            "Price intel: fetching real competitor menus for draft %d (tier=%s)...",
            draft_id, user_tier,
        )
        competitor_menus = _fetch_competitor_menus(
            competitor_data=competitor_data,
            items=items,
            max_competitors=5,
            max_fresh_searches=5,
            user_tier=user_tier,
        )

    # Day 141.7: Bulk Call 4 (Sonnet per-category batch prompting) is
    # GONE. It added 15-20 min for Sonnet to invent ranges Claude later
    # couldn't trace back to real prices. Replaced with:
    #   1) Stub out price_intelligence_results (one row per item, no range yet)
    #   2) Fire per-competitor Opus+thinking comparisons in parallel
    #   3) _aggregate_price_ranges computes real range/avg/assessment from
    #      the actual matched competitor prices
    # The aggregator sets `assessment` from deterministic price math, not
    # an LLM's subjective opinion — and every number has a traceable source.
    _stub_price_intelligence(
        draft_id=draft_id,
        restaurant_id=restaurant_id,
        items=items,
        cuisine_type=cuisine_type,
        zip_code=zip_code,
        competitor_count=market_summary.get("competitor_count", 0),
    )

    if competitor_data:
        _preload_comparisons_async(
            draft_id=draft_id,
            restaurant_id=restaurant_id,
            competitors=competitor_data,
        )

    return {
        "assessments": [],  # filled in asynchronously by the aggregator
        "category_avgs": {},
        "market_context": {"market_tier": market_summary.get("avg_market_tier", "$$")},
        "model": _COMPARE_MODEL,  # Opus drives the real work now
        "total_items": len(items),
        "items_assessed": 0,  # aggregator updates summary after comparisons finish
        "skipped": False,
        "from_cache": False,
    }


def _stub_price_intelligence(
    *,
    draft_id: int,
    restaurant_id: int,
    items: List[Dict[str, Any]],
    cuisine_type: str,
    zip_code: str,
    competitor_count: int,
) -> None:
    """Seed price_intelligence_results with one row per user item + a
    summary row — all with blank range/assessment fields. The aggregator
    fills them in after comparisons complete."""
    now = _now()
    with _db_connect() as conn:
        # Wipe prior stubs so reruns don't leave stale rows
        conn.execute("DELETE FROM price_intelligence_results WHERE draft_id = ?", (draft_id,))
        conn.execute("DELETE FROM price_intelligence_summary WHERE draft_id = ?", (draft_id,))
        for it in items:
            conn.execute(
                """INSERT INTO price_intelligence_results
                   (draft_id, restaurant_id, item_id, item_name, item_category,
                    current_price, assessment, suggested_low, suggested_high,
                    regional_avg, reasoning, confidence, price_sources,
                    comparison_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'unknown', NULL, NULL, NULL, NULL,
                           0.0, ?, 0, ?)""",
                (
                    draft_id, restaurant_id, it.get("id"),
                    it.get("name") or "",
                    it.get("category") or "Uncategorized",
                    int(it.get("price_cents") or 0),
                    json.dumps([]), now,
                ),
            )
        conn.execute(
            """INSERT INTO price_intelligence_summary
               (draft_id, restaurant_id, cuisine_type, zip_code,
                competitor_count, avg_market_tier, total_items, items_assessed,
                underpriced, fair_priced, overpriced, category_avgs,
                model_used, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, '$$', ?, 0, 0, 0, 0, ?, ?, ?, ?)""",
            (
                draft_id, restaurant_id, cuisine_type, zip_code,
                competitor_count, len(items),
                json.dumps({}),
                _COMPARE_MODEL, now, now,
            ),
        )
        conn.commit()


def get_price_intelligence(draft_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve stored price intelligence results for a draft."""
    with _db_connect() as conn:
        summary = conn.execute(
            "SELECT * FROM price_intelligence_summary WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()

        if not summary:
            return None

        items = conn.execute(
            """SELECT item_id, item_name, item_category, current_price,
                      assessment, suggested_low, suggested_high, regional_avg,
                      median_price, reasoning, confidence, comparison_count,
                      price_sources
               FROM price_intelligence_results
               WHERE draft_id = ?
               ORDER BY id""",
            (draft_id,),
        ).fetchall()

    summary_d = dict(summary)
    category_avgs = {}
    if summary_d.get("category_avgs"):
        try:
            category_avgs = json.loads(summary_d["category_avgs"])
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "assessments": [dict(r) for r in items],
        "category_avgs": category_avgs,
        "market_context": {
            "market_tier": summary_d.get("avg_market_tier", "$$"),
        },
        "model": summary_d.get("model_used"),
        "total_items": summary_d.get("total_items", 0),
        "items_assessed": summary_d.get("items_assessed", 0),
        "underpriced": summary_d.get("underpriced", 0),
        "fair_priced": summary_d.get("fair_priced", 0),
        "overpriced": summary_d.get("overpriced", 0),
        "competitor_count": summary_d.get("competitor_count", 0),
        "created_at": summary_d.get("created_at"),
        "skipped": False,
    }


def get_item_assessment(draft_id: int, item_id: int) -> Optional[Dict[str, Any]]:
    """Get price assessment for a single item."""
    with _db_connect() as conn:
        row = conn.execute(
            """SELECT item_name, item_category, current_price, assessment,
                      suggested_low, suggested_high, regional_avg,
                      median_price, reasoning, confidence, comparison_count,
                      price_sources
               FROM price_intelligence_results
               WHERE draft_id = ? AND item_id = ?""",
            (draft_id, item_id),
        ).fetchone()
    return dict(row) if row else None


def clear_price_intelligence(draft_id: int) -> int:
    """Delete all price intelligence data for a draft. Returns rows deleted."""
    with _db_connect() as conn:
        r1 = conn.execute(
            "DELETE FROM price_intelligence_results WHERE draft_id = ?",
            (draft_id,),
        ).rowcount
        conn.execute(
            "DELETE FROM price_intelligence_summary WHERE draft_id = ?",
            (draft_id,),
        )
        conn.commit()
    return r1


# ---------------------------------------------------------------------------
# Competitor side-by-side comparison (Day 141.5)
# ---------------------------------------------------------------------------

_COMPARE_MODEL = "claude-opus-4-6"
# Match the extraction pattern in ai_menu_extract.py:
# max_tokens = total thinking + response. Budget caps thinking so the
# JSON response isn't starved. 32k - 10k = 22k headroom for output
# (the extraction baseline uses this exact split and has never run dry).
_COMPARE_MAX_TOKENS = 32_000
_COMPARE_THINKING_BUDGET = 10_000


def _ensure_comparison_schema() -> None:
    """Create the competitor_comparisons cache table."""
    with _db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_comparisons (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id        INTEGER NOT NULL,
                restaurant_id   INTEGER NOT NULL,
                competitor_name TEXT NOT NULL,
                competitor_data TEXT NOT NULL,
                comparisons     TEXT NOT NULL,
                model_used      TEXT,
                created_at      TEXT NOT NULL,
                UNIQUE(draft_id, competitor_name)
            )
        """)
        conn.commit()


_ensure_comparison_schema()


def _build_comparison_prompt(
    our_items: List[Dict[str, Any]],
    competitor: Dict[str, Any],
    cuisine_type: str,
    zip_code: str,
) -> str:
    """Build a prompt asking Claude to estimate competitor prices for our items."""
    items_block = []
    for item in our_items:
        price_str = f"${item['price_cents'] / 100:.2f}" if item.get("price_cents") else "no price"
        cat = item.get("category") or "Uncategorized"
        sub = item.get("subcategory") or ""
        cat_label = f"{cat} > {sub}" if sub else cat
        items_block.append(f"- {item['name']} | {cat_label} | {price_str}")
    items_text = "\n".join(items_block)

    comp_name = competitor.get("place_name", "Unknown")
    comp_rating = competitor.get("rating", "N/A")
    comp_price = competitor.get("price_label", "N/A")
    comp_addr = competitor.get("place_address", "")
    comp_reviews = competitor.get("user_ratings", 0)

    return f"""\
You are a restaurant pricing analyst doing a direct price comparison.

OUR RESTAURANT: A {cuisine_type} restaurant in zip code {zip_code}.

COMPETITOR: {comp_name}
- Address: {comp_addr}
- Rating: {comp_rating} ({comp_reviews} reviews)
- Price tier: {comp_price}

OUR MENU ITEMS:
{items_text}

For each of our menu items, estimate what a SIMILAR item would cost at {comp_name},
based on their price tier ({comp_price}), location, and typical pricing for restaurants
at that level. Also suggest what the closest matching menu item name would be at that restaurant.

Return a JSON object:
{{
  "competitor_name": "{comp_name}",
  "competitor_tier": "{comp_price}",
  "comparisons": [
    {{
      "our_item": "exact item name from our list",
      "our_price_cents": integer (our current price in cents, 0 if none),
      "their_estimated_cents": integer (estimated price at competitor in cents),
      "their_item_name": "what this item might be called there",
      "difference_cents": integer (their price - our price, positive = theirs is more),
      "verdict": "cheaper|similar|pricier" (how OUR price compares — cheaper means we charge less)
    }}
  ]
}}

RULES:
- Return ONLY valid JSON, no markdown fencing.
- Every item from our list must appear in comparisons.
- All prices in cents (e.g., $12.99 = 1299).
- Items with no price on our side: set our_price_cents to 0, still estimate theirs.
- "similar" means within 15% of each other.
- Be realistic: a $ restaurant will price lower than a $$$$ one.
- Only compare like-for-like: a main dish vs a main dish, an appetizer vs an appetizer.
  Never compare a topping/add-on against a full dish.
- Pay close attention to the category shown after the | pipe. Items in categories like
  "Pizza", "Wraps", "Burgers" are full entrees. Items in subcategories like "Toppings"
  should be compared against toppings at comparable restaurants, not standalone dishes.
- Base your estimates on what a {comp_price} restaurant in this area would actually charge."""


# Menu categories that mean the same thing across restaurants. The
# canonical (first) name is what the pre-filter uses for overlap checks.
_CATEGORY_SYNONYMS = {
    # Subs / Grinders / Sandwiches / Clubs / Melts — all comparable handhelds.
    # A BLT Club and a BLT Grinder serve the same role on a menu.
    "subs": "sandwiches",
    "sub": "sandwiches",
    "grinders": "sandwiches",
    "grinder": "sandwiches",
    "heroes": "sandwiches",
    "hero": "sandwiches",
    "hoagies": "sandwiches",
    "hoagie": "sandwiches",
    "cold subs": "sandwiches",
    "hot subs": "sandwiches",
    "cold grinders": "sandwiches",
    "hot grinders": "sandwiches",
    "parmigiana grinders": "sandwiches",
    '12" grinders': "sandwiches",
    "sandwiches": "sandwiches",
    "sandwich": "sandwiches",
    "club sandwiches": "sandwiches",
    "melt sandwiches": "sandwiches",
    "melts": "sandwiches",
    # Appetizers
    "appetizers": "appetizers",
    "appetizer": "appetizers",
    "starters": "appetizers",
    "small bites": "appetizers",
    "apps": "appetizers",
    # Entrees
    "entrees": "entrees",
    "entrées": "entrees",
    "mains": "entrees",
    "main dishes": "entrees",
    "dinner entrees": "entrees",
    "from the grill": "entrees",
    "italian dishes": "entrees",
    "italian specialties": "entrees",
    # Sides
    "sides": "sides",
    "side orders": "sides",
    "side items": "sides",
    "side dishes": "sides",
    "fries": "sides",
    # Desserts
    "desserts": "desserts",
    "dessert": "desserts",
    "sweets": "desserts",
    # Drinks
    "beverages": "drinks",
    "drinks": "drinks",
    # Wraps
    "wraps": "wraps",
    "wrap": "wraps",
    # Wings
    "wings": "wings",
    "chicken wings": "wings",
    "buffalo wings": "wings",
    # Pizza
    "pizza": "pizza",
    "pizzas": "pizza",
    "gourmet pizza": "pizza",
    "specialty pizza": "pizza",
    "specialty pizzas": "pizza",
    # Calzones
    "calzones": "calzones",
    "calzone": "calzones",
    # Burgers
    "burgers": "burgers",
    "burger": "burgers",
    "hamburgers": "burgers",
    "6 oz angus burgers": "burgers",
    # Salads
    "salads": "salads",
    "salad": "salads",
    # Pasta
    "pasta": "pasta",
    "pasta dishes": "pasta",
    # Seafood
    "seafood": "seafood",
    "fresh seafood": "seafood",
    # Soups
    "soups": "soups",
    "soup": "soups",
    # Nachos
    "nachos": "appetizers",
    # Dinner (generic)
    "dinner": "entrees",
    "lunch": "entrees",
    # Flatbreads (close enough to pizza for comparison)
    "flatbreads": "pizza",
    "flatbread": "pizza",
    # Small plates
    "plates": "appetizers",
    # Catch tokens that appear inside compound names
    "appetizer": "appetizers",
    "grinder": "sandwiches",
    "sub": "sandwiches",
}


def _normalize_menu_category(cat: str) -> str:
    """Map a menu category to its canonical name using token-based matching.

    Handles the infinite variety of real restaurant category names:
    '12" Grinders' → sandwiches (token 'grinders' hits the map)
    'Appetizers & Sides' → appetizers (token 'appetizers' hits)
    'Dinner Menu' → entrees (token 'dinner' hits)
    'Specialty Pizza' → pizza (token 'pizza' hits)
    """
    key = cat.strip().lower()
    # Try exact match first (fastest)
    if key in _CATEGORY_SYNONYMS:
        return _CATEGORY_SYNONYMS[key]
    # Token-based: split on spaces and punctuation, check each word
    import re
    tokens = re.split(r'[\s&/,\-\'"]+', key)
    for tok in tokens:
        tok = tok.strip()
        if tok and tok in _CATEGORY_SYNONYMS:
            return _CATEGORY_SYNONYMS[tok]
    return key


def _build_real_comparison_prompt(
    our_items: List[Dict[str, Any]],
    their_items: List[Dict[str, Any]],
    comp_name: str,
    competitor: Dict[str, Any],
) -> str:
    """Build the Opus+thinking prompt for per-competitor menu comparison.

    Day 141.7: moved from Sonnet with a hand-rolled per-category pre-filter
    (brittle — kept cross-matching cookies to pizzas) to Opus with extended
    thinking. Opus reasons about category, cuisine, item type, and name
    similarity rather than pattern-matching. No pre-filter necessary; we
    give Opus the full competitor menu and let it decide what matches.
    """
    def _fmt_item(it: Dict[str, Any]) -> str:
        price = f"${it['price_cents'] / 100:.2f}" if it.get("price_cents") else "no price"
        cat = (it.get("category") or "Other").strip()
        sub = (it.get("subcategory") or "").strip()
        label = f"{cat} > {sub}" if sub else cat
        desc = (it.get("description") or "").strip()
        desc_part = f" — {desc[:80]}" if desc else ""
        return f"- [{label}] {it['name']} | {price}{desc_part}"

    our_block = "\n".join(_fmt_item(it) for it in our_items) or "(none)"
    their_block = "\n".join(_fmt_item(it) for it in their_items) or "(none)"

    comp_price = competitor.get("price_label", "N/A")
    comp_rating = competitor.get("rating", "N/A")

    return f"""\
You are a restaurant pricing analyst comparing our menu to a real
competitor's menu. Your output drives a side-by-side UI the restaurant
owner uses to price-check their items, so the match quality matters
more than the count — a wrong match is worse than many honest "no_match".

OUR MENU ({len(our_items)} items):
{our_block}

{comp_name.upper()} — {comp_price} · ★{comp_rating} — ({len(their_items)} items):
{their_block}

For each of OUR items, find the best-matching item on {comp_name}'s menu
— OR return "no_match" if nothing on their menu is a real peer.

Think through matches like a human would:

1. **Item type first.** A pizza competes with another pizza. A calzone
   competes with another calzone. A burger with a burger, a grinder/sub/
   hoagie with the same, a side (fries/rings) with a side, a salad with
   a salad, an appetizer with an appetizer. NEVER match across types —
   pizza ↔ salad, pizza ↔ cookie, calzone ↔ pasta are all wrong.

2. **Flavor/ingredient family second.** Within the same type, pick the
   closest flavor profile:
   - Our "Buffalo Chicken Pizza" → their "Buffalo Chicken Pizza" (exact),
     or "Spicy Chicken Pizza" (close), or any chicken pizza (approximate)
   - Our "Cheese Pizza" → their "Cheese Pizza" or "Plain Cheese" (exact/
     close), NOT "4 Cheese" if a simpler match exists
   - Our "Meat Lovers Pizza" → their "Meat Lovers" / "All Meats" / "Carnivore"
   - Our "Combination Pizza" → their "House Special" / "Supreme" / "The Works"

3. **Category-name synonyms to treat as equivalent when assessing type:**
   - Subs = Grinders = Heroes = Hoagies = Heros
   - Sandwiches = Melts = Club Sandwiches
   - Entrees = Mains = Dinner Entrees = Dinner Specials
   - Appetizers = Starters = Small Plates = Small Bites
   - Flatbreads ≈ Pizzas (if no real pizza section exists)
   - Soda / Beverages / Drinks = Drinks

4. **Return "no_match" freely.** If their menu has NO item of the same
   type (e.g. they're an Italian restaurant with no pizzas, or a sub shop
   with no burgers), every one of our items in that missing type should
   be "no_match". Owners understand "no comparable item" — they don't
   understand pizza-to-cookie comparisons.

5. **One-to-one.** Don't reuse the same competitor item for multiple of
   our items unless absolutely nothing else would fit — prefer no_match
   over reusing.

6. **Use their REAL prices from the menu above. Do NOT estimate.** If
   the listed price is 0 or "no price", the item isn't really orderable
   (it's a modifier or category header) — treat as a bad match.

Return JSON only, no markdown fencing:

{{
  "comparisons": [
    {{
      "our_item": "exact item name from our list",
      "our_price_cents": <int>,
      "their_item_name": "exact item name from their menu, or empty string",
      "their_estimated_cents": <int, their real price in cents; 0 if no_match>,
      "difference_cents": <int, their - ours; 0 if no_match>,
      "verdict": "cheaper|similar|pricier|no_match",
      "match_quality": "exact|close|approximate|no_match",
      "reasoning": "one short sentence on why you picked this match (or no_match)"
    }}
  ]
}}

- Every one of OUR items must appear exactly once.
- "similar" = within 15% either way. "cheaper" = our price lower. "pricier" = our price higher.
- `difference_cents` is (their - ours), signed."""


def compare_with_competitor(
    draft_id: int,
    restaurant_id: int,
    competitor: Dict[str, Any],
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Compare our menu prices against a competitor. Tries real scraped data first,
    falls back to Claude estimates if their menu isn't publicly available.
    Results are cached per (draft_id, competitor_name).
    """
    comp_name = competitor.get("place_name", "Unknown")
    place_id = competitor.get("place_id") or ""

    # Check cache first
    if not force_refresh:
        cached = get_competitor_comparison(draft_id, comp_name)
        if cached:
            return cached

    # Get our items
    from storage.drafts import get_draft_items
    from storage.users import get_restaurant

    items = get_draft_items(draft_id, include_modifier_groups=False) or []
    if not items:
        return {"error": "No items in draft", "comparisons": []}

    rest = get_restaurant(restaurant_id)
    cuisine_type = (rest.get("cuisine_type") or "restaurant") if rest else "restaurant"
    zip_code = (rest.get("zip_code") or "") if rest else ""

    # Filter to main menu items — exclude toppings, modifiers, add-ons
    MODIFIER_SUBCATS = {"toppings", "sauce options", "wing sauces", "bread options",
                        "add-ons", "extras", "sides", "dressings", "preparation"}
    priced_items = []
    for it in items:
        if not it.get("price_cents") or it["price_cents"] <= 0:
            continue
        sub = (it.get("subcategory") or "").strip().lower()
        # Skip items in modifier subcategories
        if sub and sub in MODIFIER_SUBCATS:
            continue
        # Skip very cheap items that are clearly toppings/modifiers (< $3 with a subcategory)
        if sub and it["price_cents"] < 300:
            continue
        priced_items.append(it)
    if not priced_items:
        return {"error": "No main menu items to compare", "comparisons": []}
    if len(priced_items) > 50:
        priced_items = priced_items[:50]

    # --- Step 1: Try to get REAL menu data ---
    real_menu = None
    data_source = "estimated"
    if place_id:
        try:
            from storage.price_intel import scrape_competitor_menu
            real_menu = scrape_competitor_menu(place_id, comp_name)
            if real_menu and real_menu.get("items"):
                data_source = "scraped"
                log.info("Got %d real menu items for %s", len(real_menu["items"]), comp_name)
        except Exception as e:
            log.warning("Menu scraping failed for %s: %s", comp_name, e)

    # --- Step 2: Build comparison ---
    client = _get_client()
    if not client:
        return {"error": "Anthropic client not available", "comparisons": []}

    if data_source == "scraped" and real_menu and real_menu.get("items"):
        # Day 141.7: strip modifiers + collapse size variants from the
        # competitor menu before sending to Claude. Sauce ramekins and
        # size variants only add noise to the comparison.
        their_items = real_menu["items"]
        try:
            from storage.menu_classifier import filter_comparison_items
            their_items = filter_comparison_items(their_items)
        except Exception:
            pass
        # Real data path: ask Claude to match our items against their actual menu
        prompt = _build_real_comparison_prompt(
            priced_items, their_items, comp_name, competitor
        )
        use_thinking = True
    else:
        # Estimate path: ask Claude to estimate prices (still Sonnet — lighter task)
        prompt = _build_comparison_prompt(priced_items, competitor, cuisine_type, zip_code)
        use_thinking = False

    comparisons = []
    try:
        if use_thinking:
            # Opus + extended thinking for real menu matching. Streams
            # because thinking runs can exceed the non-streaming timeout.
            api_kwargs = {
                "model": _COMPARE_MODEL,
                "max_tokens": _COMPARE_MAX_TOKENS,
                "temperature": 1,  # required for extended thinking
                "thinking": {"type": "enabled", "budget_tokens": _COMPARE_THINKING_BUDGET},
                "messages": [{"role": "user", "content": prompt}],
            }
            with client.messages.stream(**api_kwargs) as stream:
                message = stream.get_final_message()
            raw = ""
            for block in message.content:
                if getattr(block, "type", None) == "text" or hasattr(block, "text"):
                    raw += getattr(block, "text", "") or ""
            raw = raw.strip()
        else:
            # Estimates path — Sonnet, non-streaming
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=6000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        comparisons = data.get("comparisons", [])
    except Exception as e:
        log.error("Competitor comparison failed for %s: %s", comp_name, e)
        return {"error": str(e), "comparisons": []}
    menu_url = real_menu.get("menu_url") if real_menu else None

    # Cache the result (include data_source in competitor_data)
    cache_data = dict(competitor)
    cache_data["_data_source"] = data_source
    cache_data["_menu_url"] = menu_url
    cache_data["_their_item_count"] = len(real_menu["items"]) if real_menu and real_menu.get("items") else 0
    try:
        with _db_connect() as conn:
            conn.execute(
                """INSERT INTO competitor_comparisons
                   (draft_id, restaurant_id, competitor_name, competitor_data,
                    comparisons, model_used, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(draft_id, competitor_name)
                   DO UPDATE SET
                       competitor_data = excluded.competitor_data,
                       comparisons = excluded.comparisons,
                       model_used = excluded.model_used,
                       created_at = excluded.created_at""",
                (
                    draft_id, restaurant_id, comp_name,
                    json.dumps(cache_data),
                    json.dumps(comparisons),
                    _COMPARE_MODEL,
                    _now(),
                ),
            )
            conn.commit()
    except Exception as e:
        log.warning("Failed to cache comparison: %s", e)

    return {
        "competitor_name": comp_name,
        "competitor_tier": competitor.get("price_label", "N/A"),
        "competitor_rating": competitor.get("rating"),
        "comparisons": comparisons,
        "data_source": data_source,
        "menu_url": menu_url,
        "from_cache": False,
    }


def get_competitor_comparison(
    draft_id: int,
    competitor_name: str,
) -> Optional[Dict[str, Any]]:
    """Retrieve cached competitor comparison."""
    try:
        with _db_connect() as conn:
            row = conn.execute(
                """SELECT competitor_name, competitor_data, comparisons, model_used, created_at
                   FROM competitor_comparisons
                   WHERE draft_id = ? AND competitor_name = ?""",
                (draft_id, competitor_name),
            ).fetchone()
        if not row:
            return None
        comp_data = json.loads(row["competitor_data"])
        comparisons = json.loads(row["comparisons"])
        return {
            "competitor_name": row["competitor_name"],
            "competitor_tier": comp_data.get("price_label", "N/A"),
            "competitor_rating": comp_data.get("rating"),
            "comparisons": comparisons,
            "data_source": comp_data.get("_data_source", "estimated"),
            "menu_url": comp_data.get("_menu_url"),
            "from_cache": True,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Cache-based comparison (no Claude call — instant, free)
# ---------------------------------------------------------------------------

def get_cached_competitor_menu_comparison(
    draft_id: int,
    restaurant_id: int,
    competitor_name: str,
    competitor: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Build a side-by-side comparison from cached competitor menu data.
    No Claude call — uses Python-based item matching. Instant and free.
    Returns None if no cached menu exists for this competitor.
    """
    from difflib import SequenceMatcher
    from storage.drafts import get_draft_items
    from storage.price_intel import _get_cached_menu

    place_id = competitor.get("place_id")
    if not place_id:
        return None

    # Get cached competitor menu
    cached = _get_cached_menu(place_id)
    if not cached or not cached.get("items"):
        return None

    raw_items = cached["items"]
    # Day 141.7: strip modifiers + collapse variants before comparison
    try:
        from storage.menu_classifier import filter_comparison_items
        their_items = filter_comparison_items(raw_items)
    except Exception:
        their_items = raw_items
    # Filter to items with real prices
    their_items = [it for it in their_items if it.get("price_cents") and it["price_cents"] > 0]
    if not their_items:
        return None

    # Get our items
    our_items = get_draft_items(draft_id, include_modifier_groups=False) or []
    # Filter to main menu items (same filter as compare_with_competitor)
    MODIFIER_SUBCATS = {"toppings", "sauce options", "wing sauces", "bread options",
                        "add-ons", "extras", "sides", "dressings", "preparation"}
    our_priced = []
    for it in our_items:
        if not it.get("price_cents") or it["price_cents"] <= 0:
            continue
        sub = (it.get("subcategory") or "").strip().lower()
        if sub and sub in MODIFIER_SUBCATS:
            continue
        if sub and it["price_cents"] < 300:
            continue
        our_priced.append(it)

    if not our_priced:
        return None

    # Match items by category + name similarity
    comparisons = _match_items(our_priced, their_items)

    # Count verdicts
    cheaper = sum(1 for c in comparisons if c["verdict"] == "cheaper")
    similar = sum(1 for c in comparisons if c["verdict"] == "similar")
    pricier = sum(1 for c in comparisons if c["verdict"] == "pricier")

    return {
        "competitor_name": competitor_name,
        "competitor_tier": competitor.get("price_label", "N/A"),
        "competitor_rating": competitor.get("rating"),
        "comparisons": comparisons,
        "data_source": "scraped",
        "menu_url": cached.get("menu_url"),
        "from_cache": True,
    }


def _match_items(
    our_items: List[Dict[str, Any]],
    their_items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Match our menu items against theirs using category + name similarity."""
    from difflib import SequenceMatcher

    used_theirs: set = set()
    comparisons = []

    for our in our_items:
        our_name = (our.get("name") or "").lower().strip()
        our_cat = _normalize_category(our.get("category") or "")
        our_price = our.get("price_cents", 0)

        best_match = None
        best_score = 0.0
        best_idx = -1

        for i, their in enumerate(their_items):
            if i in used_theirs:
                continue
            their_name = (their.get("name") or "").lower().strip()
            their_cat = _normalize_category(their.get("category") or "")

            # Category bonus: matching categories get a boost
            cat_match = (our_cat == their_cat) if our_cat and their_cat else False

            # Name similarity
            score = SequenceMatcher(None, our_name, their_name).ratio()
            if cat_match:
                score += 0.2  # boost for same category

            if score > best_score:
                best_score = score
                best_match = their
                best_idx = i

        # Threshold: need at least 0.45 similarity to match
        if best_match and best_score >= 0.45 and best_idx >= 0:
            used_theirs.add(best_idx)
            their_price = best_match.get("price_cents", 0)
            diff = their_price - our_price
            # Verdict
            if their_price == 0 or our_price == 0:
                verdict = "no_match"
            elif abs(diff) < our_price * 0.15:
                verdict = "similar"
            elif diff > 0:
                verdict = "cheaper"  # we're cheaper
            else:
                verdict = "pricier"  # we're pricier

            quality = "exact" if best_score > 0.85 else "close" if best_score > 0.6 else "approximate"

            comparisons.append({
                "our_item": our.get("name", ""),
                "our_price_cents": our_price,
                "their_estimated_cents": their_price,
                "their_item_name": best_match.get("name", ""),
                "difference_cents": diff,
                "verdict": verdict,
                "match_quality": quality,
            })
        else:
            comparisons.append({
                "our_item": our.get("name", ""),
                "our_price_cents": our_price,
                "their_estimated_cents": 0,
                "their_item_name": "",
                "difference_cents": 0,
                "verdict": "no_match",
                "match_quality": "no_match",
            })

    return comparisons
