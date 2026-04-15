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

    results = []
    fresh_count = 0

    for comp in sorted_comps[:max_competitors]:
        place_id = comp.get("place_id")
        place_name = comp.get("place_name", "Unknown")
        if not place_id:
            continue

        # Fetch menu (cache or fresh)
        menu_data = None
        try:
            menu_data = scrape_competitor_menu(
                place_id, place_name,
                force_refresh=False,
            )
        except Exception as e:
            log.warning("Failed to fetch menu for %s: %s", place_name, e)

        if menu_data and menu_data.get("source") == "not_found" and not menu_data.get("from_cache"):
            fresh_count += 1
        elif menu_data and not menu_data.get("from_cache"):
            fresh_count += 1

        if fresh_count >= max_fresh_searches and not (menu_data and menu_data.get("from_cache")):
            log.info("Hit max fresh searches (%d), skipping remaining", max_fresh_searches)
            break

        their_items = menu_data.get("items", []) if menu_data else []
        their_cats = {it.get("category", "Other") for it in their_items}
        overlap = _categories_overlap(our_cats, their_cats)

        # Keep competitors even without exact category overlap —
        # Claude will match individual items. Only skip if debugging shows
        # a need to filter (e.g., completely different cuisine).
        if their_items and not overlap:
            log.info("No exact category overlap for %s, but keeping (items may still match)", place_name)

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
        "Fetched %d competitor menus (%d with items, %d fresh searches)",
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

    # Get competitor data from Google Places (cached from Day 134)
    competitor_data = get_cached_comparisons(restaurant_id)
    market_summary = get_market_summary(restaurant_id)

    # Day 141.7: Fetch real competitor menus via Apify (premium tier only)
    # Uses owner's tier; if multiple users, premium wins.
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

    # Batch items by category to avoid token overflow on large menus
    batches = _make_batches(items)
    log.info(
        "Price intel: %d items → %d batch(es) for draft %d (%d competitor menus with data)",
        len(items), len(batches), draft_id,
        sum(1 for m in competitor_menus if m.get("items")),
    )

    batch_results: List[Dict[str, Any]] = []
    for i, batch in enumerate(batches):
        log.info("Price intel batch %d/%d: %d items", i + 1, len(batches), len(batch))
        prompt = _build_prompt(
            batch, cuisine_type, zip_code, competitor_data, market_summary,
            competitor_menus=competitor_menus,
        )
        raw = _call_claude(prompt, model=model)
        if raw:
            batch_results.append(raw)
        else:
            log.warning("Price intel batch %d/%d failed, skipping", i + 1, len(batches))

    if not batch_results:
        return {
            "error": "All Claude API batches failed",
            "skipped": True,
            "assessments": [],
            "total_items": len(items),
        }

    # Merge batches and validate
    merged = _merge_batch_results(batch_results)
    validated = _validate_results(merged, items)

    # Save to DB
    _save_results(
        draft_id=draft_id,
        restaurant_id=restaurant_id,
        validated=validated,
        cuisine_type=cuisine_type,
        zip_code=zip_code,
        competitor_count=market_summary.get("competitor_count", 0),
        model=merged.get("_model", model),
    )

    return {
        "assessments": validated["assessments"],
        "category_avgs": validated["category_avgs"],
        "market_context": validated["market_context"],
        "model": merged.get("_model", model),
        "total_items": len(items),
        "items_assessed": sum(
            1 for a in validated["assessments"] if a["assessment"] != "unknown"
        ),
        "skipped": False,
        "from_cache": False,
    }


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
                      reasoning, confidence
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
                      reasoning, confidence
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

_COMPARE_MODEL = "claude-sonnet-4-5"
_COMPARE_MAX_TOKENS = 6_000


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


def _build_real_comparison_prompt(
    our_items: List[Dict[str, Any]],
    their_items: List[Dict[str, Any]],
    comp_name: str,
    competitor: Dict[str, Any],
) -> str:
    """Build a prompt for matching our items against their REAL scraped menu."""
    our_block = []
    for item in our_items:
        price_str = f"${item['price_cents'] / 100:.2f}" if item.get("price_cents") else "no price"
        cat = item.get("category") or "Uncategorized"
        sub = item.get("subcategory") or ""
        cat_label = f"{cat} > {sub}" if sub else cat
        our_block.append(f"- {item['name']} | {cat_label} | {price_str}")
    our_text = "\n".join(our_block)

    their_block = []
    for item in their_items:
        price_str = f"${item['price_cents'] / 100:.2f}" if item.get("price_cents") else "no price"
        cat = item.get("category") or "Other"
        their_block.append(f"- {item['name']} | {cat} | {price_str}")
    their_text = "\n".join(their_block)

    comp_price = competitor.get("price_label", "N/A")
    comp_rating = competitor.get("rating", "N/A")

    return f"""\
You are a restaurant pricing analyst. Compare two real restaurant menus side by side.

OUR MENU ITEMS:
{our_text}

{comp_name.upper()}'S ACTUAL MENU ({comp_price}, ★{comp_rating}):
{their_text}

For each of OUR items, find the closest matching item on {comp_name}'s menu.
Use their REAL price — do not estimate.

Return a JSON object:
{{
  "comparisons": [
    {{
      "our_item": "exact item name from our list",
      "our_price_cents": integer (our price in cents),
      "their_estimated_cents": integer (their REAL price in cents from their menu),
      "their_item_name": "the actual matching item name from their menu",
      "difference_cents": integer (their price - our price),
      "verdict": "cheaper|similar|pricier",
      "match_quality": "exact|close|approximate|no_match"
    }}
  ]
}}

RULES:
- Return ONLY valid JSON, no markdown fencing.
- Every one of our items must appear in comparisons.
- Use REAL prices from their menu — this is not an estimate.
- "match_quality" indicates how close the match is:
  - "exact": same item name (e.g., Tuna Wrap → Tuna Wrap)
  - "close": very similar item (e.g., Grilled Chicken Wrap → Chicken Wrap)
  - "no_match": no comparable item on their menu
- IMPORTANT: prefer "no_match" over bad matches. Do NOT match different items just
  because they're in the same category. "Buffalo Chicken Wrap" should only match
  "Buffalo Chicken Wrap" or "Spicy Chicken Wrap" — NOT a generic "Gyro Wrap."
  Each of their items can only be used as a match ONCE — don't reuse the same
  competitor item for multiple of our items.
- "similar" verdict means prices within 15% of each other.
- "cheaper" means OUR price is lower. "pricier" means OUR price is higher.
- If no_match, set verdict to "no_match" and difference_cents to 0.
- Only compare like-for-like: wraps vs wraps, burgers vs burgers, pizza vs pizza.
  Never match a topping/modifier against a full dish or side."""


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
        # Real data path: ask Claude to match our items against their actual menu
        prompt = _build_real_comparison_prompt(
            priced_items, real_menu["items"], comp_name, competitor
        )
    else:
        # Estimate path: ask Claude to estimate prices
        prompt = _build_comparison_prompt(priced_items, competitor, cuisine_type, zip_code)

    try:
        response = client.messages.create(
            model=_COMPARE_MODEL,
            max_tokens=_COMPARE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
    except Exception as e:
        log.error("Competitor comparison failed: %s", e)
        return {"error": str(e), "comparisons": []}

    comparisons = data.get("comparisons", [])
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

    their_items = cached["items"]
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
