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
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Reuse shared Anthropic client
from .ai_menu_extract import _get_client


# Gemini's search-grounded pricing sometimes returns list-article headlines
# ("Best Pizza in Cape May Court House | My Pizza Heaven") instead of real
# restaurant names. Those get matched by Google Places to the first pizzeria
# it can find — typically hundreds of miles from the user's actual location.
# Filter aggressively at ingest so pill citations + the editor map stay clean.
_ARTICLE_MARKERS = (
    " | ", " · ", "...",
    " near me", " near you", " near here",
    "best pizza in ", "best pizzas in ", "best pizza of ",
    "best restaurants in ", "best restaurants of ",
    "top restaurants in ", "top pizzas in ",
    "places to eat in ", "where to eat in ",
)
_ARTICLE_PREFIXES = (
    "best ", "top ", "the best ", "the top ",
    "10 ", "5 ", "7 ", "12 ", "15 ", "20 ", "25 ",
)


def _coerce_item_id(raw):
    """Convert an `id` field from a Gemini/Haiku response into an int.

    Gemini sometimes echoes the literal `#NNN` token from our prompt back
    in its JSON (we format items as `- #19283 "Cheese Pizza" ...`). Plain
    `int(raw)` then raises ValueError, which the broad `except Exception`
    in the parser catches — silently dropping the entire batch's worth of
    successfully-fetched data. Strip the prefix here so we don't lose any
    item Gemini actually returned.

    Returns None if `raw` cannot be converted (caller should skip that row).
    """
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip().lstrip("#").strip()
        if s.isdigit():
            return int(s)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_article_title(name: str) -> bool:
    """Return True when `name` is probably a Google Search article title
    or list header, not a real restaurant. Conservative — we'd rather let
    some junk through than drop a legitimately odd restaurant name."""
    if not name:
        return True
    n = name.strip()
    if len(n) > 80:
        return True
    low = n.lower()
    if any(m in low for m in _ARTICLE_MARKERS):
        return True
    if any(low.startswith(p) for p in _ARTICLE_PREFIXES):
        return True
    return False

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


def _ensure_gemini_log_schema() -> None:
    """Persistent log of every Gemini API call we make. Used to track
    success/failure rates over time, identify outage windows, and confirm
    whether `pro` continues to be reliable in production. Append-only —
    we never modify rows after insert."""
    with _db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gemini_call_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                model         TEXT NOT NULL,
                outcome       TEXT NOT NULL,   -- 'ok' | 'error' | 'empty_body'
                error_type    TEXT,
                error_status  INTEGER,
                error_message TEXT,
                batch_size    INTEGER,
                duration_s    REAL,
                draft_id      INTEGER
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gemini_log_ts "
            "ON gemini_call_log(ts)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gemini_log_model "
            "ON gemini_call_log(model)"
        )
        conn.commit()


def _log_gemini_call(*, model: str, outcome: str,
                      error_type: Optional[str] = None,
                      error_status: Optional[int] = None,
                      error_message: Optional[str] = None,
                      batch_size: int = 0, duration_s: float = 0.0,
                      draft_id: Optional[int] = None) -> None:
    """Append a single row to gemini_call_log. Swallows DB errors so a
    log failure can never block a real Gemini call from completing."""
    try:
        with _db_connect() as conn:
            conn.execute(
                """INSERT INTO gemini_call_log
                   (ts, model, outcome, error_type, error_status, error_message,
                    batch_size, duration_s, draft_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _now(), model, outcome,
                    error_type, error_status,
                    (error_message or "")[:500] if error_message else None,
                    batch_size, round(duration_s, 2), draft_id,
                ),
            )
            conn.commit()
    except Exception as e:
        log.warning("Failed to log Gemini call: %s", e)


def _extract_status_from_error(exc: Exception) -> Optional[int]:
    """Best-effort HTTP status code extraction from a Gemini SDK exception.
    Errors typically format as 'ServerError: 503 UNAVAILABLE. {...}'."""
    s = str(exc)
    for code in (503, 500, 502, 504, 429, 400, 401, 403, 404, 408):
        if str(code) in s:
            return code
    return None


# Run on import
_ensure_schema()
_ensure_gemini_log_schema()


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


# Module-level metrics for Gemini calls — reset at the start of every
# _gemini_search_prices invocation. Lets the caller (and humans reading
# logs) see exactly what happened: how many batches, how many succeeded,
# what errors fired. Helps with the "Gemini IS the product, it must be
# rock solid" requirement — silent failures are not acceptable.
_GEMINI_LAST_RUN: Dict[str, Any] = {
    "batches_total": 0,
    "batches_ok": 0,
    "batches_failed": 0,
    "items_requested": 0,
    "items_returned": 0,
    "model_usage": {},   # {model_name: ok_batches}
    "errors": [],        # list of {attempt, error_type, status, message}
    "duration_s": 0.0,
}

# Single grounded-pricing model. Earlier iterations had a multi-model
# fallback chain (flash → flash-lite → pro) with probe, race, and switch
# logic, but observation across multiple production runs in Apr 2026
# showed:
#   - flash-lite: ~50% empty bodies on grounded prompts, sometimes
#                 garbage when it does answer. Dropped first.
#   - flash:      regularly 503s during Google capacity events. When it
#                 works it's fast, but reliability is too inconsistent
#                 to keep as primary.
#   - pro:        slower (~60-120s per 20-item batch) but consistently
#                 returns clean, well-cited results. Zero observed 503s
#                 in our production usage (caveat: forum reports show
#                 pro CAN have outages, but rare).
# Pro-only keeps the code simple, the data clean, and the cost still
# under $0.50 per typical menu — well within margin on the $80/mo plan.
# If pro itself goes down, we Haiku-fallback + show a banner.
_GEMINI_MODEL = "gemini-2.5-pro"

def _is_retryable_gemini_error(exc: Exception) -> bool:
    """Return True if this Gemini error is worth retrying. Covers the full
    set of transient failures Google calls out: 5xx server errors, 429
    rate limit, deadline exceeded, network blips."""
    s = str(exc)
    return any(marker in s for marker in (
        "503", "500", "502", "504", "429",
        "UNAVAILABLE", "RESOURCE_EXHAUSTED",
        "DEADLINE_EXCEEDED", "INTERNAL",
        "ServerError", "ServiceUnavailable",
    ))


# Hardcoded REJECT list for the `restaurant` field in cited sources.
# These should NEVER appear as the restaurant name on a citeable cite —
# the prompt forbids them but rules can be soft-followed when Gemini
# encounters edge cases (e.g., a small business whose only online menu
# is on Toast). The deterministic backstop kicks in regardless. Lower-
# cased substring match: any cite whose restaurant field contains one
# of these tokens (or matches as a whole word) gets dropped.
_FORBIDDEN_SOURCE_NAMES = frozenset({
    # Delivery / ordering aggregators
    "doordash", "grubhub", "uber eats", "ubereats", "uber-eats",
    "postmates", "seamless", "caviar",
    # Ordering platforms (cite-only forbidden — they still feed the range
    # pool internally)
    "toast", "toasttab", "toast tab",
    "slice", "slicelife", "slice life", "slice.com",
    "chownow", "chow now",
    "square", "square space",
    # Review / aggregator sites
    "yelp", "tripadvisor", "trip advisor", "zomato",
    "google maps", "google.com",
})


def _is_forbidden_source_name(name: str) -> bool:
    """Return True when `name` matches a forbidden platform/aggregator.
    Catches both whole-name matches and substring hits (e.g., 'Toast'
    inside 'Toast Tab Pizza Co' would still hit). Lowercase normalized."""
    if not name:
        return True
    n = name.lower().strip()
    if not n:
        return True
    if n in _FORBIDDEN_SOURCE_NAMES:
        return True
    for forbidden in _FORBIDDEN_SOURCE_NAMES:
        # Substring guard — "Toast Bistro" should pass (legitimate
        # restaurant name containing 'toast'); only block when 'toast'
        # appears as its own word/component.
        if forbidden in n:
            # Word-boundary check to avoid false positives like
            # "Roastoria" containing "toast"
            import re as _re
            pattern = r"\b" + _re.escape(forbidden) + r"\b"
            if _re.search(pattern, n):
                return True
    return False


# Compound size labels we explicitly reject when matching against a
# customer's single-size variant. Gemini's "Med" token in "Med/Large"
# creates a strong false-match signal — backstop catches it.
import re as _re_compound
_COMPOUND_SIZE_PATTERN = _re_compound.compile(
    r"\b(sm|small|md|med|medium|reg|regular|lg|large|xl|x-large)"
    r"\s*/\s*"
    r"(sm|small|md|med|medium|reg|regular|lg|large|xl|x-large|family|party)\b",
    _re_compound.IGNORECASE,
)


def _quote_has_compound_size(quote: str) -> bool:
    """Return True when the quote contains a compound size label like
    'Small/Med' or 'Med/Large'. The customer's single-size variant
    can't unambiguously match either side of the slash, so the cite
    should be rejected to prevent the Nicky's-style misclassification.

    NOTE: only call this when the customer's own variant label does
    NOT contain a slash — otherwise legit compound-size customers
    would lose all their cites.
    """
    if not quote:
        return False
    return bool(_COMPOUND_SIZE_PATTERN.search(quote))


# Quantity-mismatch detection — catches cases where Gemini cites a
# small-quantity item (6 Wings $9.95) under a customer's large-quantity
# variant (30 Pcs Wings). Wings are the canonical example: the same
# competitor menu often has multiple quantity tiers and Gemini's loose
# matching treats them as interchangeable. Pattern matches "N wings",
# "N pcs", "N pieces", etc. — pulls the integer for comparison.
_QTY_PATTERN = _re_compound.compile(
    r"\b(\d{1,3})\s*(pcs?|pieces?|piece|wings?|count|ct)\b",
    _re_compound.IGNORECASE,
)


def _extract_quantity(text: str) -> Optional[int]:
    """Extract a leading piece-count from text. Returns None if no
    quantity-like token found."""
    if not text:
        return None
    m = _QTY_PATTERN.search(text)
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            return None
    return None


def _quantity_mismatch(item_name: str, quote: str, tolerance: float = 0.5) -> bool:
    """True if the quote's quantity differs from the item's quantity
    by more than `tolerance` (proportionally). E.g. customer's
    "30 Pcs Wings" vs quote "10 Pieces Wings $9.99" — differs by 67%,
    over the 50% tolerance, reject. Same number tolerated up to 50%
    drift to allow loose matches like "20 Pcs" vs "20 piece" or
    "20 Pcs Wings $X" vs "Wings (24) $Y".

    Only fires when BOTH item_name and quote contain quantity tokens.
    Items without piece counts (most menu items) skip this check
    entirely.
    """
    item_qty = _extract_quantity(item_name)
    quote_qty = _extract_quantity(quote)
    if item_qty is None or quote_qty is None:
        return False  # at least one side has no quantity → can't compare
    if item_qty == 0:
        return False
    drift = abs(quote_qty - item_qty) / item_qty
    return drift > tolerance


# Plausibility ceiling for cited prices. Catches hallucinated absurd
# values ($9,999 menu items) and parser errors. $200 is generous for
# any reasonable single menu item — adjust if we ever onboard fine-
# dining or catering-only customers.
PRICE_PLAUSIBILITY_CEILING_CENTS = 20_000


def _apply_category_ranges(
    our_rows: List[Any],
    item_variants: Dict[int, List[Dict[str, Any]]],
    item_market: Dict[int, Dict[str, Any]],
    city: str,
    state: str,
    zip_code: str,
    cuisine: str,
    full_address: str,
    draft_id: Optional[int] = None,
) -> int:
    """Apply a shared market range to items that cluster at the same
    (category, size_label, customer_price). Source citations (the
    cooler) stay per-item from the earlier _gemini_search_prices call;
    only the range numbers (the boat) get replaced with the cluster
    range.

    Data-driven batching: the menu itself decides what's a batch
    category. If a restaurant prices all its specialty pizzas at $17.95
    for 12" Sml, those 6 items form a group of 6 and share one search.
    If appetizers are all at varying prices, each forms a singleton and
    keeps its per-item range. No hardcoded "batch this category" lists.

    Group key:
      - Items with variants → (category, variant_label, variant_price)
      - Items without variants → (category, "", current_price)

    Singletons (1 item at a unique price in their category) keep the
    per-item range. Only groups with 2+ members get the cluster
    override. Returns the count of items whose ranges were replaced.
    """
    # Build the group → items mapping.
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in our_rows:
        iid = row["item_id"]
        if iid not in item_market:
            continue
        cat = (row.get("item_category") or "").strip()
        if not cat:
            continue
        # Items with variants: group by (cat, label, variant_price)
        if iid in item_variants:
            for v in item_variants[iid]:
                label = (v.get("label") or "").strip()
                vprice = int(v.get("price") or 0)
                if not label or vprice <= 0:
                    continue
                key = (cat, label, vprice)
                groups.setdefault(key, []).append({
                    "item_id": iid,
                    "item_name": row.get("item_name", ""),
                    "variant_label": label,
                    "variant_price": vprice,
                    "kind": "variant",
                })
            continue
        # Items without variants: group by (cat, "", current_price)
        cur_price = int(row.get("current_price") or 0)
        if cur_price <= 0:
            continue
        key = (cat, "", cur_price)
        groups.setdefault(key, []).append({
            "item_id": iid,
            "item_name": row.get("item_name", ""),
            "current_price": cur_price,
            "kind": "non_variant",
        })

    # Only groups with 2+ items get the shared range; singletons keep
    # their per-item search result.
    batch_groups = {k: v for k, v in groups.items() if len(v) >= 2}
    if not batch_groups:
        return 0
    log.info("Category-range pass: %d batch groups across %d items "
             "(of %d total groups)",
             len(batch_groups),
             sum(len(v) for v in batch_groups.values()),
             len(groups))

    # Run category search per group, in parallel (small calls)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    results: Dict[tuple, Dict[str, Any]] = {}
    location = full_address or f"{city}, {state} {zip_code}"

    def _query_one(key):
        cat, size_label, price_cents = key
        examples = [it["item_name"] for it in batch_groups[key]
                    if it.get("item_name")][:5]
        return key, _gemini_search_category_range(
            category=cat, size_label=size_label,
            price_cents=price_cents, examples=examples,
            location=location, cuisine=cuisine, draft_id=draft_id,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_query_one, k) for k in batch_groups.keys()]
        for fut in as_completed(futs):
            try:
                key, rng = fut.result()
                if rng:
                    results[key] = rng
            except Exception as e:
                log.warning("Category range query failed: %s", e)

    if not results:
        return 0

    # Apply category ranges back to item_market entries
    overridden = 0
    for key, rng in results.items():
        cat, size_label, _price = key
        for item_info in batch_groups[key]:
            iid = item_info["item_id"]
            mr = item_market.get(iid)
            if not mr:
                continue
            kind = item_info.get("kind")
            if kind == "variant":
                # Override the variant-specific range, keep the
                # variant's per-item sources
                sizes = mr.get("sizes") or {}
                target_size = sizes.get(size_label)
                if not isinstance(target_size, dict):
                    sizes[size_label] = {
                        "low": rng["low"],
                        "high": rng["high"],
                        "median": rng["median"],
                        "category_range": True,
                    }
                else:
                    target_size["low"] = rng["low"]
                    target_size["high"] = rng["high"]
                    target_size["median"] = rng["median"]
                    target_size["category_range"] = True
                mr["sizes"] = sizes
                overridden += 1
            elif kind == "non_variant":
                # Override the base range, keep the per-item sources
                mr["low"] = rng["low"]
                mr["high"] = rng["high"]
                mr["median"] = rng["median"]
                mr["category_range"] = True
                overridden += 1
    return overridden


def _gemini_search_category_range(
    category: str,
    size_label: str,
    price_cents: int,
    examples: List[str],
    location: str,
    cuisine: str,
    draft_id: Optional[int] = None,
) -> Optional[Dict[str, int]]:
    """Single Gemini call for the typical price range of a price-tier
    cluster: `{size_label} {category}` items priced near
    `${price_cents/100}` in `{location}`. Anchored to the customer's
    price tier so $17.95 specialty pizzas get a different range than
    $11 cheese pizzas, even though both are "12 inch Pizza".

    Returns dict with low_cents/high_cents/median_cents, or None on
    failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return None
    client = genai.Client(api_key=api_key)

    price_dollars = price_cents / 100.0
    if size_label:
        descriptor = f"{size_label} {category}"
    else:
        descriptor = category
    examples_line = ""
    if examples:
        examples_line = (
            f"\nExamples of items at this tier on the source menu: "
            f"{', '.join(examples)}.\n"
        )

    prompt = f"""For {descriptor} priced near ${price_dollars:.2f} at
restaurants near {location}, what's the typical market price range
for items in this tier? Search local menus for {descriptor} that sell
around ${price_dollars:.2f} and report the realistic LOW-HIGH spread
across competitors.{examples_line}
Return JSON only, single object:
{{"low_cents": 1100, "high_cents": 2500, "median_cents": 1700}}

  - low_cents = cheapest typical price for items in this tier locally
  - high_cents = most expensive typical price for items in this tier
  - median_cents = midpoint of the spread

Use real local menu data. If the area has limited data, reason from
nearby comparable markets and widen the range to flag uncertainty
rather than guessing precisely. Return ONLY the JSON, no commentary."""

    try:
        resp = client.models.generate_content(
            model=_GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        txt = (resp.text or "").strip() if resp else ""
        if txt.startswith("```"):
            txt = txt.split("\n", 1)[1] if "\n" in txt else txt[3:]
        if txt.endswith("```"):
            txt = txt.rsplit("```", 1)[0]
        txt = txt.strip()
        data = json.loads(txt)
        low = int(data.get("low_cents") or 0)
        high = int(data.get("high_cents") or 0)
        median = int(data.get("median_cents") or 0)
        if low <= 0 or high <= 0 or median <= 0:
            return None
        if low > high:
            low, high = high, low
        if median < low or median > high:
            median = (low + high) // 2
        return {"low": low, "high": high, "median": median}
    except Exception as e:
        log.warning("Category range query failed for %r %r @ %d: %s",
                    category, size_label, price_cents, e)
        return None


def _count_market_sources(entry: Dict[str, Any]) -> int:
    """Total sources backing a market-rate entry: base sources plus all
    per-size sources. Used by the low-source retry pass to find items
    that came back with too few citations to be trustworthy."""
    if not isinstance(entry, dict):
        return 0
    n = len(entry.get("sources") or [])
    for sz in (entry.get("sizes") or {}).values():
        if isinstance(sz, dict):
            n += len(sz.get("sources") or [])
    return n


def _quote_validates_price(quote: str, price_cents: int, item_name: str) -> bool:
    """Verify a Gemini source quote actually backs the cited price + item.

    Two checks:
      (a) The quote contains a recognizable form of the dollar amount.
          We accept any of: "$14.99", "14.99", "$14", "14", or with
          space/comma punctuation. False negatives possible (e.g.,
          European-format "14,99") but rare for US menus.
      (b) The quote contains at least one significant token from the
          item name. "Significant" = a word longer than 3 chars that
          isn't a generic stopword. Catches Gemini citing a price for
          the wrong item (e.g., "Pepperoni Pizza $14.99" being credited
          as a Cheese Pizza source).

    Conservative: when in doubt, reject. The whole point is to cut
    fabricated cites.
    """
    if not quote:
        return False
    q = quote.lower()

    # Price match — try a few formats.
    dollars = price_cents // 100
    cents = price_cents % 100
    formats = [
        f"${dollars}.{cents:02d}",     # $14.99
        f"{dollars}.{cents:02d}",       # 14.99
    ]
    if cents == 0:
        formats.append(f"${dollars}")   # $14
        formats.append(f" {dollars} ")  # ' 14 '
    if not any(f in q for f in formats):
        return False

    # Item-name token match — require at least one significant token
    # from the item name to appear in the quote. Skip generic words.
    # Synonyms map: when our item is a common dish that competitors
    # often list under a different name, accept either. Keeps quote
    # validation strict on fabricated quotes while not rejecting
    # legitimate cites where the source happens to use a synonym.
    _STOP = {"the", "and", "with", "for", "size", "small", "large",
             "medium", "regular", "personal", "mini", "pizza"}
    # General principle: synonyms map ONLY to the SAME PRODUCT under a
    # different name — not "similar items with different ingredients."
    # If two items differ in ingredients (Margherita vs Cheese), prep
    # method (Stromboli rolled vs Calzone folded), or core composition
    # (Boneless wings = breaded chicken bites, NOT bone-in wings), they
    # are DIFFERENT items even if names share tokens. Don't put them
    # here. The cost of a missed legit cite is far lower than the cost
    # of a wrong-item cross-cite that misleads the customer.
    _SYNONYMS = {
        "cheese": ("plain", "mozzarella"),
        "hamburger": ("burger",),
        "cheeseburger": ("cheese burger",),
        "wings": ("buffalo", "chicken", "wing"),
        "buffalo": ("wings", "wing"),
        "fries": ("french", "side", "basket"),
        "calzone": ("calz",),
        "sub": ("hoagie", "grinder", "hero"),
        "hoagie": ("sub", "grinder", "hero"),
        "grinder": ("sub", "hoagie", "hero"),
    }
    name_lower = (item_name or "").lower()
    tokens = [t for t in re.findall(r"[a-z]+", name_lower)
              if len(t) > 3 and t not in _STOP]
    if not tokens:
        # Fall back to ANY name token (item name was all stopwords)
        tokens = [t for t in re.findall(r"[a-z]+", name_lower)
                  if len(t) > 2]
    if not tokens:
        # No usable tokens at all — accept on price match alone.
        return True
    # Expand each token to its synonym set; quote must match any expansion.
    expanded = set()
    for t in tokens:
        expanded.add(t)
        for syn in _SYNONYMS.get(t, ()):
            expanded.add(syn)
    if not any(e in q for e in expanded):
        return False

    # PROXIMITY CHECK (Gemini's recommendation, round 10): the
    # differentiator token must appear NEAR the cited price, not just
    # anywhere in the quote. Catches the multi-item-on-one-line case:
    #   quote: "Cheese Pizza $14.99 / Meat Lovers $19.99"
    #   cited price: $14.99 with item "Meat Lovers Pizza"
    # The previous check passed because "meat" appears somewhere in
    # the quote — but it's near the WRONG price ($19.99). Proximity
    # check requires the token to be within ~60 chars BEFORE the
    # cited price, which corresponds to "the same line/cell as the
    # price" on a typical menu layout.
    price_idx = -1
    for fmt in formats:
        idx = q.find(fmt)
        if idx >= 0:
            price_idx = idx
            break
    if price_idx > 0:
        # Window: 60 chars before the price + the price itself.
        # Tighter than 25 words since menus pack many items per line.
        window_start = max(0, price_idx - 60)
        window = q[window_start:price_idx]
        if not any(e in window for e in expanded):
            return False
    return True


def _gemini_search_prices(items: List[Dict[str, Any]], city: str, state: str,
                          zip_code: str, cuisine: str,
                          address: str = "",
                          draft_id: Optional[int] = None,
                          competitor_anchors: Optional[List[Dict[str, str]]] = None,
                          ) -> Dict[int, Dict[str, Any]]:
    """Use Gemini with Google Search grounding to get real market prices.

    Batches items and asks Gemini to search Google for actual local pricing.
    Returns {item_id: {low, high, median, sizes: {...}}} in cents.
    """
    import random
    _start_t = time.time()
    _GEMINI_LAST_RUN.update({
        "batches_total": 0, "batches_ok": 0, "batches_failed": 0,
        "items_requested": len(items), "items_returned": 0,
        "errors": [], "duration_s": 0.0,
    })

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        log.error("Gemini: GEMINI_API_KEY not set — cannot run real-price pricing")
        return {}
    if not items:
        return {}

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        log.error("Gemini: google-genai not installed — pip install google-genai")
        return {}

    client = genai.Client(api_key=api_key)

    # No probe, no fallback chain — pro is the only model. If pro is down,
    # we'll find out on the first batch's failure and Haiku takes over.
    out: Dict[int, Dict[str, Any]] = {}
    # 20 items/batch. Gemini 2.5 has a 1M context window so prompt size isn't
    # the constraint — output JSON is. 20 items × ~200 tokens = ~4K output
    # tokens, well under any limit.
    batch_size = 20
    draft_id_for_log = draft_id

    def _run_batch(batch):
        item_lines = ""
        for it in batch:
            variants = it.get("variants", [])
            if variants:
                sizes_str = ", ".join(v["label"] for v in variants if v.get("label"))
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]}) [sizes: {sizes_str}]\n'
            else:
                item_lines += f'- #{it["item_id"]} "{it["item_name"]}" ({it["category"]})\n'

        location = address or f"{city}, {state} {zip_code}"

        # Phase-1 anchor block — confirmed local competitors from Google
        # Places. Inserts a literal list of restaurant names into the
        # prompt so Gemini can run targeted searches ("Athena Pizza
        # combination pizza price") instead of vague open-web discovery.
        # When empty (anchors not available yet, or sparse area), the
        # prompt falls back to the original generic search instructions.
        if competitor_anchors:
            # Render each anchor as `Name (Address)` so Gemini's targeted
            # searches disambiguate restaurants that share names across
            # markets ("Joe's Pizza" matches dozens otherwise). The
            # address came from Google Places — we know it's the right
            # one for this location.
            def _fmt_anchor(a):
                if isinstance(a, dict):
                    name = a.get("name") or ""
                    addr = a.get("address") or ""
                    return f"  - {name} ({addr})" if addr else f"  - {name}"
                return f"  - {a}"
            anchor_block = (
                "\nPRIORITY COMPETITORS — these are confirmed restaurants "
                f"within 5 miles of {location}, vetted by Google Places. "
                "Each anchor is shown as `Name (Address)`. Use BOTH the "
                "name AND address in your targeted search to make sure "
                "you're pulling pricing from the EXACT competitor we "
                'identified — e.g. \'"Joe\'s Pizza 123 Main St" '
                "combination pizza price`. There may be other restaurants "
                "sharing the same name in nearby cities; the address is "
                "how you confirm you've got the right one.\n"
                + "\n".join(_fmt_anchor(a) for a in competitor_anchors)
                + "\n\nWhen you've exhausted these for an item, broaden "
                "with synonyms or expand to other local restaurants you "
                "find via search. The anchor list is a STARTING POINT, "
                "not a hard limit.\n"
            )
        else:
            anchor_block = ""

        prompt = f"""You are pricing a menu against REAL competitor prices. There
are TWO things you produce per item, and they have different rules:

  (1) The PRICE RANGE (low/high/median) — the numbers that drive the
      "Below Market / Higher Range" badge the owner sees on each item.
      Use as much real local pricing data as you can find from BOTH
      restaurant websites AND third-party platforms (Toast, Slice,
      ChowNow, DoorDash, Uber Eats, Grubhub). More data points = a
      truer range. The range stays internal to the calculation — the
      owner sees the numbers, not the underlying mix of sources.

  (2) The CITED SOURCES array (the clickable list shown to the owner).
      The owner is going to click each cited restaurant name and land
      on that restaurant's actual website. If the price you cited
      isn't visibly on that website, they think it's made up. So
      sources in this array must ONLY contain quotes you found on the
      restaurant's own menu page. No third-party platforms in this
      array, ever — not Toast, not DoorDash, none of them.

This split lets you cast a wide net for accurate ranges (the menu
shows badges everywhere) while keeping every clickable cite
trustworthy. If you find 5 prices on platforms and 1 on a restaurant
website, the range uses all 6 prices but the sources array shows
only the 1 verifiable cite. That's correct.

Read this twice: every entry in the SOURCES array must be backed by
a verbatim quote from the restaurant's OWN menu page. No estimates,
no platform quotes, no "typical range" guesses, no averages from
articles. The range itself can include platform prices internally —
just don't surface them as cites.

PHANTOM PRICE CASE — if every price you found came from third-party
platforms (DoorDash/Toast/Slice/Grubhub/UberEats/ChowNow) and ZERO
restaurant websites had the item: return the calculated low/high/
median range from the platform data, with an EMPTY sources array.
This is correct, intended output. The owner sees a price range
badge with no clickable cites. Don't suppress the result, don't
fabricate a website cite, don't skip the item — just return the
range and empty sources.

If you cannot find ANY data (zero direct sites AND zero platforms),
return zero for the item.

For each item below, give me a low-high price range using REAL price
data from restaurants within 5 miles of {location}.
{anchor_block}
Search order (when an anchor list is present):
  1. FIRST: the priority competitors above. Run a targeted search per
     anchor restaurant — `"Restaurant Name" item-name price`. These
     are confirmed local, vetted by Google Places.
  2. THEN: if you need more data, broaden with the generic queries
     below to find restaurants the anchor list missed.

Generic broadening queries:
  For items WITHOUT sizes:  "(item name) (category) price near {location}"
  For items WITH sizes:     "(size) (item name) (category) price near {location}"

IMPORTANT: Only use restaurants within 5 miles of {location}. Distance
is the rule — state lines do NOT matter. Many of our customers are on
state borders (e.g., a restaurant in Agawam, MA has Suffield, CT
within 4 miles). A pizzeria 3 miles south across the CT line is a
local competitor; a pizzeria 25 miles north in the same state is not.
Use geographic distance, not political boundaries.

Do exclude: restaurants in genuinely distant cities (anything beyond
~5 miles), even if they share the customer's state.

VERBATIM QUOTES — every entry in the SOURCES array MUST have a
"quote" field with the exact text from the restaurant's menu page
where you saw the price. The quote must contain BOTH:
  (a) the price you're citing (e.g. "$14.99" or "14.99")
  (b) the item name or a clear synonym
If you can't quote verbatim, DO NOT include the source. ONE real
verifiable cite beats ten fabricated ones.

MENU FRESHNESS — only cite menus that look current. Reject menus
dated from prior years, marked "summer 2020 specials" / "winter 2019",
or showing pricing patterns clearly inconsistent with current local
norms. If you can't tell how old a menu is, but the prices look
reasonable for today's market, accept it. The bar is "doesn't look
obviously stale" — not "must be dated this year".

SOURCES ARRAY rules — what counts as a citeable source:

ACCEPT in the sources array:
  - The restaurant's own website menu page (HTML or text-based PDF
    hosted on their domain). The owner must be able to click and
    see the cited price immediately on that page.

REJECT from the sources array (these can still feed the price RANGE
calculation, just not the cited list shown to the owner):
  - Third-party ordering and delivery platforms — Toast, Square,
    ChowNow, Slice, DoorDash, Uber Eats, Grubhub. Use them for the
    range, never cite them.
  - Yelp, TripAdvisor, Google Maps, Zomato, or any review/aggregator
    site. Outdated and unauthoritative — exclude from BOTH the range
    and the sources array.
  - "Average pizza price in Massachusetts" type articles, roundups,
    blog posts, news pieces, Reddit threads, social media posts.
    Exclude from BOTH range and sources.
  - National chain corporate pricing pages (unless that chain has a
    location within 5 miles AND the local franchise's menu shows the
    price on their own page).
  - Image-only menus you can't extract a verbatim quote from.
  - Scanned/photocopied PDFs whose text extraction yields gibberish.
  - Your own training-data knowledge of typical prices.

Per-size matching: when an item has size variants, you MUST be strict
about which competitor sizes count as matches. The single biggest
mistake to avoid: approximating compound competitor sizes onto our
single sizes.

REJECT compound size labels for single-size matching. If the
competitor's menu has a size like "Med/Large", "Small/Med",
"Small/Reg", or any other compound label using a slash, those are
NOT matches for our customer's "Medium", "Small", or "Large"
variants. They are explicitly different categories — usually
between two of our sizes — and approximating either way produces
wrong comparisons.

WORKED EXAMPLE (real case we got wrong before): Customer has
"Medium" pizza at $13.95. Competitor "Nicky's Pizza" has FIVE
sizes:
   Small      $14.00
   Small/Med  $17.00
   Med/Large  $21.00
   Large      $26.00
   Party      $29.00
The correct match for our Medium is Nicky's Small ($14) — closest
in size and price. Med/Large ($21) and Large ($26) are LARGER
sizes; do NOT cite them as our Medium. The compound label
"Med/Large" must be rejected outright as ambiguous, even though
its name shares the "Med" token with our "Medium".

CONTEXTUAL ANALYSIS REQUIRED: before matching ANY single size,
look at the competitor's full size run as an ordered sequence
(by price ascending). Use the customer's variant's place in
their own size run as guidance — if their Medium is a 12" pizza
priced at $13.95, look for a competitor size in that diameter and
price neighborhood. If no competitor size cleanly maps, OMIT THE
SOURCE for that size. Returning fewer accurate cites is better
than returning approximated wrong ones.

PRICING NUANCES — restaurant menus list prices in confusing ways.
Stick to these rules so the data stays comparable:
  - When a menu shows multiple prices (lunch/dinner, happy hour,
    early bird, brunch), use the STANDARD à la carte dinner price.
    Skip lunch specials, happy-hour discounts, and prix-fixe deals.
  - Skip "from $X.XX" or "starting at $X.XX" pricing — that's a
    floor, not a real price for any specific configuration. Find a
    concrete price for the actual item, or skip the source.
  - The price is for the BASE item as listed. Do not include
    optional upcharges (e.g. "Burger $15, add bacon $3" — record
    $15, not $18). Required toppings or "comes with" inclusions
    that have no separate price are part of the base price.
  - Skip catering / large-format pricing (whole-tray, half-pan,
    by-the-dozen) unless the customer's item is explicitly that
    format. A regular cheese pizza is not the same product as a
    "5lb party pizza."

Items:
{item_lines}

Return JSON only — an array:
[{{"id": 123, "low_cents": 800, "high_cents": 1400, "median_cents": 1100,
   "total_data_points": 8, "sizes": null,
   "sources": [
     {{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "Cheese Pizza  $8.99"}},
     {{"restaurant": "Main St Pizzeria", "price_cents": 1200, "quote": "Cheese Pie - Large $12.00"}},
     {{"restaurant": "Tony's House of Pizza", "price_cents": 1099, "quote": "Plain Cheese - $10.99"}},
     {{"restaurant": "Bella Napoli", "price_cents": 1350, "quote": "Margherita Pizza  $13.50"}},
     {{"restaurant": "Mama's Pizzeria", "price_cents": 950, "quote": "Cheese Pie  $9.50"}}
   ]
}}]

`total_data_points` is the count of REAL prices that fed the range
calculation, including BOTH direct-site prices and platform prices
(Toast/Slice/DoorDash/ChowNow/UberEats/Grubhub). The example above
shows 8 total — 5 from restaurant websites (cited in the array) and
3 from platforms (used for the range, not cited). This is how we
audit whether the two-pool architecture is delivering: a healthy
gap between total_data_points and len(sources) means platforms ARE
widening the data set.

For items with [sizes], include per-size ranges AND sources per size.
total_data_points lives at the per-size level — count separately for
each size:
{{"id": 123, "low_cents": 800, "high_cents": 2500, "median_cents": 1500,
  "total_data_points": 12,
  "sources": [{{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "Cheese Pizza  $8.99"}}],
  "sizes": {{"12\\" Sml": {{"low_cents": 800, "high_cents": 1400, "median_cents": 1100,
    "total_data_points": 7,
    "sources": [
      {{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "12\\" Cheese Pizza - $8.99"}},
      {{"restaurant": "Main St Pizzeria", "price_cents": 1200, "quote": "Small (12 inch) cheese - $12.00"}},
      {{"restaurant": "Tony's House of Pizza", "price_cents": 1099, "quote": "Small Cheese 12in - $10.99"}},
      {{"restaurant": "Bella Napoli", "price_cents": 1350, "quote": "12\\" Margherita - $13.50"}},
      {{"restaurant": "Mama's Pizzeria", "price_cents": 950, "quote": "Small (12\\") Cheese - $9.50"}}
    ]
  }}}}
}}

Rules:
- Use real price data from restaurants within 5 miles of {location}
- RANGE: aim for 3-5+ price data points feeding the low/high range,
  pulled from BOTH restaurant websites AND third-party platforms
  (Toast/Slice/DoorDash/etc.). The wider the data, the truer the
  range. The owner sees only the resulting numbers, not the mix.
- SOURCES ARRAY: include only quotes from the restaurant's own
  website. Zero from this set is OK — the range still gets shown.
  Don't manufacture a citeable source by counting platform quotes,
  outdated menus, or substituted items.
- BOTH POOLS EMPTY: if there's literally no real local pricing data
  (no platforms, no direct sites), set low_cents to 0 and omit
  sources. The item will be skipped. This is rare — most common
  items have at least platform data.
- SYNONYM PRINCIPLE: a synonym is a different NAME for the EXACT SAME
  product. Not a "similar item." Apply this strictly:
    SAME product, different name → synonym, OK to cite:
      Hamburger / Burger
      Cheese Pizza / Plain / Mozzarella Pizza
      Sub / Hoagie / Grinder / Hero (regional names for one sandwich)
      Fries / French Fries / Side of Fries
      Calzone / Calz
      Wings / Buffalo Wings / Chicken Wings / Hot Wings (all bone-in)

    DIFFERENT products that share name tokens → NOT synonyms, never
    cross-cite:
      Cheese Pizza ≠ Margherita Pizza (Margherita has fresh tomato,
        fresh basil, fresh mozzarella; usually priced higher)
      Cheese Pizza ≠ Neapolitan Pizza (different style, different
        ingredients, different price point)
      Calzone ≠ Stromboli (folded vs rolled, different prep)
      Wings ≠ Boneless Wings (boneless = breaded chicken bites,
        different protein product entirely)
      Hamburger ≠ Cheeseburger (cheese is a substantive add)
      Quarter-pound burger ≠ Half-pound burger (different size class)

  When in doubt, treat them as different items and skip rather than
  cross-cite. A missed legit source is recoverable; a wrong cross-
  cite shows the customer a misleading "above/below market" pill.

- QUANTITY MATCHING: when an item has a piece count or portion size
  in its name (e.g., "30 Pcs Wings", "16 inch Pizza", "Half Rack
  Ribs"), the cited source must reference the SAME quantity. Citing
  a 6-piece wing price under a 30-piece variant is a quantity
  mismatch — the absolute price will be way off and the comparison
  is meaningless. Match piece-counts and size-classes strictly.
- Every source MUST have a verbatim "quote" field — no quote, no source
- Prices in US cents (e.g. $9.00 = 900)
- The low/high range comes from ALL real prices you found — both
  restaurant websites AND third-party platforms. low = cheapest
  price found anywhere, high = most expensive. Do NOT widen, pad,
  or smooth the range. If you found only one price total (across
  both pools), set low = high = median = that price. Inventing a
  wider range is fabrication.
- Use the item ID numbers exactly as given
- Return ONLY the JSON array, no other text"""

        # Pro-only: one call, one quick retry on transient 503, then give up
        # the batch (its items will be picked up by the per-item retry pass
        # or fall to Haiku). Every attempt is logged to gemini_call_log so
        # we can audit pro's reliability over time.
        batch_ids = [it["item_id"] for it in batch]

        def _attempt():
            t0 = time.time()
            try:
                candidate = client.models.generate_content(
                    model=_GEMINI_MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.1,
                    ),
                )
                txt = (candidate.text or "").strip() if candidate else ""
                dur = time.time() - t0
                if not txt:
                    _log_gemini_call(
                        model=_GEMINI_MODEL, outcome="empty_body",
                        error_type="EmptyResponse",
                        error_message="200 OK with empty body",
                        batch_size=len(batch), duration_s=dur,
                        draft_id=draft_id_for_log,
                    )
                    return None, "EmptyResponse", None, "200 OK with empty body"
                _log_gemini_call(
                    model=_GEMINI_MODEL, outcome="ok",
                    batch_size=len(batch), duration_s=dur,
                    draft_id=draft_id_for_log,
                )
                return candidate, None, None, None
            except Exception as e:
                dur = time.time() - t0
                err_type = type(e).__name__
                err_msg = str(e)[:200]
                err_status = _extract_status_from_error(e)
                _log_gemini_call(
                    model=_GEMINI_MODEL, outcome="error",
                    error_type=err_type, error_status=err_status,
                    error_message=err_msg,
                    batch_size=len(batch), duration_s=dur,
                    draft_id=draft_id_for_log,
                )
                return None, err_type, err_status, err_msg

        response, err_type, err_status, err_msg = _attempt()

        # One retry on transient errors only (503/429/timeout). Empty body
        # and 4xx errors don't get retried — they're deterministic.
        if response is None and err_type and err_type != "EmptyResponse":
            sleep_s = 15.0 + random.uniform(0, 10.0)
            log.warning(
                "Pro batch failed (%s: %s) — retrying in %.1fs",
                err_type, err_msg, sleep_s,
            )
            time.sleep(sleep_s)
            response, err_type, err_status, err_msg = _attempt()

        if not response:
            _GEMINI_LAST_RUN["errors"].append({
                "model": _GEMINI_MODEL,
                "error_type": err_type,
                "error_status": err_status,
                "message": err_msg,
                "batch_size": len(batch),
                "batch_ids": batch_ids,
            })
            log.error(
                "Pro batch failed after retry (%s) — items=%s",
                err_type, batch_ids,
            )
            _GEMINI_LAST_RUN["batches_failed"] += 1
            return {}
        _GEMINI_LAST_RUN["model_usage"][_GEMINI_MODEL] = (
            _GEMINI_LAST_RUN["model_usage"].get(_GEMINI_MODEL, 0) + 1
        )

        try:
            text = (response.text or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            results = json.loads(text)
            batch_out = {}
            # Quick id → name lookup for quote validation
            id_to_name = {it["item_id"]: it.get("item_name") or "" for it in batch}
            id_to_variants = {
                it["item_id"]: [v.get("label", "").lower()
                                for v in it.get("variants", [])
                                if v.get("label")]
                for it in batch
            }
            quote_filter_stats = {
                "kept": 0, "no_quote": 0, "bad_quote": 0,
                "platform_name": 0, "compound_size": 0, "implausible_price": 0,
                "qty_mismatch": 0,
            }

            def _filter_sources(raw_sources, item_name, ctx, customer_size_label=""):
                """Drop sources without a verifiable verbatim quote PLUS the
                programmatic backstops Gemini recommended (round 6 critique):
                  - Reject when source's `restaurant` is a known platform
                    name. These should never reach the cited list.
                  - Reject when the source's quote contains a compound size
                    label (Med/Large, Small/Med) and the customer's variant
                    label is a single non-compound size. This catches
                    Gemini's soft-following of per-size matching rules.
                  - Reject when the cited price is implausible (<= $0 or
                    above PRICE_PLAUSIBILITY_CEILING_CENTS).
                Each rejection is counted in quote_filter_stats so we can
                see how often each backstop is actually firing."""
                if not isinstance(raw_sources, list):
                    return []
                out = []
                for s in raw_sources:
                    if not isinstance(s, dict):
                        continue
                    rest = s.get("restaurant") or ""
                    if not rest or _is_article_title(rest):
                        continue
                    # Backstop 1: hardcoded platform-name rejection. The
                    # prompt forbids these but rules are soft-followed —
                    # this is the deterministic safety net.
                    if _is_forbidden_source_name(rest):
                        quote_filter_stats["platform_name"] += 1
                        continue
                    quote = (s.get("quote") or "").strip()
                    price = int(s.get("price_cents") or 0)
                    if not quote:
                        quote_filter_stats["no_quote"] += 1
                        continue
                    if not _quote_validates_price(quote, price, item_name):
                        quote_filter_stats["bad_quote"] += 1
                        continue
                    # Backstop 2: compound-size rejection. Only fires when
                    # we're matching against a specific size slot AND that
                    # slot's label isn't itself a compound. Catches the
                    # Nicky's Med/Large -> our Medium misclassification.
                    if customer_size_label and "/" not in customer_size_label:
                        if _quote_has_compound_size(quote):
                            quote_filter_stats["compound_size"] += 1
                            continue
                    # Backstop 2b: quantity rejection. Two cases:
                    #   (i) Both sides have piece counts and they differ
                    #       by more than tolerance → reject (catches
                    #       "30 Pcs" item cited with "6 Wings" quote).
                    #   (ii) Item has a piece count but quote does NOT
                    #        → reject (can't verify quantity match;
                    #        catches "30 Pcs Wings" being cited with
                    #        a generic "Wings $14.99" quote that's
                    #        actually for 6-piece pricing).
                    item_has_qty = _extract_quantity(item_name) is not None
                    if item_has_qty:
                        quote_has_qty = _extract_quantity(quote) is not None
                        if not quote_has_qty:
                            quote_filter_stats["qty_mismatch"] += 1
                            continue
                        if _quantity_mismatch(item_name, quote):
                            quote_filter_stats["qty_mismatch"] += 1
                            continue
                    # Backstop 3: implausibility. Catches hallucinated
                    # absurd prices (e.g., $9999) or zero/negative parses.
                    if price <= 0 or price > PRICE_PLAUSIBILITY_CEILING_CENTS:
                        quote_filter_stats["implausible_price"] += 1
                        continue
                    quote_filter_stats["kept"] += 1
                    out.append({
                        "restaurant": rest,
                        "price_cents": price,
                        "quote": quote[:200],  # cap quote length for storage
                    })
                return out

            for r in results:
                iid = _coerce_item_id(r.get("id"))
                low = r.get("low_cents", 0)
                high = r.get("high_cents", 0)
                med = r.get("median_cents", 0)
                if iid and low > 0 and high > 0 and med > 0:
                    item_name = id_to_name.get(iid, "")
                    entry = {"low": int(low), "high": int(high), "median": int(med)}
                    # Total real prices that fed this item's range, across
                    # BOTH direct websites and platforms. Used as the audit
                    # metric for the two-pool architecture: a healthy gap
                    # between this and len(sources) confirms platforms are
                    # widening the data set.
                    # Drop sources without verifiable verbatim quotes.
                    # Day 141.9: Gemini's grounded search frequently fabricates
                    # plausible-looking prices when it can't find an exact size
                    # match; the quote field forces it to back each cite with
                    # actual page text or omit the source.
                    base_sources = _filter_sources(r.get("sources"), item_name, "base")
                    # Defensive floor: Gemini sometimes omits total_data_points
                    # entirely (we saw cited > reported in the audit metric on
                    # the last run). Floor at len(base_sources) since we KNOW
                    # that many data points exist (the cited, validated ones).
                    base_dp = int(r.get("total_data_points") or 0)
                    base_dp = max(base_dp, len(base_sources))
                    if base_dp > 0:
                        entry["total_data_points"] = base_dp
                    if base_sources:
                        entry["sources"] = base_sources

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
                                    size_sources = _filter_sources(
                                        sdata.get("sources"), item_name, f"size:{slabel}",
                                        customer_size_label=slabel,
                                    )
                                    # Same defensive floor as base.
                                    sz_dp = int(sdata.get("total_data_points") or 0)
                                    sz_dp = max(sz_dp, len(size_sources))
                                    if sz_dp > 0:
                                        size_entry["total_data_points"] = sz_dp
                                    # Keep the size if it has either citeable
                                    # sources OR platform data (sz_dp > 0
                                    # means real prices fed the range, even
                                    # if none came from direct sites).
                                    if size_sources:
                                        size_entry["sources"] = size_sources
                                    if not size_sources and sz_dp <= 0:
                                        continue
                                    sizes_out[slabel] = size_entry
                        if sizes_out:
                            entry["sizes"] = sizes_out

                    # Keep the item if any real prices fed the range —
                    # whether they're citeable (sources array) or platform-
                    # only (total_data_points > 0). Drop only when nothing
                    # backs the range at all (Gemini hallucinated).
                    has_base_data = bool(entry.get("sources")) or base_dp > 0
                    has_size_data = bool(entry.get("sizes"))
                    if not has_base_data and not has_size_data:
                        continue

                    batch_out[iid] = entry

            if any(quote_filter_stats.values()):
                log.info(
                    "Quote filter: kept=%d, no_quote=%d, bad_quote=%d, "
                    "platform_name=%d, compound_size=%d, implausible_price=%d",
                    quote_filter_stats["kept"],
                    quote_filter_stats["no_quote"],
                    quote_filter_stats["bad_quote"],
                    quote_filter_stats["platform_name"],
                    quote_filter_stats["compound_size"],
                    quote_filter_stats["implausible_price"],
                )
            # Two-pool architecture audit metric. Per-batch totals of
            # data points found vs cited sources — a healthy gap proves
            # platforms ARE widening the data set. Equal numbers mean
            # platforms aren't contributing and the design isn't paying
            # for itself.
            _total_dp = 0
            _total_cites = 0
            for _e in batch_out.values():
                _total_dp += int(_e.get("total_data_points") or 0)
                _total_cites += len(_e.get("sources") or [])
                for _sz in (_e.get("sizes") or {}).values():
                    if isinstance(_sz, dict):
                        _total_dp += int(_sz.get("total_data_points") or 0)
                        _total_cites += len(_sz.get("sources") or [])
            log.info(
                "Gemini batch ok: %d/%d items returned. "
                "Two-pool metric: %d total data points (range pool), "
                "%d cited sources (direct sites only). gap=%d "
                "(higher gap = platforms widening data).",
                len(batch_out), len(batch),
                _total_dp, _total_cites, _total_dp - _total_cites,
            )
            _GEMINI_LAST_RUN["batches_ok"] += 1
            return batch_out
        except json.JSONDecodeError as e:
            preview = (text or "")[:200] if 'text' in locals() else "<no response>"
            log.error(
                "Gemini batch JSON parse failed: %s — preview=%r — items=%s",
                e, preview, batch_ids,
            )
            _GEMINI_LAST_RUN["batches_failed"] += 1
            _GEMINI_LAST_RUN["errors"].append({
                "attempt": "parse", "error_type": "JSONDecodeError",
                "message": str(e)[:200], "batch_size": len(batch), "batch_ids": batch_ids,
            })
            return {}
        except Exception as e:
            log.error(
                "Gemini batch processing failed: %s: %s — items=%s",
                type(e).__name__, e, batch_ids,
            )
            _GEMINI_LAST_RUN["batches_failed"] += 1
            _GEMINI_LAST_RUN["errors"].append({
                "attempt": "process", "error_type": type(e).__name__,
                "message": str(e)[:200], "batch_size": len(batch), "batch_ids": batch_ids,
            })
            return {}

    # Run batches in parallel. 2 workers (was 4) — long grounded calls
    # (~30-60s each) compound badly under contention; halving the pressure
    # cut the 503/timeout rate dramatically in testing without changing
    # wall time meaningfully (we're network-bound, not CPU-bound).
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    _GEMINI_LAST_RUN["batches_total"] = len(batches)
    from concurrent.futures import ThreadPoolExecutor
    # 2 parallel workers. Earlier "sequential to avoid self-throttling"
    # turned out to be wrong — the 503s we attributed to rate limiting
    # were actually the #-prefix parser bug silently dropping successful
    # responses. With that fixed, parallel pressure is fine. 2 workers
    # cuts wall time roughly in half; the adaptive demotion makes batch
    # 2 onwards skip the failing primary model entirely.
    with ThreadPoolExecutor(max_workers=min(2, len(batches))) as pool:
        futures = [pool.submit(_run_batch, b) for b in batches]
        for f in futures:
            try:
                out.update(f.result())
            except Exception as e:
                log.error("Gemini batch future raised: %s: %s", type(e).__name__, e)

    # Batch-level retry pass: if SOME batches succeeded and others failed,
    # Pro is partially up — retry the failed batches once. Catches the
    # "Pro is heavily throttled but not fully down" case (observed Apr 29:
    # 1/14 batches ok, 13 failed; the one success proves Pro is reachable).
    # Skip when no batches succeeded (Pro is genuinely down → Haiku) and
    # skip when all succeeded (nothing to retry).
    if 0 < _GEMINI_LAST_RUN["batches_ok"] < _GEMINI_LAST_RUN["batches_total"]:
        succeeded_ids = set(out.keys())
        failed_batches = [
            b for b in batches
            if not any(it["item_id"] in succeeded_ids for it in b)
        ]
        if failed_batches:
            log.info(
                "Batch retry pass: %d batches failed, %d succeeded — "
                "retrying failed batches sequentially (Pro may be partially recovering)",
                len(failed_batches), _GEMINI_LAST_RUN["batches_ok"],
            )
            recovered = 0
            for batch in failed_batches:
                # Sequential with a small gap — gives Pro a moment between
                # calls and avoids resurfacing whatever throttle hit on the
                # first pass.
                time.sleep(3 + random.uniform(0, 2))
                try:
                    result = _run_batch(batch)
                    if result:
                        out.update(result)
                        recovered += len(result)
                except Exception as e:
                    log.warning("Retry batch raised: %s: %s", type(e).__name__, e)
            if recovered:
                log.info("Batch retry pass recovered %d items", recovered)

    _GEMINI_LAST_RUN["items_returned"] = len(out)
    _GEMINI_LAST_RUN["duration_s"] = round(time.time() - _start_t, 1)
    model_breakdown = ", ".join(
        f"{m}={n}" for m, n in _GEMINI_LAST_RUN["model_usage"].items()
    ) or "none"
    log.info(
        "Gemini search summary: %d/%d items, %d/%d batches ok, "
        "models=[%s], %d errors logged, %.1fs",
        len(out), len(items),
        _GEMINI_LAST_RUN["batches_ok"], _GEMINI_LAST_RUN["batches_total"],
        model_breakdown,
        len(_GEMINI_LAST_RUN["errors"]),
        _GEMINI_LAST_RUN["duration_s"],
    )
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
                iid = _coerce_item_id(r.get("id"))
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
                    batch_out[iid] = entry
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
                iid = _coerce_item_id(r.get("id"))
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
                    batch_out[iid] = entry
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


# Number followed by up to 3 words, then a piece/wing keyword. Catches:
#   "20 Pcs"         → 20
#   "12 Boneless Wings" → 12
#   "BONELESS BUFFALO WINGS 15 Pieces" → 15
# Doesn't match pizza slice counts ("Pizza 8 Slices" excluded by keyword
# list) since slice counts are about the pie, not the variant size.
# Menu-abbreviation synonym dict. Maps shortform → canonical token so
# rapidfuzz's token comparison treats "Combo Pizza" and "Combination
# Pizza" as the same product. Hand-curated for restaurant menus —
# there's no comprehensive open-source equivalent that covers food
# abbreviations specifically. Conservative: only adds entries where
# the abbreviation is unambiguous in restaurant context.
_MENU_SYNONYMS = {
    "combo": "combination",
    "spec": "special",
    "spcl": "special",
    "ckn": "chicken",
    "chkn": "chicken",
    "pep": "pepperoni",
    "pepp": "pepperoni",
    "veggie": "vegetable",
    "veg": "vegetable",
    "mush": "mushroom",
    "mshrm": "mushroom",
    "marg": "margherita",
    "marghr": "margherita",
    "sup": "supreme",
    "supr": "supreme",
    "buff": "buffalo",
    "bbq": "barbecue",
    "stk": "steak",
    "shrmp": "shrimp",
    "pinap": "pineapple",
    "pin": "pineapple",
    "trad": "traditional",
    "med": "medium",
    "mdm": "medium",
    "lrg": "large",
    "lg": "large",
    "sml": "small",
    "sm": "small",
    "xl": "extra-large",
    "xlg": "extra-large",
    "pcs": "pieces",
    "pc": "piece",
    "fav": "favorite",
    "grnd": "grinder",
    "grdr": "grinder",
    "swch": "sandwich",
    "sndwch": "sandwich",
    "parm": "parmesan",
    "alf": "alfredo",
    "calz": "calzone",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Tokens stripped before similarity comparison. These don't
# distinguish between menu items — every calzone has "calzone" in the
# name, every pizza has "pizza", so leaving them in inflates token-set
# similarity to ~75 even when the distinguishing words share nothing.
_GENERIC_NAME_TOKENS = frozenset({
    # Category nouns
    "pizza", "pie", "calzone", "calzones", "sandwich", "sandwiches",
    "sub", "subs", "grinder", "grinders", "hoagie", "wrap", "wraps",
    "salad", "salads", "burger", "burgers", "hamburger", "pita",
    "platter", "platters", "dinner", "entree",
    # Size nouns
    "personal", "small", "medium", "large", "party", "family",
    "regular", "deluxe", "mini", "size", "sized",
    # Quantity descriptors
    "slices", "slice", "pieces", "piece", "pcs", "pc", "inch",
    # Common menu connectors
    "with", "and", "the", "a", "an", "of", "to", "for", "or",
    "served", "side",
})


def _normalize_menu_name(name: str) -> str:
    """Lowercase, expand menu abbreviations, strip generic category /
    size / connector tokens, and singularize. The result contains
    only the DISTINGUISHING tokens — those that identify the specific
    product, not the category noun.

    'Chicken Bacon Ranch Calzone'   → 'chicken bacon ranch'
    'COMBO PIZZA Small (8 Slices)'  → 'combination'
    "MEAT LOVER'S PIZZA"            → 'meat lover'  (apostrophe-s + plural fold)
    'Cheeseburger Melt'             → 'melt'        (cheeseburger generic)
    """
    if not name:
        return ""
    n = name.lower()
    tokens = _TOKEN_RE.findall(n)
    out = []
    for t in tokens:
        full = _MENU_SYNONYMS.get(t, t)
        if full in _GENERIC_NAME_TOKENS:
            continue
        if full.isdigit():
            continue
        if len(full) <= 1:        # apostrophe-s leftovers
            continue
        # Singularize: 'lovers'→'lover', 'wings'→'wing', 'melts'→'melt'
        if len(full) > 3 and full.endswith("s") and not full.endswith("ss"):
            full = full[:-1]
        out.append(full)
    return " ".join(out)


# Product-type words pulled from category to enrich the similarity
# tokens. When a customer names an item just by protein ("Tuna" in
# "Club Sandwiches"), the category tells us it's a club. Adding
# 'club' to the customer's effective token set means anchors must
# also be clubs (or have 'club' in their name/category) to score
# high. Without this, "Tuna" customer matched "Tuna Melt", "Tuna
# Salad Sandwich" — different products that happen to share the
# protein word.
#
# Only "type-distinguishing" words go here; brand/quality words like
# "Angus" or "Gourmet" aren't included because they don't distinguish
# product types, just qualities of the same type.
_CATEGORY_TYPE_WORDS = frozenset({
    "club", "melt", "panini", "hoagie", "wrap", "sub", "grinder",
    "pita", "platter", "dinner", "stromboli", "frittata",
    "specialty", "gourmet",
})


def _category_type_tokens(category: str) -> set:
    """Pull the type-distinguishing token from a category. 'Club
    Sandwiches' → {'club'}. 'Melt Sandwiches' → {'melt'}. '6 Oz
    Angus Burgers' → {} (no type word). 'Wraps' → {} (wrap stripped
    as generic noun)."""
    if not category:
        return set()
    n = _normalize_cat(category)
    out = set()
    for t in n.split():
        if t in _CATEGORY_TYPE_WORDS and t not in _GENERIC_NAME_TOKENS:
            out.add(t)
    return out


def _name_similarity(cust_name: str, anch_name: str,
                      cust_cat: str = "", anch_cat: str = "") -> int:
    """Asymmetric token coverage (0-100): what fraction of the
    customer's distinguishing tokens appear in the anchor's name.

    Why asymmetric (not Jaccard, not rapidfuzz token_set_ratio):
      * token_set_ratio returns 100 when one set is a subset of the
        other — 'Cheeseburger Melt' vs 'Cheeseburger' scored 100,
        which let plain burgers contaminate Melt Sandwich sources.
      * Jaccard penalizes anchors that have EXTRA descriptive words
        ('Cheese Pizza' customer vs 'TRADITIONAL CHEESE PIZZA' anchor
        scored 50 even though they're the same product).
      * Asymmetric coverage is correct for both: it asks "does the
        anchor contain everything the customer's name calls for?"
        Extra anchor tokens are fine (Cheese Pizza customer matches
        TRADITIONAL CHEESE PIZZA at 100%), but missing customer
        tokens drop the score (Cheeseburger Melt customer vs plain
        Cheeseburger anchor scores 0 — anchor is missing 'melt').

    Examples (threshold 60):
      'Combination'           vs 'COMBO PIZZA'             → 100
      'Combination'           vs 'VEGGIE COMBO PIZZA'      → 100  (anchor superset)
      'Cheese Pizza'          vs 'TRADITIONAL CHEESE PIZZA' → 100
      'Cheeseburger Melt'     vs 'Cheeseburger'            →   0   (missing 'melt')
      'Manhattan Club'        vs 'Hamburger Club Swch'     →  50   (only 'club' shared)
      'Steak & Cheese Melt'   vs 'STEAK & CHEESE SANDWICH' →  67
      'Steak & Cheese Melt'   vs 'HAM & CHEESE SANDWICH'   →  33
      'Honey Mustard ChickenBacon' vs 'Honey Mustard Grilled Chicken Bacon' → 100
    """
    n1 = _normalize_menu_name(cust_name)
    n2 = _normalize_menu_name(anch_name)
    t1 = set(n1.split()) if n1 else set()
    t2 = set(n2.split()) if n2 else set()
    # Enrich both sides with type-distinguishing tokens from category
    # ("Club Sandwiches" → {'club'}). Closes the gap when the customer
    # name is just an ingredient ("Tuna") and the type comes only
    # from the category.
    t1 |= _category_type_tokens(cust_cat)
    t2 |= _category_type_tokens(anch_cat)
    if not t1:
        return 0
    overlap = len(t1 & t2)
    return int(round(100 * overlap / len(t1)))


# Size-tier classifier. Resolves ambiguity by checking EXPLICIT size
# words first (small/medium/large/party), then falling back to inch
# numbers. This way "12\" Sml" is small (because of 'sml') even though
# "12\"" alone could be either small or medium depending on style.
_SIZE_TIER_WORD_PATTERNS = [
    ("party", [
        r"\bparty\b", r"\bfamily\b", r"\bx[\s-]?large\b",
        r"\bxlarge\b", r"\bextra[\s-]large\b", r"\bsuper\b",
        r"\bjumbo\b",
    ]),
    ("large",  [r"\blarge\b", r"\blrg\b", r"\blg\b"]),
    ("medium", [r"\bmedium\b", r"\bmed\b", r"\bmdm\b"]),
    ("small",  [r"\bsmall\b", r"\bsml\b", r"\bpersonal\b",
                r"\bmini\b"]),
]
_SIZE_TIER_INCH_PATTERNS = [
    ("party",  [r"\b(?:18|20|22|24|26|28)['\"]", r"\b17x24\b"]),
    ("large",  [r"\b16['\"]"]),
    ("medium", [r"\b14['\"]"]),
    ("small",  [r"\b(?:8|10|12)['\"]"]),
]


def _size_tier(text: str) -> str:
    """Classify a label/name into small/medium/large/party tier.
    Returns 'unknown' if no size info found — unknown matches any tier
    so anchor items without explicit size still contribute."""
    if not text:
        return "unknown"
    t = text.lower()
    # Explicit size words win.
    for tier, patterns in _SIZE_TIER_WORD_PATTERNS:
        for p in patterns:
            if re.search(p, t):
                return tier
    # Inch fallback for items like "16\" Hawaiian" that have no word.
    for tier, patterns in _SIZE_TIER_INCH_PATTERNS:
        for p in patterns:
            if re.search(p, t):
                return tier
    return "unknown"


_PIECE_COUNT_RE = re.compile(
    r'\b(\d+)(?:\s+\w+){0,3}?\s+(?:pc|pcs|piece|pieces|wing|wings)\b',
    re.IGNORECASE,
)

# Specialty-pizza markers — words that indicate a specialty/topping
# pizza vs a plain cheese. Used to keep customer "Combination Pizza"
# from being cited against anchor "Cheese Pizza Large" just because
# the prices align.
_SPECIALTY_MARKERS = frozenset({
    "pepperoni", "sausage", "bacon", "ham", "hawaiian", "philly",
    "buffalo", "bbq", "taco", "mexican", "mediterranean", "mushroom",
    "veggie", "vegetarian", "supreme", "special", "deluxe", "gourmet",
    "combo", "combination", "meat", "lovers", "margherita", "pesto",
    "alfredo", "parmesan", "florentine", "ranch", "broccoli",
    "spinach", "garlic", "chicken", "steak", "burger", "souvlaki",
    "stromboli", "white",
})


def _extract_piece_count(name: str) -> Optional[int]:
    """Pull '20 Pcs', '15 Pieces', '10 Wings' etc. as integer count.
    Returns None if no count found. Used for strict piece-count
    matching so '20 Pcs Wings' doesn't cite '12 Pcs' or '50 Pcs' as
    sources."""
    if not name:
        return None
    m = _PIECE_COUNT_RE.search(name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


# Categories where specialty/plain matters for matching. Anchor items
# are filtered to the same specialty-status as the customer's item.
# Add cautiously — only categories where "plain baseline + topping
# variants" is the real product structure.
_SPECIALTY_FILTERED_CATS = ("pizza", "calzone", "burger", "dog", "omelet")

# Product-type categories — anchors in different product types
# shouldn't cross-cite. A "Hamburger Pita" (Pita category) shouldn't
# appear as a source for a customer's "Burger" because they're
# different product types, even though both have "hamburger" in name.
# This is a SUPERSET of _SPECIALTY_FILTERED_CATS.
_PRODUCT_TYPE_CATS = (
    "pizza", "calzone", "burger", "dog", "omelet",
    "pita", "grinder", "sandwich", "wrap", "sub", "hoagie", "salad",
    "soup", "wing", "platter", "stromboli",
)

# Tokens that are the BASE NAME (not a specialty marker) for the
# base-noun categories. "Cheeseburger" alone is plain; "Bacon
# Cheeseburger" is specialty because of "bacon". "Cheese Omelet" is
# plain; "Western Omelet" / "Bacon Cheese Omelet" is specialty.
# Pizza/calzone use the marker list instead — different pattern.
_BASE_NAME_TOKENS_BY_CAT = {
    "burger": frozenset({"burger", "hamburger", "cheeseburger",
                          "cheese", "plain", "kid", "kids", "angus",
                          "oz", "beef"}),
    "dog":    frozenset({"dog", "hot", "hotdog", "frank",
                          "frankfurter", "cheese", "plain",
                          "kid", "kids"}),
    "omelet": frozenset({"omelet", "omelette", "egg", "eggs",
                          "cheese", "plain", "kid", "kids"}),
}


def _is_modifier_subcategory(subcat: str) -> bool:
    """Detect modifier/add-on sub-categories that shouldn't get pricing
    intel. These are ingredient options or preparation choices, not
    standalone menu items — the customer doesn't sell 'Pepperoni'
    alone, they sell pizzas with pepperoni as a $1.50 add-on.

    Matches: Toppings, Sauce Options, Wing Sauces, Bread Options,
    Tortilla Options, Preparation, Add-Ons, Sides, Modifiers, etc.
    """
    if not subcat:
        return False
    s = subcat.lower().strip()
    return any(p in s for p in (
        "topping", "sauce", "preparation", "add-on", "addon", "add on",
        "modifier", "options", "tortilla", "bread option",
    ))


def _is_by_the_slice(name: str) -> bool:
    """Detect 'by the slice' pizza items vs whole pies. Whole pies
    have slice counts in PARENTHESES as size descriptors ('(8 Slices)',
    '(6 Slices)') — those are kept. By-the-slice items have 'slice'
    or 'slices' in the main name without parentheses — those are
    different products and shouldn't be cited under whole-pie clusters.

    Examples:
        'Cheese Pizza Personal (6 Slices)' → False (whole pie)
        'Cheese Slice'                      → True  (single slice)
        'Two Cheese Pizza Slices Lunch'     → True  (multi-slice deal)
        'Pizza by the Slice'                → True
        'Cheese Pizza Mini 10"'              → False (size, not slices)
    """
    if not name:
        return False
    # Strip parenthetical content — the slice count there describes
    # the whole pie's size, not a by-the-slice product
    no_parens = re.sub(r"\([^)]*\)", "", name).lower()
    return bool(re.search(r"\bslices?\b", no_parens))


def _is_specialty_item(name: str, category: str = "") -> bool:
    """True if item is specialty (has toppings/styles beyond the base).

    Pizza/calzone: marker-based. Looks for pepperoni/sausage/buffalo/
    combination/etc. tokens. 'Cheese Pizza' / 'Plain Calzone' → False;
    'Combination Pizza' / 'Buffalo Chicken Calzone' → True.

    Burger/hot dog: base-name-based. Anything beyond burger/hamburger/
    cheeseburger (or hot dog/frank) tokens is specialty. 'Burger' /
    'Cheeseburger' / 'Crestview Burger' (just brand+base) → False;
    'Bacon Burger' / 'Mama\\'s Burger' / 'Chili Dog' → True.

    Other categories: returns False (filter doesn't apply)."""
    if not name:
        return False
    n = name.lower()
    cat = (category or "").lower()

    # Burger / hot-dog: distinguishing tokens beyond the base name
    for cat_key, base_tokens in _BASE_NAME_TOKENS_BY_CAT.items():
        if cat_key in cat:
            tokens = _TOKEN_RE.findall(n)
            for t in tokens:
                full = _MENU_SYNONYMS.get(t, t)
                if full in _GENERIC_NAME_TOKENS:
                    continue
                if full in base_tokens:
                    continue
                if full.isdigit():
                    continue
                # Found a distinguishing token (e.g. "bacon", "mama")
                return True
            return False

    # Pizza / calzone: marker-based
    for marker in _SPECIALTY_MARKERS:
        if marker in n:
            return True
    if re.search(r'\b(four|three|4|3)\s*cheese\b', n):
        return True
    return False


# Backwards-compat alias.
_is_specialty_pizza = _is_specialty_item


def _normalize_cat(cat: str) -> str:
    """Lowercase, strip punctuation, simple plural normalize so 'Pizzas',
    'Pizza', "Pizza's" all map to the same key. Used to match customer
    categories against anchor menu categories (which use whatever
    headers each anchor's menu uses)."""
    c = (cat or "").lower().strip()
    c = c.replace("'", "").replace('"', "").replace("&", "and")
    c = c.strip()
    # English plural normalization tuned so customer's "Sandwiches" and
    # an anchor's "Sandwich" both land on "sandwich". Don't strip "es"
    # blindly — that breaks "Calzones" → "calzon" while "Calzone" stays
    # "calzone".
    if c.endswith("ies") and len(c) > 4:
        c = c[:-3] + "y"
    elif (c.endswith("ches") or c.endswith("shes") or c.endswith("xes")
          or c.endswith("zes") or c.endswith("sses")) and len(c) > 4:
        c = c[:-2]
    elif c.endswith("s") and len(c) > 2:
        c = c[:-1]
    return c


def _aggregate_from_anchor_menus(
    our_rows: List[Any],
    item_variants: Dict[int, List[Dict[str, Any]]],
    competitor_anchors: List[Dict[str, str]],
    *,
    target_anchor_count: int = 8,
    max_attempts: int = 15,
    force_refresh: bool = False,
) -> Dict[int, Dict[str, Any]]:
    """Pull menu items from up to N anchor restaurants, then for each
    customer item-cluster (grouped by category + size + price tier),
    compute the low/high range from anchor items in the matching
    category at a similar price tier. Cite the specific anchors and
    items used.

    Replaces the old per-item grounded-search pipeline (which produced
    noisy, non-deterministic ranges that varied per item even when the
    items were structurally identical). Same 5 anchors → same range,
    every time. Cited prices are real items from real menus, not
    synthesized.

    Returns: {item_id: {"low", "high", "median", "sources",
                        "sizes": {label: {"low", "high", "median",
                                          "sources"}}}}
    """
    try:
        from storage.price_intel import (scrape_competitor_menu,
                                          classify_anchor_url,
                                          get_place_details)
    except ImportError:
        log.warning("scrape_competitor_menu not importable — anchor "
                    "aggregation disabled")
        return {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Step 0: tier filter — drop Tier C anchors (brochure sites,
    # country clubs, places with no public menu) before paying for
    # menu extraction. Both the URL-resolve and the tier check are
    # cached, so this is fast on repeat runs.
    raw_candidates = [a for a in competitor_anchors if a.get("place_id")]

    def _classify(anchor):
        try:
            details = get_place_details(anchor["place_id"])
            url = (details or {}).get("website")
            if not url:
                return None
            info = classify_anchor_url(url)
            tier = info.get("tier")
            # Tier A only — these are platforms with FAST extractors
            # (Slice modal-click, Allhungry API). Tier B platforms
            # (Toast/Foodtec/etc. without fast extractors) take too
            # long to scrape during the wizard. They can be added
            # back later if we ship fast extractors for them, or
            # pre-fetched in the background during onboarding.
            if tier != "A":
                return None
            return {
                **anchor,
                "_url": url,
                "_tier": tier,
                "_platform": info.get("platform"),
            }
        except Exception as e:
            log.warning("Tier classify failed for %s: %s",
                        anchor.get("name"), e)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        tiered = list(pool.map(_classify, raw_candidates))
    quality_anchors = [a for a in tiered if a]
    by_tier: Dict[str, int] = {}
    for a in quality_anchors:
        by_tier[a["_tier"]] = by_tier.get(a["_tier"], 0) + 1
    log.info("Anchor tiering: %d/%d are Tier A/B (%s)",
             len(quality_anchors), len(raw_candidates),
             ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))

    # Step 1: pull anchor menus, in parallel, until we have N with
    # actual items. Try up to max_attempts anchors total.
    anchor_menus: List[Dict[str, Any]] = []
    candidates = quality_anchors[:max_attempts]

    def _fetch(anchor):
        try:
            res = scrape_competitor_menu(
                anchor["place_id"], anchor["name"],
                force_refresh=force_refresh,
            )
            items = res.get("items") or []
            if not items:
                return None
            return {
                "place_id": anchor["place_id"],
                "name": anchor["name"],
                "address": anchor.get("address", ""),
                "items": items,
            }
        except Exception as e:
            log.warning("Anchor menu fetch failed for %s: %s",
                        anchor.get("name"), e)
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(_fetch, a) for a in candidates]
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                anchor_menus.append(r)
                if len(anchor_menus) >= target_anchor_count:
                    break
        # Cancel any still-pending futures past our target
        for f in futs:
            if not f.done():
                f.cancel()

    if not anchor_menus:
        log.warning("No usable anchor menus fetched (tried %d)",
                    len(candidates))
        return {}

    log.info("Anchor menus: %d restaurants, %d total priced items",
             len(anchor_menus),
             sum(len(m["items"]) for m in anchor_menus))

    # Step 2: index anchor items by normalized category. Skip non-main
    # roles (sauces, toppings, sides) — those would contaminate ranges.
    anchor_by_cat: Dict[str, List[tuple]] = {}
    for menu in anchor_menus:
        for it in menu["items"]:
            role = it.get("role")
            if role in ("sauce_choice", "topping_choice", "side_choice",
                        "modifier"):
                continue
            price = int(it.get("price_cents") or 0)
            if price <= 0:
                continue
            cat = (it.get("category") or "").strip()
            if not cat:
                continue
            cat_key = _normalize_cat(cat)
            anchor_by_cat.setdefault(cat_key, []).append((menu, it))

    # Step 3: cluster customer items by (cat, label, customer_price).
    # Same logic as the old _apply_category_ranges but applied to ALL
    # items (no per-item search to fall through to anymore).
    clusters: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in our_rows:
        iid = row["item_id"]
        # Skip MODIFIER sub-items (toppings, sauces, bread options,
        # tortilla options, wing preparation, add-ons). These are
        # ingredients/options sold as add-ons, not standalone menu
        # items. Comparing "Pepperoni $1.50" topping to anchor menus
        # produces meaningless matches.
        subcat = (row["subcategory"] if "subcategory" in row.keys()
                  else None) or ""
        if _is_modifier_subcategory(subcat):
            continue
        cat = ((row["item_category"] if "item_category" in row.keys() else "") or "").strip()
        if not cat:
            continue
        item_name = ((row["item_name"] if "item_name" in row.keys() else "") or "")
        if iid in item_variants:
            for v in item_variants[iid]:
                label = (v.get("label") or "").strip()
                vprice = int(v.get("price") or 0)
                if not label or vprice <= 0:
                    continue
                key = (cat, label, vprice)
                clusters.setdefault(key, []).append({
                    "item_id": iid, "kind": "variant",
                    "name": item_name,
                    "label": label, "price": vprice,
                })
            continue
        cur_price = int((row["current_price"] if "current_price" in row.keys() else 0) or 0)
        if cur_price <= 0:
            continue
        key = (cat, "", cur_price)
        clusters.setdefault(key, []).append({
            "item_id": iid, "kind": "non_variant",
            "name": item_name, "price": cur_price,
        })

    # Build a flat all-items index for the name-token fallback below.
    # Used when a customer category has too few anchor matches (e.g.,
    # "6 Oz Angus Burgers" → only 1 anchor has a Burger category, but
    # "Cheeseburger Grinder" hides in Grinders).
    all_anchor_items: List[tuple] = []
    for menu in anchor_menus:
        for it in menu["items"]:
            role = it.get("role")
            if role in ("sauce_choice", "topping_choice", "side_choice",
                        "modifier"):
                continue
            if int(it.get("price_cents") or 0) <= 0:
                continue
            all_anchor_items.append((menu, it))

    # Step 4: for each cluster, find anchor matches → range + sources
    item_market: Dict[int, Dict[str, Any]] = {}
    for (cat, label, cust_price), items_in_cluster in clusters.items():
        cat_key = _normalize_cat(cat)
        # Token-substring category matching. Customer "6 Oz Angus
        # Burgers" → token 'burger' matches anchor categories
        # 'Burgers', 'Hamburger', 'Land & Burgers', '6oz. Angus
        # Burgers' (different formatting/spellings all share the
        # 'burger' substring inside a token). Plain string-containment
        # (the previous approach) failed for most of these because
        # the FULL category strings don't contain each other.
        cust_cat_tokens = [t for t in cat_key.split()
                           if len(t) > 3 and t not in {"with", "and", "the"}]
        candidates_anchor: List[tuple] = []
        seen_ids: set = set()
        for c, lst in anchor_by_cat.items():
            if not c:
                continue
            anch_cat_tokens = [t for t in c.split()
                               if len(t) > 3 and t not in {"with", "and", "the"}]
            matched = False
            for ct in cust_cat_tokens:
                for at in anch_cat_tokens:
                    if ct in at or at in ct:
                        matched = True
                        break
                if matched:
                    break
            if not matched:
                continue
            for entry in lst:
                iid_e = id(entry[1])
                if iid_e in seen_ids:
                    continue
                seen_ids.add(iid_e)
                candidates_anchor.append(entry)

        # Name-token fallback: extract the key noun from the customer's
        # category (last word, normalized — "6 Oz Angus Burgers" →
        # "burger") and find anchor items whose name OR category
        # contains it. Catches items hiding in unexpected categories
        # (Cheeseburger Grinder under "Grinders") that share the noun.
        # Skips when noun is too generic (≤3 chars) to avoid noise.
        # Also skips anchor items in a DIFFERENT specialty-filtered
        # category — e.g., "Bacon Cheeseburger Pizza" shouldn't enter
        # the Burger candidate pool just because it has "burger" in
        # the name.
        words = cat_key.split()
        key_noun = words[-1] if words else ""
        cust_product_type_words = [c for c in _PRODUCT_TYPE_CATS
                                    if c in cat_key]
        # Always run name-token enrichment. Cross-PRODUCT-TYPE items
        # are rejected — Pita/Grinder/Pizza items shouldn't enter the
        # Burger pool just because they have "burger" in their name
        # ("Hamburger Pita Regular", "Cheeseburger Grinder", etc.
        # are pitas/grinders, not burgers).
        if key_noun and len(key_noun) > 3:
            seen_ids = {id(it) for (_, it) in candidates_anchor}
            for menu, it in all_anchor_items:
                if id(it) in seen_ids:
                    continue
                nm = (it.get("name") or "").lower()
                cc = _normalize_cat(it.get("category") or "")
                if not (key_noun in nm or key_noun in cc):
                    continue
                # Reject anchor items in a DIFFERENT product-type
                # category. Pita-category items don't belong in the
                # Burger pool even though they have "burger" in name.
                anch_product_type_words = [
                    c for c in _PRODUCT_TYPE_CATS if c in cc
                ]
                if (anch_product_type_words
                        and not set(anch_product_type_words)
                                & set(cust_product_type_words)):
                    continue
                candidates_anchor.append((menu, it))

        if not candidates_anchor:
            continue

        # Product-similarity gate: kill the noise from price-tier
        # matches that are the wrong PRODUCT. Two real failures we saw:
        #   - "20 Pcs Wings" citing "10 Pcs" / "50 Pcs" / "Salerno
        #     Combo" because their prices happen to fall in tolerance
        #   - Specialty pizzas (Combination, Philly Steak) citing plain
        #     "Cheese Pizza Large" because the prices align
        # Both are solved by filtering candidates by name signal before
        # price-tier matching kicks in.
        cust_name = (items_in_cluster[0].get("name") or ""
                     if items_in_cluster else "")
        cust_pcs = _extract_piece_count(cust_name)
        cust_is_specialty = _is_specialty_item(cust_name, cat)
        # Specialty/plain filter fires on pizza, calzone, burger, hot
        # dog, omelet — categories where "plain base + topping
        # variants" is the real product structure.
        is_specialty_filtered = any(c in cat_key
                                     for c in _SPECIALTY_FILTERED_CATS)
        # Size-tier guard for variants: customer's "Family Size 17x24\""
        # (party tier) shouldn't be matched against anchor "Large (16
        # Slices)" (large tier) just because the prices align. Take
        # the cluster label as the customer's tier.
        cust_size_tier = _size_tier(label) if label else "unknown"

        def _product_match(it) -> bool:
            anch_name = it.get("name") or ""
            # Piece-count strict — if customer has a count, anchor must
            # have the same count. Anchors without a count are
            # rejected (they're typically combos/single-portion items
            # we don't want to compare against piece-counted wings).
            if cust_pcs is not None:
                anch_pcs = _extract_piece_count(anch_name)
                if anch_pcs != cust_pcs:
                    return False
            # Specialty/plain guard — applies to pizza, calzone,
            # burger, hot dog, omelet. Anchor classified using the
            # customer's category so the same base-name rules apply
            # to both sides.
            if is_specialty_filtered:
                anch_is_specialty = _is_specialty_item(anch_name, cat)
                if cust_is_specialty != anch_is_specialty:
                    return False
            # Size-tier guard. Allow 'unknown' (no size info in anchor
            # name) since those should still contribute. Reject
            # cross-tier matches (party vs large, small vs medium).
            if cust_size_tier != "unknown":
                anch_tier = _size_tier(anch_name)
                if anch_tier != "unknown" and anch_tier != cust_size_tier:
                    return False
            # By-the-slice guard for pizza: whole-pie customer clusters
            # shouldn't cite single-slice anchor items ('Cheese Slice',
            # 'Two Pizza Slices Lunch'). The '(N Slices)' parenthetical
            # descriptors that appear in some whole-pie names ('Pizza
            # Personal (6 Slices)') are kept — those describe the whole
            # pie's slice count, they're not by-the-slice products.
            if "pizza" in cat_key and _is_by_the_slice(anch_name):
                return False
            return True

        candidates_filtered = [
            (a, it) for (a, it) in candidates_anchor if _product_match(it)
        ]
        if not candidates_filtered:
            # No same-product anchors at all → don't fabricate sources
            # by widening to wrong products. Drop the cluster.
            continue

        # Price-tier matching with progressive widening. The "fall back
        # to all candidates" path was removed because it produced the
        # exact failure mode we just gated above.
        def _within(candidates, tol):
            return [(a, it) for (a, it) in candidates
                    if abs(int(it["price_cents"]) - cust_price) <= tol]

        matches = _within(candidates_filtered, max(int(cust_price * 0.30), 300))
        if len(matches) < 3:
            matches = _within(candidates_filtered, max(int(cust_price * 0.50), 500))
        if len(matches) < 3:
            # Try one more widening to ±100% before giving up — covers
            # markets where a customer's outlier price has limited
            # comparable anchors. Beyond this, the cluster is dropped.
            matches = _within(candidates_filtered, max(int(cust_price * 1.0), 800))

        if len(matches) < 2:
            # Below 2 product-matched anchor items: no meaningful range.
            # Skip the cluster — UI will show no market data, which is
            # truthful when we can't find peer products in the area.
            continue

        prices = [int(it["price_cents"]) for (_, it) in matches]
        low, high = min(prices), max(prices)
        median = sorted(prices)[len(prices) // 2]

        # Per-item source lists. Range is shared across the cluster
        # (all $17.95 specialty pizzas get the same low/high — that's
        # the point of clustering by price). But sources are RANKED
        # PER ITEM by name similarity to that specific item.
        #
        # Silent range vs visible sources split:
        #   The RANGE is computed from ALL `matches` (which includes
        #   name-dissimilar but price/category-comparable items, e.g.
        #   "16\" White Pizza" informing a Combination's range). That
        #   ranges in extra market signal.
        #   The DISPLAYED sources are filtered to high name-similarity
        #   only (>= NAME_SIM_THRESHOLD). Showing "16\" White Pizza"
        #   under "Combination" degrades trust — better to silently
        #   use it for the range and not display it.
        # Threshold tuned to user's "trust line": same product gets
        # cited, different specialty silently informs the range. At
        # 70+: exact name matches + abbreviations (Combo→Combination =
        # 100, Veggie Combo = 100). At 60-70: cross-specialty same
        # category (Buffalo Chicken vs Chicken Bacon Ranch = 64) —
        # excluded as displayed sources by user request.
        NAME_SIM_THRESHOLD = 70
        def _build_sources_for(item_name_local: str) -> List[Dict[str, Any]]:
            # Plain items whose name IS the category noun (e.g.
            # "Burger", "Hot Dog") strip to empty after normalization,
            # which makes name-similarity fail for everything. In
            # that case skip the similarity filter — any anchor
            # already passing category + price + specialty + size
            # filters is a valid plain-tier peer.
            cust_norm = _normalize_menu_name(item_name_local)
            cust_cat_extra = _category_type_tokens(cat)
            if not cust_norm.strip() and not cust_cat_extra:
                # Customer name + category have no distinguishing
                # tokens (e.g., "Burger" in "Burgers" — both strip to
                # nothing). Fall back to accepting all matches.
                ranked = [(100, a, it) for (a, it) in matches]
            else:
                scored = [
                    (_name_similarity(
                        item_name_local, it.get("name") or "",
                        cust_cat=cat, anch_cat=it.get("category") or ""),
                     a, it)
                    for (a, it) in matches
                ]
                ranked = [(s, a, it) for s, a, it in scored
                          if s >= NAME_SIM_THRESHOLD]
            if not ranked:
                return []
            ranked.sort(key=lambda t: (
                -t[0],
                abs(int(t[2]["price_cents"]) - cust_price),
            ))
            high = ranked  # local rename to keep diversity loop intact
            per_rest: Dict[str, int] = {}
            seen_pair: set = set()
            picked: List[tuple] = []
            for _score, a, it in high:
                rname = a["name"]
                if per_rest.get(rname, 0) >= 2:
                    continue
                # Dedupe identical (restaurant, item, price) — avoids
                # the lunch/dinner double-list seen in some menus.
                pair = (rname, (it.get("name") or "").strip().lower(),
                        int(it["price_cents"]))
                if pair in seen_pair:
                    continue
                seen_pair.add(pair)
                picked.append((a, it))
                per_rest[rname] = per_rest.get(rname, 0) + 1
                if len(picked) >= 8:
                    break
            return [{
                "restaurant": a["name"],
                "address": a.get("address", ""),
                "price_cents": int(it["price_cents"]),
                "quote": it.get("name", ""),
            } for (a, it) in picked]

        # Apply uniformly to every item in the cluster
        for item_info in items_in_cluster:
            iid = item_info["item_id"]
            sources = _build_sources_for(item_info.get("name", ""))
            mr = item_market.setdefault(iid, {
                "low": low, "high": high, "median": median,
                "sources": [], "sizes": {},
            })
            if item_info["kind"] == "variant":
                mr["sizes"][label] = {
                    "low": low, "high": high, "median": median,
                    "sources": sources,
                }
                # Stretch the base range to span all variant ranges so
                # the assessment pill (which reads from base low/high)
                # reflects the widest envelope.
                if low < mr.get("low", low):
                    mr["low"] = low
                if high > mr.get("high", high):
                    mr["high"] = high
                mr["median"] = (mr["low"] + mr["high"]) // 2
            else:
                mr["low"] = low
                mr["high"] = high
                mr["median"] = median
                mr["sources"] = sources

    log.info("Anchor aggregation: %d clusters → %d items priced",
             len(clusters), len(item_market))
    return item_market


def _aggregate_price_ranges(draft_id: int, *, force_refresh: bool = False) -> int:
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
            """SELECT r.address, r.city, r.state, r.zip_code, r.cuisine_type
               FROM restaurants r JOIN drafts d ON d.restaurant_id = r.id
               WHERE d.id = ?""",
            (draft_id,),
        ).fetchone()
        address = (rest_row["address"] or "") if rest_row else ""
        city = (rest_row["city"] or "Unknown") if rest_row else "Unknown"
        state = (rest_row["state"] or "") if rest_row else ""
        zip_code = (rest_row["zip_code"] or "") if rest_row else ""
        cuisine = (rest_row["cuisine_type"] or "restaurant") if rest_row else "restaurant"
        full_address = f"{address}, {city}, {state} {zip_code}".strip(", ")

        # Build per-item variant info for size breakdowns
        var_rows = conn.execute(
            """SELECT div.item_id, div.label, div.price_cents
               FROM draft_item_variants div
               JOIN draft_items di ON di.id = div.item_id
               WHERE di.draft_id = ? AND div.label IS NOT NULL""",
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

        # Phase 1 anchors: pull the Places-nearby competitor list to feed
        # into Gemini's prompt. These are confirmed local restaurants
        # (already filtered by distance, rating, business status) that
        # Gemini should search FIRST before broadening. Restaurant name
        # is the key field — Gemini does targeted searches like
        # "{name} {item} price" rather than open-web discovery.
        rest_id_row = conn.execute(
            "SELECT restaurant_id FROM drafts WHERE id = ?", (draft_id,),
        ).fetchone()
        # Anchors include both name AND address. The address disambiguates
        # restaurants that share names across markets — a critical
        # correctness issue ("Joe's Pizza" matches dozens otherwise).
        # When the cached entry has no address, fall back to name-only.
        competitor_anchors: List[Dict[str, str]] = []
        if rest_id_row and rest_id_row["restaurant_id"]:
            try:
                from storage.price_intel import get_cached_comparisons
                _comps = get_cached_comparisons(rest_id_row["restaurant_id"])
                competitor_anchors = [
                    {
                        "place_id": c.get("place_id"),
                        "name": c["place_name"],
                        "address": (c.get("place_address") or "").strip(),
                    }
                    for c in _comps if c.get("place_name")
                ]
                # No cap. The old 25-cap existed because anchors were
                # injected into a Gemini prompt where bloat hurt. Now
                # anchors are scrape targets, and the tiering filter
                # below drops Tier C automatically — bigger pool just
                # means more Tier A/B candidates surface.
            except Exception as e:
                log.warning("Anchor fetch failed for draft %d: %s", draft_id, e)

        # Anchor-menu aggregation: pull menus from ~5 local anchors,
        # cluster customer items by (category, size, price tier), and
        # compute each cluster's range from anchor items in the matching
        # category at a similar price tier. Replaces the prior per-item
        # grounded-search pipeline (which was non-deterministic and
        # produced different ranges for structurally identical items).
        #
        # Same 5 anchors → same ranges every time. Cited prices are real
        # items from real menus, not synthesized.
        item_market = _aggregate_from_anchor_menus(
            our_rows, item_variants, competitor_anchors,
            force_refresh=force_refresh,
        )
        if not item_market:
            log.warning(
                "Anchor aggregation returned no priced items — likely no "
                "anchor menus could be fetched. %d items will lack ranges.",
                len(haiku_items),
            )

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

    # Critical path is GEMINI-ONLY now. The 20-restaurant Google Places
    # search and the per-competitor Opus comparisons are NOT inputs to
    # Gemini — Gemini fetches its own prices via Google Search grounding.
    # Those steps power editor-side features (map pins, click-to-compare),
    # so they still run, but in a fire-and-forget background daemon AFTER
    # aggregation lands. No more 15-min Playwright menu scrapes blocking
    # the pills the user actually came here for.
    market_summary = get_market_summary(restaurant_id)
    _stub_price_intelligence(
        draft_id=draft_id,
        restaurant_id=restaurant_id,
        items=items,
        cuisine_type=cuisine_type,
        zip_code=zip_code,
        competitor_count=market_summary.get("competitor_count", 0),
    )

    # Phase 1 (Discovery): Places nearby search runs FIRST, before Gemini
    # pricing. This is the architectural unlock Gemini itself recommended:
    # instead of asking Gemini to do open-web discovery + extraction in
    # one shot, we hand it a clean list of confirmed local competitors
    # to anchor the searches against. Cuts Gemini's filtering load
    # dramatically — it's no longer wading through Yelp/articles/distant
    # chains, it's running targeted searches on known competitor names.
    try:
        from storage.price_intel import search_nearby_restaurants
        comp_data = get_cached_comparisons(restaurant_id)
        if not comp_data and user_tier == "premium":
            search_nearby_restaurants(restaurant_id, force_refresh=False)
    except Exception as e:
        log.warning(
            "Places nearby search failed for rest %d: %s",
            restaurant_id, e,
        )

    # Phase 2 (Extraction): synchronous Gemini aggregation. The Places
    # list from Phase 1 is now available to be passed into Gemini's
    # prompt as competitor anchors. Pills + price_sources land before
    # this function returns. ~30-90s for a typical menu.
    try:
        n = _aggregate_price_ranges(draft_id, force_refresh=force_refresh)
        log.info("Gemini aggregation updated %d items for draft %d", n, draft_id)
    except Exception as e:
        log.warning("Gemini aggregation failed for draft %d: %s", draft_id, e)

    # Return the freshly-aggregated results so callers see the real counts
    # instead of an empty shell.
    final = get_price_intelligence(draft_id) or {}
    return {
        "assessments": final.get("assessments", []),
        "category_avgs": final.get("category_avgs", {}),
        "market_context": {"market_tier": market_summary.get("avg_market_tier", "$$")},
        "model": _COMPARE_MODEL,
        "total_items": len(items),
        "items_assessed": final.get("items_assessed", 0),
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
