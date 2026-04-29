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
    _STOP = {"the", "and", "with", "for", "size", "small", "large",
             "medium", "regular", "personal", "mini", "pizza"}
    tokens = [t for t in re.findall(r"[a-z]+", (item_name or "").lower())
              if len(t) > 3 and t not in _STOP]
    if not tokens:
        # Fall back to ANY name token (item name was all stopwords)
        tokens = [t for t in re.findall(r"[a-z]+", (item_name or "").lower())
                  if len(t) > 2]
    if tokens and not any(t in q for t in tokens):
        return False
    return True


def _gemini_search_prices(items: List[Dict[str, Any]], city: str, state: str,
                          zip_code: str, cuisine: str,
                          address: str = "",
                          draft_id: Optional[int] = None) -> Dict[int, Dict[str, Any]]:
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
        prompt = f"""For each item below, give me a low-high price range using REAL price data from restaurants within 5 miles of {location}.

For items WITHOUT sizes, search: "(item name) (category) price near {location}"
For items WITH sizes, search EACH size separately: "(size) (item name) (category) price near {location}"

IMPORTANT: Only use restaurants within 5 miles of {location}. Do NOT include restaurants from other states or distant cities.

VERBATIM QUOTES REQUIRED — this is the most important rule. For every
single source you cite, include a "quote" field with the exact text from
the restaurant's menu page where you saw the price. The quote must
contain BOTH:
  (a) the price you're citing (e.g. "$14.99" or "14.99")
  (b) the item name or a clear synonym
If you can't quote verbatim, DO NOT include the source. We'd rather have
two real cites than ten fabricated ones.

Per-size matching: if the source's menu uses different size labels than
ours (e.g. their "Small" vs our "12 Sml"), only cite that source's price
under our size if the SOURCE's size description is in your quote and is
unambiguously the same item size. If a competitor's size doesn't map
cleanly to ours, OMIT THE SOURCE for that size — don't approximate.

Items:
{item_lines}

Return JSON only — an array:
[{{"id": 123, "low_cents": 800, "high_cents": 1400, "median_cents": 1100, "sizes": null,
   "sources": [
     {{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "Cheese Pizza  $8.99"}},
     {{"restaurant": "Main St Pizzeria", "price_cents": 1200, "quote": "Cheese Pie - Large $12.00"}}
   ]
}}]

For items with [sizes], include per-size ranges AND sources per size:
{{"id": 123, "low_cents": 800, "high_cents": 2500, "median_cents": 1500,
  "sources": [{{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "Cheese Pizza  $8.99"}}],
  "sizes": {{"12\\" Sml": {{"low_cents": 800, "high_cents": 1400, "median_cents": 1100,
    "sources": [
      {{"restaurant": "Joe's Pizza", "price_cents": 899, "quote": "12\\" Cheese Pizza - $8.99"}},
      {{"restaurant": "Main St Pizzeria", "price_cents": 1200, "quote": "Small (12 inch) cheese - $12.00"}}
    ]
  }}}}
}}

Rules:
- Use real price data from restaurants within 5 miles of {location}
- Every source MUST have a verbatim "quote" field — no quote, no source
- Prices in US cents (e.g. $9.00 = 900)
- low_cents and high_cents MUST be different — if you only find one price, widen the range by +/- 15%
- If you can't find verifiable real data for an item, set low_cents to 0 and omit sources
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
            quote_filter_stats = {"kept": 0, "no_quote": 0, "bad_quote": 0}

            def _filter_sources(raw_sources, item_name, ctx):
                """Drop sources without a verifiable verbatim quote."""
                if not isinstance(raw_sources, list):
                    return []
                out = []
                for s in raw_sources:
                    if not isinstance(s, dict):
                        continue
                    rest = s.get("restaurant") or ""
                    if not rest or _is_article_title(rest):
                        continue
                    quote = (s.get("quote") or "").strip()
                    price = int(s.get("price_cents") or 0)
                    if not quote:
                        quote_filter_stats["no_quote"] += 1
                        continue
                    if not _quote_validates_price(quote, price, item_name):
                        quote_filter_stats["bad_quote"] += 1
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
                    # Drop sources without verifiable verbatim quotes.
                    # Day 141.9: Gemini's grounded search frequently fabricates
                    # plausible-looking prices when it can't find an exact size
                    # match; the quote field forces it to back each cite with
                    # actual page text or omit the source.
                    base_sources = _filter_sources(r.get("sources"), item_name, "base")
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
                                    )
                                    # If a size has zero validated sources,
                                    # drop the size entirely — the range it
                                    # claims isn't backed by anything we can
                                    # verify.
                                    if not size_sources:
                                        continue
                                    size_entry["sources"] = size_sources
                                    sizes_out[slabel] = size_entry
                        if sizes_out:
                            entry["sizes"] = sizes_out

                    # If neither base nor any size has validated sources, drop
                    # the whole item — there's no real data here.
                    has_base = bool(entry.get("sources"))
                    has_size = bool(entry.get("sizes"))
                    if not has_base and not has_size:
                        continue

                    batch_out[iid] = entry

            if quote_filter_stats["no_quote"] or quote_filter_stats["bad_quote"]:
                log.info(
                    "Quote filter: kept=%d, no_quote=%d, bad_quote=%d",
                    quote_filter_stats["kept"],
                    quote_filter_stats["no_quote"],
                    quote_filter_stats["bad_quote"],
                )
            log.info(
                "Gemini batch ok: %d/%d items returned (ids=%s)",
                len(batch_out), len(batch), batch_ids,
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

        # Gemini with Google Search grounding — REAL prices from REAL menus.
        # Gemini is the product. Haiku is an emergency fallback that should
        # almost never run. The flow:
        #   Pass 1: Batched Gemini (~10 items per call, parallel)
        #   Pass 2: Per-item Gemini retry for what Pass 1 missed (smaller
        #           prompts, far higher per-call success rate)
        #   Pass 3: Haiku estimates ONLY for what's still missing — logged
        #           loudly so this can be investigated.
        item_market = _gemini_search_prices(
            haiku_items, city, state, zip_code, cuisine,
            address=full_address, draft_id=draft_id)

        # Circuit breaker: if more than half the batched calls failed,
        # Gemini is in a degraded state (sustained 503s, regional outage,
        # whatever). Per-item retries against a degraded Gemini are pure
        # waste — each one will exhaust the same 9-attempt fallback chain
        # we already ran, just smaller. Bail to Haiku immediately so the
        # user sees pills in seconds instead of an hour-long grind.
        batches_total = _GEMINI_LAST_RUN.get("batches_total", 0) or 1
        batches_failed = _GEMINI_LAST_RUN.get("batches_failed", 0)
        # Tightened from 50% to 25% — once a quarter of batches are failing,
        # per-item retries will only make the throttling worse. Bail to Haiku.
        gemini_degraded = batches_failed >= max(1, batches_total // 4)

        missing = [it for it in haiku_items if it["item_id"] not in item_market]
        if missing and gemini_degraded:
            log.warning(
                "Gemini circuit breaker open: %d/%d batches failed — skipping "
                "per-item retry, going straight to Haiku for %d missing items",
                batches_failed, batches_total, len(missing),
            )
        elif missing:
            log.warning(
                "Gemini batched run missed %d/%d items — running per-item retry pass",
                len(missing), len(haiku_items),
            )
            # Per-item retry: 1 item per call so each Gemini request has a
            # tiny prompt and fast response. Items that fail inside a
            # 10-item batch (deadline, parse error on huge response) often
            # succeed when asked individually. Cap concurrency at 2 so per-
            # item retries don't themselves overload Gemini.
            #
            # Hard wall-clock cap: 120s. If Gemini degrades partway through,
            # we don't want to grind on it forever. Anything not recovered
            # in that window falls through to Haiku.
            PER_ITEM_BUDGET_S = 120
            from concurrent.futures import ThreadPoolExecutor
            per_item: Dict[int, Dict[str, Any]] = {}
            _retry_t0 = time.time()
            def _one(it):
                return _gemini_search_prices(
                    [it], city, state, zip_code, cuisine,
                    address=full_address, draft_id=draft_id,
                )
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_one, it) for it in missing]
                for fut in futures:
                    elapsed = time.time() - _retry_t0
                    remaining = PER_ITEM_BUDGET_S - elapsed
                    if remaining <= 0:
                        log.warning(
                            "Per-item Gemini retry hit %ds budget — abandoning "
                            "remaining %d futures, falling to Haiku",
                            PER_ITEM_BUDGET_S,
                            sum(1 for f in futures if not f.done()),
                        )
                        for f in futures:
                            f.cancel()
                        break
                    try:
                        per_item.update(fut.result(timeout=remaining))
                    except Exception as e:
                        log.error("Per-item Gemini retry raised: %s: %s", type(e).__name__, e)
            recovered = {iid: d for iid, d in per_item.items() if iid not in item_market}
            if recovered:
                log.info("Per-item Gemini retry recovered %d items in %.1fs",
                         len(recovered), time.time() - _retry_t0)
                item_market.update(recovered)

        still_missing = [it for it in haiku_items if it["item_id"] not in item_market]
        if still_missing:
            # Haiku — last resort. Loud warning because this means Gemini
            # genuinely couldn't deliver and the user is getting estimated
            # prices instead of real local prices for these items.
            log.warning(
                "GEMINI FALLBACK: using Haiku estimates for %d/%d items "
                "(Gemini exhausted after batch + per-item retries). "
                "These items will lack real source citations.",
                len(still_missing), len(haiku_items),
            )
            fallback = _estimate_item_market_rates(still_missing, city, state, zip_code, cuisine)
            for iid, data in fallback.items():
                if iid not in item_market:
                    item_market[iid] = data
        else:
            log.info(
                "Gemini delivered 100%% coverage: %d/%d items, no Haiku fallback needed",
                len(item_market), len(haiku_items),
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

    # THE PRODUCT: synchronous Gemini aggregation. Pills + price_sources
    # land before this function returns. ~30-90s for a typical menu.
    try:
        n = _aggregate_price_ranges(draft_id)
        log.info("Gemini aggregation updated %d items for draft %d", n, draft_id)
    except Exception as e:
        log.warning("Gemini aggregation failed for draft %d: %s", draft_id, e)

    # Background: only the Google Places nearby search. Cheap (~30s, single
    # API call) and gives the editor map its 20 nearby-restaurant pins for
    # the "similar restaurants" sidebar. Per-competitor Opus comparisons
    # used to run here too but were deleted (Day 141.9): they computed
    # data the editor UI never displayed, burned ~$6-10 per run, and the
    # daemon's 10-25 min runtime created CPU contention with editor renders.
    def _background_enrichment(_rid=restaurant_id, _tier=user_tier):
        try:
            from storage.price_intel import search_nearby_restaurants
            comp_data = get_cached_comparisons(_rid)
            if not comp_data and _tier == "premium":
                search_nearby_restaurants(_rid, force_refresh=False)
        except Exception as e:
            log.warning("Background Places enrichment failed for rest %d: %s", _rid, e)
    import threading as _threading
    _threading.Thread(target=_background_enrichment, daemon=True).start()

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
