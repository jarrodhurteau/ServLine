# storage/price_intel.py
"""
Google Places API integration for price comparison intelligence.

Searches for comparable restaurants near the customer's location by cuisine
type + zip code.  Results are cached in a local SQLite table to avoid
duplicate API calls.

Usage:
    from storage.price_intel import search_nearby_restaurants

    results = search_nearby_restaurants(restaurant_id, force_refresh=False)

Requires GOOGLE_PLACES_API_KEY in environment (loaded via .env).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Config
# -------------------------------------------------------------------
DB_PATH = Path(__file__).resolve().parents[1] / "storage" / "servline.db"

# Cache TTL — don't re-query the same zip+cuisine combo within 30 days
CACHE_TTL_DAYS = 30

# Max nearby restaurants to store per search (closest 10 of matching cuisine)
MAX_RESULTS = 10

# Google Places API radius (meters).  ~8 km ≈ 5 miles.
# Note: unused when rankby=distance, kept for reference / future fallback.
SEARCH_RADIUS_METERS = 8000

# Rate limiting: max API calls per minute
RATE_LIMIT_PER_MINUTE = 10
_call_timestamps: list[float] = []

# Cuisine type → Google Places search keyword

# Google price_level mapping → display labels
PRICE_LEVEL_LABELS = {
    0: "Free",
    1: "$",
    2: "$$",
    3: "$$$",
    4: "$$$$",
}


# -------------------------------------------------------------------
# DB helpers (same pattern as drafts.py)
# -------------------------------------------------------------------
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_schema() -> None:
    """Create the price_comparison_cache table if it doesn't exist."""
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_comparison_cache (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                zip_code      TEXT    NOT NULL,
                cuisine_type  TEXT    NOT NULL,
                results_json  TEXT    NOT NULL,
                result_count  INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL,
                expires_at    TEXT    NOT NULL,
                UNIQUE(zip_code, cuisine_type)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_comparison_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                restaurant_id   INTEGER NOT NULL,
                cache_id        INTEGER NOT NULL REFERENCES price_comparison_cache(id) ON DELETE CASCADE,
                place_id        TEXT,
                place_name      TEXT    NOT NULL,
                place_address   TEXT,
                price_level     INTEGER,
                price_label     TEXT,
                rating          REAL,
                user_ratings    INTEGER,
                cuisine_match   TEXT,
                latitude        REAL,
                longitude       REAL,
                created_at      TEXT    NOT NULL
            )
        """)
        conn.commit()


# Run on import so the tables exist
_ensure_schema()


# -------------------------------------------------------------------
# Rate limiting
# -------------------------------------------------------------------
def _check_rate_limit() -> None:
    """Raise RuntimeError if we've exceeded the per-minute rate limit."""
    now = time.time()
    cutoff = now - 60
    # Prune old timestamps
    while _call_timestamps and _call_timestamps[0] < cutoff:
        _call_timestamps.pop(0)
    if len(_call_timestamps) >= RATE_LIMIT_PER_MINUTE:
        raise RuntimeError(
            f"Google Places API rate limit exceeded ({RATE_LIMIT_PER_MINUTE}/min). "
            "Try again shortly."
        )


def _record_api_call() -> None:
    _call_timestamps.append(time.time())


# -------------------------------------------------------------------
# Google Places API
# -------------------------------------------------------------------
def _get_api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "GOOGLE_PLACES_API_KEY not set. "
            "Add it to your .env file to enable price comparison."
        )
    return key


def _geocode_zip(zip_code: str, api_key: str) -> Optional[Dict[str, float]]:
    """Convert a US zip code to lat/lng via Google Geocoding API.

    Accepts ZIP+4 ("01001-2543") — strips the suffix so the 5-digit base
    is used (Google's geocoder tolerates both, but the 5-digit form is
    also used as the cache key, so we normalize here).
    """
    zip5 = (zip_code or "").strip().split("-", 1)[0]
    params = urllib.parse.urlencode({
        "address": zip5,
        "components": "country:US",
        "key": api_key,
    })
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ServLine/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "OK" or not data.get("results"):
            log.warning("Geocode failed for zip %s: %s", zip_code, data.get("status"))
            return None
        loc = data["results"][0]["geometry"]["location"]
        return {"lat": loc["lat"], "lng": loc["lng"]}
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as exc:
        log.error("Geocode error for zip %s: %s", zip_code, exc)
        return None


def _search_nearby(
    lat: float,
    lng: float,
    keyword: str,
    api_key: str,
) -> List[Dict[str, Any]]:
    """Search Google Places Nearby for restaurants matching keyword.

    Uses rankby=distance so the top N are the closest matches. We trust
    Google's keyword match — whatever it returns first is what the user
    sees. No post-filtering; simpler and less surprising.
    """
    params = urllib.parse.urlencode({
        "location": f"{lat},{lng}",
        "rankby": "distance",
        "keyword": keyword,
        "type": "restaurant",
        "key": api_key,
    })
    url = f"https://maps.googleapis.com/maps/api/place/nearbysearch/json?{params}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ServLine/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            log.warning("Places search failed: %s", data.get("status"))
            return []
        results = []
        for place in data.get("results", [])[:MAX_RESULTS]:
            results.append({
                "place_id": place.get("place_id"),
                "name": place.get("name", "Unknown"),
                "address": place.get("vicinity", ""),
                "price_level": place.get("price_level"),
                "price_label": PRICE_LEVEL_LABELS.get(place.get("price_level"), "N/A"),
                "rating": place.get("rating"),
                "user_ratings_total": place.get("user_ratings_total", 0),
                "lat": place.get("geometry", {}).get("location", {}).get("lat"),
                "lng": place.get("geometry", {}).get("location", {}).get("lng"),
                "types": place.get("types", []),
            })
        return results
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        log.error("Places search error: %s", exc)
        return []


# -------------------------------------------------------------------
# Cache layer
# -------------------------------------------------------------------
def _get_cached(zip_code: str, cuisine_type: str) -> Optional[Dict[str, Any]]:
    """Return cached results if they exist and haven't expired."""
    with db_connect() as conn:
        row = conn.execute(
            """SELECT id, results_json, result_count, created_at, expires_at
               FROM price_comparison_cache
               WHERE zip_code = ? AND cuisine_type = ?""",
            (zip_code, cuisine_type),
        ).fetchone()
    if not row:
        return None
    if row["expires_at"] < _now():
        # Expired — delete and return None
        with db_connect() as conn:
            conn.execute("DELETE FROM price_comparison_cache WHERE id = ?", (row["id"],))
            conn.commit()
        return None
    return {
        "cache_id": row["id"],
        "results": json.loads(row["results_json"]),
        "result_count": row["result_count"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "from_cache": True,
    }


def _store_cache(
    zip_code: str,
    cuisine_type: str,
    results: List[Dict[str, Any]],
    restaurant_id: int,
) -> int:
    """Store search results in cache.  Returns cache row id."""
    now = _now()
    expires = (datetime.utcnow() + timedelta(days=CACHE_TTL_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with db_connect() as conn:
        # Upsert: replace if exists
        conn.execute(
            """INSERT INTO price_comparison_cache
               (zip_code, cuisine_type, results_json, result_count, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(zip_code, cuisine_type)
               DO UPDATE SET results_json = excluded.results_json,
                             result_count = excluded.result_count,
                             created_at   = excluded.created_at,
                             expires_at   = excluded.expires_at""",
            (zip_code, cuisine_type, json.dumps(results), len(results), now, expires),
        )
        cache_id = conn.execute(
            "SELECT id FROM price_comparison_cache WHERE zip_code = ? AND cuisine_type = ?",
            (zip_code, cuisine_type),
        ).fetchone()["id"]
        # Clear old detail rows and re-insert
        conn.execute(
            "DELETE FROM price_comparison_results WHERE cache_id = ?", (cache_id,)
        )
        for r in results:
            conn.execute(
                """INSERT INTO price_comparison_results
                   (restaurant_id, cache_id, place_id, place_name, place_address,
                    price_level, price_label, rating, user_ratings, cuisine_match,
                    latitude, longitude, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    restaurant_id,
                    cache_id,
                    r.get("place_id"),
                    r["name"],
                    r.get("address"),
                    r.get("price_level"),
                    r.get("price_label"),
                    r.get("rating"),
                    r.get("user_ratings_total", 0),
                    cuisine_type,
                    r.get("lat"),
                    r.get("lng"),
                    now,
                ),
            )
        conn.commit()
    return cache_id


# -------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------
def search_nearby_restaurants(
    restaurant_id: int,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Search for comparable restaurants near the given restaurant's location.

    Returns dict with:
        results: list of nearby restaurant dicts
        result_count: int
        from_cache: bool
        zip_code, cuisine_type: search params used
        error: str (only if something went wrong)
    """
    from storage.users import get_restaurant  # lazy to avoid circular

    restaurant = get_restaurant(restaurant_id)
    if not restaurant:
        return {"error": "Restaurant not found", "results": [], "result_count": 0}

    # Normalize ZIP+4 ("01001-2543") to 5-digit form for consistent cache keys
    zip_code = (restaurant.get("zip_code") or "").strip().split("-", 1)[0]
    cuisine_type = (restaurant.get("cuisine_type") or "other").strip().lower()

    if not zip_code:
        return {
            "error": "Restaurant has no zip code. Please update the restaurant profile.",
            "results": [],
            "result_count": 0,
            "zip_code": "",
            "cuisine_type": cuisine_type,
        }

    # Check cache first (unless force refresh)
    if not force_refresh:
        cached = _get_cached(zip_code, cuisine_type)
        if cached:
            log.info(
                "Price intel cache hit: zip=%s cuisine=%s (%d results)",
                zip_code, cuisine_type, cached["result_count"],
            )
            cached["zip_code"] = zip_code
            cached["cuisine_type"] = cuisine_type
            return cached

    # Live API call
    try:
        api_key = _get_api_key()
    except RuntimeError as exc:
        return {
            "error": str(exc),
            "results": [],
            "result_count": 0,
            "zip_code": zip_code,
            "cuisine_type": cuisine_type,
        }

    _check_rate_limit()

    # Step 1: geocode zip
    location = _geocode_zip(zip_code, api_key)
    if not location:
        return {
            "error": f"Could not geocode zip code '{zip_code}'.",
            "results": [],
            "result_count": 0,
            "zip_code": zip_code,
            "cuisine_type": cuisine_type,
        }
    _record_api_call()

    # Step 2: nearby search
    # Day 141.7: build the keyword straight from the user's cuisine.
    # Google's keyword match is loose — feeding "diner" or "chinese" or
    # whatever the owner typed is more reliable than a hand-maintained
    # lookup table that drifts out of date.
    cuisine_clean = (cuisine_type or "").strip().lower()
    if cuisine_clean in ("", "other"):
        keyword = "restaurant"
    else:
        keyword = f"{cuisine_clean} restaurant"
    _check_rate_limit()
    results = _search_nearby(location["lat"], location["lng"], keyword, api_key)
    _record_api_call()

    # Step 3: cache results
    cache_id = _store_cache(zip_code, cuisine_type, results, restaurant_id)
    log.info(
        "Price intel fresh search: zip=%s cuisine=%s → %d results (cache_id=%d)",
        zip_code, cuisine_type, len(results), cache_id,
    )

    return {
        "cache_id": cache_id,
        "results": results,
        "result_count": len(results),
        "from_cache": False,
        "zip_code": zip_code,
        "cuisine_type": cuisine_type,
    }


def get_cached_comparisons(restaurant_id: int) -> List[Dict[str, Any]]:
    """Return all cached comparison results for a restaurant."""
    with db_connect() as conn:
        rows = conn.execute(
            """SELECT place_id, place_name, place_address, price_level, price_label,
                      rating, user_ratings, cuisine_match, latitude, longitude
               FROM price_comparison_results
               WHERE restaurant_id = ?
               ORDER BY rating DESC NULLS LAST""",
            (restaurant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_market_summary(restaurant_id: int) -> Dict[str, Any]:
    """
    Compute a market summary from cached comparison data.
    Returns avg rating, price tier distribution, and competitor count.
    """
    comps = get_cached_comparisons(restaurant_id)
    if not comps:
        return {
            "competitor_count": 0,
            "avg_rating": None,
            "price_distribution": {},
            "has_data": False,
        }
    ratings = [c["rating"] for c in comps if c["rating"] is not None]
    price_dist: Dict[str, int] = {}
    for c in comps:
        label = c.get("price_label") or "N/A"
        price_dist[label] = price_dist.get(label, 0) + 1

    return {
        "competitor_count": len(comps),
        "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
        "price_distribution": price_dist,
        "has_data": True,
    }


def clear_cache(zip_code: Optional[str] = None, cuisine_type: Optional[str] = None) -> int:
    """Clear cache entries.  Returns number of rows deleted."""
    with db_connect() as conn:
        if zip_code and cuisine_type:
            cur = conn.execute(
                "DELETE FROM price_comparison_cache WHERE zip_code = ? AND cuisine_type = ?",
                (zip_code, cuisine_type),
            )
        elif zip_code:
            cur = conn.execute(
                "DELETE FROM price_comparison_cache WHERE zip_code = ?", (zip_code,)
            )
        else:
            cur = conn.execute("DELETE FROM price_comparison_cache")
        conn.commit()
    return cur.rowcount


# -------------------------------------------------------------------
# Competitor Menu Scraping (Day 141.5)
# -------------------------------------------------------------------

def _ensure_menu_scrape_schema() -> None:
    """Create table for caching scraped competitor menus."""
    with db_connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_menus (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                place_id        TEXT NOT NULL UNIQUE,
                place_name      TEXT NOT NULL,
                website_url     TEXT,
                menu_url        TEXT,
                menu_items      TEXT,
                item_count      INTEGER DEFAULT 0,
                scrape_status   TEXT NOT NULL DEFAULT 'pending',
                error_message   TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                expires_at      TEXT
            )
        """)
        # Migration: add expires_at if missing
        try:
            conn.execute("ALTER TABLE competitor_menus ADD COLUMN expires_at TEXT")
        except Exception:
            pass  # column already exists
        conn.commit()


_MENU_CACHE_TTL_DAYS = 30

_ensure_menu_scrape_schema()


def get_place_details(place_id: str) -> Optional[Dict[str, Any]]:
    """Fetch website URL and other details from Google Place Details API."""
    try:
        api_key = _get_api_key()
    except RuntimeError:
        return None

    params = urllib.parse.urlencode({
        "place_id": place_id,
        "fields": "name,website,url,formatted_phone_number",
        "key": api_key,
    })
    url = f"https://maps.googleapis.com/maps/api/place/details/json?{params}"
    try:
        _check_rate_limit()
        _record_api_call()
        req = urllib.request.Request(url, headers={"User-Agent": "ServLine/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        if data.get("status") != "OK":
            log.warning("Place Details failed for %s: %s", place_id, data.get("status"))
            return None
        result = data.get("result", {})
        return {
            "name": result.get("name"),
            "website": result.get("website"),
            "google_url": result.get("url"),
            "phone": result.get("formatted_phone_number"),
        }
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        log.error("Place Details error: %s", exc)
        return None


def _find_menu_url(website_url: str) -> Optional[str]:
    """Try to find a menu page on the competitor's website."""
    if not website_url:
        return None

    import re as _re

    # Ordering platforms that often host the real menu
    ORDERING_PLATFORMS = [
        "getsauce.com", "chownow.com", "toasttab.com", "order.online",
        "slicelife.com", "order.kounta.com", "popmenu.com",
        "ordering.app", "bfrk.com", "square.site",
    ]

    # Common menu URL patterns to try directly
    menu_paths = ["/menu", "/our-menu", "/food-menu", "/lunch-menu",
                  "/dinner-menu", "/menus", "/food", "/eat"]

    # First check the homepage for menu links or content
    try:
        # Use Playwright for the homepage to catch JS-rendered content
        html = _fetch_page_content(website_url)
        if not html:
            return None

        # If homepage has many price indicators, it might BE the menu
        price_hits = len(_re.findall(r'\$\d+\.?\d{0,2}', html))
        if price_hits > 10:
            return website_url

        # Collect ALL links from the page
        all_links = _re.findall(r'href=["\']([^"\']+)["\']', html, _re.IGNORECASE)

        # Priority 1: Check for ordering platform links (these always have full menus)
        for link in all_links:
            link_clean = link.replace("&amp;", "&")
            link_lower = link_clean.lower()
            if any(platform in link_lower for platform in ORDERING_PLATFORMS):
                if 'menu' in link_lower or 'order' in link_lower:
                    log.info("Found ordering platform menu: %s", link_clean)
                    return link_clean

        # Priority 2: Menu links on the site itself
        menu_candidates = []
        for link in all_links:
            link_lower = link.lower()
            if any(x in link_lower for x in ['#', 'javascript:', 'mailto:', 'facebook',
                                               'instagram', 'twitter', '.jpg', '.png']):
                continue
            if 'menu' in link_lower or 'food' in link_lower or 'order' in link_lower:
                menu_candidates.append(link)

        base = website_url.rstrip("/")
        for link in menu_candidates[:5]:
            if link.startswith("http"):
                full_url = link
            elif link.startswith("/"):
                full_url = base + link
            else:
                full_url = base + "/" + link
            try:
                inner_html = _fetch_page_content(full_url)
                if inner_html:
                    inner_prices = len(_re.findall(r'\$\d+\.?\d{0,2}', inner_html))
                    if inner_prices > 5:
                        return full_url
            except Exception:
                continue

    except Exception as e:
        log.debug("Failed to fetch %s: %s", website_url, e)

    # Try common menu paths directly
    base = website_url.rstrip("/")
    for path in menu_paths:
        test_url = base + path
        try:
            inner_html = _fetch_page_content(test_url)
            if inner_html:
                inner_prices = len(_re.findall(r'\$\d+\.?\d{0,2}', inner_html))
                if inner_prices > 3:
                    return test_url
        except Exception:
            continue

    return None


def _fetch_page_content(url: str) -> Optional[str]:
    """Fetch a webpage and return its HTML content.
    First tries a simple HTTP fetch. If the page lacks prices
    (likely JS-rendered), falls back to Playwright headless browser.
    """
    import re as _re
    html = _fetch_page_simple(url)
    if html:
        price_count = len(_re.findall(r"\$\d+\.?\d{0,2}", html))
        # Simple fetch found prices — no need for Playwright
        if price_count > 3:
            return html
    # Either simple fetch failed OR returned no prices — try Playwright
    pw_html = _fetch_page_playwright(url)
    if pw_html:
        return pw_html
    # Fall back to whatever simple fetch got (might still be useful)
    return html


def _fetch_page_simple(url: str) -> Optional[str]:
    """Simple HTTP fetch (no JS rendering)."""
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return None
            raw = resp.read()
            if len(raw) > 500_000:
                raw = raw[:500_000]
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        log.debug("Simple fetch failed for %s: %s", url, e)
        return None


def _fetch_page_playwright(url: str) -> Optional[str]:
    """Fetch a JS-rendered page using Playwright headless browser.
    Returns the full rendered HTML after JavaScript execution.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.debug("Playwright not installed, skipping JS rendering for %s", url)
        return None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page.goto(url, timeout=25000, wait_until="networkidle")
            # Wait for dynamic content to render
            page.wait_for_timeout(3000)
            # Try to get the visible text content (better than raw HTML for menus)
            # This captures JS-rendered prices that aren't in the source HTML
            html = page.content()
            browser.close()
            if len(html) > 500_000:
                html = html[:500_000]
            return html
    except Exception as e:
        log.debug("Playwright fetch failed for %s: %s", url, e)
        return None


def _extract_menu_text(html: str) -> str:
    """Strip HTML to get clean text content for Claude to analyze."""
    import re
    # Remove script/style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Remove nav/header/footer blocks (often noise)
    text = re.sub(r"<(?:nav|header|footer)[^>]*>.*?</(?:nav|header|footer)>", "",
                  text, flags=re.DOTALL | re.IGNORECASE)
    # Replace tags with spaces
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#36;", "$").replace("&dollar;", "$")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Cap at 8000 chars for the Claude prompt
    if len(text) > 8000:
        text = text[:8000]
    return text


# Known spam/parked domain patterns
_BAD_URL_PATTERNS = [
    "netguard-app.com", "promo.", "click.", "redirect.", "parked",
    "godaddy.com/park", "sedoparking.com", "bodis.com", "hugedomains.com",
    "afternic.com", "dan.com", "undeveloped.com", "security-check",
    "captcha", "i-m-a-human", "verify-human",
]


def _validate_restaurant_url(url: str) -> Optional[str]:
    """Check if a restaurant URL is legitimate (not spam/parked/redirect).
    Pattern-only check — no network call (that happens in _fetch_page_content).
    """
    if not url:
        return None
    url_lower = url.lower()
    for pattern in _BAD_URL_PATTERNS:
        if pattern in url_lower:
            log.debug("Filtered bad URL: %s (matched: %s)", url, pattern)
            return None
    return url


def _try_allmenus(place_name: str) -> Optional[str]:
    """Try to find the restaurant on allmenus.com."""
    import re as _re
    try:
        # Search allmenus.com
        slug = _re.sub(r"[^a-z0-9]+", "-", place_name.lower()).strip("-")
        # Try the search page
        search_url = f"https://www.allmenus.com/results/-/{urllib.parse.quote(place_name)}/"
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Find restaurant links
        links = _re.findall(r'href="(https://www\.allmenus\.com/[^"]+/menu/)"', html)
        # Pick the first one that has the restaurant name in it
        name_parts = place_name.lower().split()
        for link in links:
            link_lower = link.lower()
            if any(part in link_lower for part in name_parts if len(part) > 2):
                # Verify it has menu content
                inner = _fetch_page_content(link)
                if inner:
                    import re
                    prices = len(re.findall(r"\$\d+\.?\d{0,2}", inner))
                    if prices > 5:
                        return link
        return None
    except Exception as e:
        log.debug("allmenus search failed for %s: %s", place_name, e)
        return None


def _try_menupages(place_name: str) -> Optional[str]:
    """Try to find the restaurant on menupages.com."""
    import re as _re
    try:
        search_url = (
            f"https://www.menupages.com/restaurants/?q={urllib.parse.quote(place_name)}"
        )
        req = urllib.request.Request(search_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        links = _re.findall(r'href="(/restaurants/[^"]+)"', html)
        name_parts = place_name.lower().split()
        for link in links:
            link_lower = link.lower()
            if any(part in link_lower for part in name_parts if len(part) > 2):
                full_url = f"https://www.menupages.com{link}"
                inner = _fetch_page_content(full_url)
                if inner:
                    import re
                    prices = len(re.findall(r"\$\d+\.?\d{0,2}", inner))
                    if prices > 5:
                        return full_url
        return None
    except Exception as e:
        log.debug("menupages search failed for %s: %s", place_name, e)
        return None


def scrape_competitor_menu(
    place_id: str,
    place_name: str,
    *,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Get a competitor's real menu prices using Claude web search.
    Claude searches Grubhub, DoorDash, MenuPages, Yelp, restaurant websites,
    and any other source to find actual menu items with real prices.

    Results are cached per place_id.
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # Check cache first
    if not force_refresh:
        cached = _get_cached_menu(place_id)
        if cached:
            return cached

    # Get address for search context
    address = ""
    try:
        with db_connect() as conn:
            row = conn.execute(
                "SELECT place_address FROM price_comparison_results WHERE place_id = ?",
                (place_id,),
            ).fetchone()
            if row:
                address = row["place_address"] or ""
    except Exception:
        pass

    # Claude web search — finds real prices from any online source
    log.info("Searching for %s menu via Claude web search", place_name)
    items = _claude_web_search_menu(place_name, address)
    if items:
        items = [it for it in items if it.get("price_cents") and it["price_cents"] > 0]

    # Day 141.7: classify roles + canonical names so downstream can skip
    # sauce ramekins and collapse size variants. Safe to skip on failure —
    # consumers fall back to raw items when role is None.
    if items:
        try:
            from .menu_classifier import classify_menu_items
            items = classify_menu_items(place_name, items)
            log.info("Classified %d items for %s", len(items), place_name)
        except Exception as e:
            log.warning("Classifier failed for %s: %s", place_name, e)

    if items:
        _save_menu_cache(place_id, place_name, None, None, items,
                         "web_search", None, now)
        return {
            "place_id": place_id,
            "place_name": place_name,
            "menu_url": None,
            "items": items,
            "source": "scraped",
            "from_cache": False,
        }

    _save_menu_cache(place_id, place_name, None, None, [],
                     "not_found", "No menu found via web search", now)
    return {
        "place_id": place_id,
        "place_name": place_name,
        "menu_url": None,
        "items": [],
        "source": "not_found",
        "from_cache": False,
    }


def _claude_extract_competitor_menu(place_name: str, menu_text: str) -> List[Dict[str, Any]]:
    """Use Claude to extract menu items and prices from scraped text."""
    try:
        from .ai_menu_extract import _get_client
        client = _get_client()
        if not client:
            return []
    except Exception:
        return []

    prompt = f"""\
Extract all menu items with prices from this restaurant's menu text.
Restaurant: {place_name}

MENU TEXT:
{menu_text}

Return a JSON array of menu items. Each item should have:
- "name": the item name (string)
- "price_cents": price in cents (integer, e.g. $12.99 = 1299). Use 0 if no price shown.
- "category": the menu section/category this item belongs to (string)

RULES:
- Return ONLY a valid JSON array, no markdown fencing.
- Include ALL items that have a recognizable name, even if price is missing.
- Skip headers, descriptions, and non-food items.
- If a price range is shown (e.g. "$12-15"), use the lower price.
- If sizes are listed (S/M/L), create one entry with the base/smallest price.
- Be thorough — extract every item you can find."""

    return _claude_parse_menu_response(client, prompt, place_name)


def _claude_web_search_menu(place_name: str, place_address: str) -> List[Dict[str, Any]]:
    """Find competitor menu items + prices.

    Flow (Day 141.7):
      1. Claude web search finds delivery-platform URLs (DoorDash, Grubhub, ...)
      2. DoorDash / Grubhub URLs → Apify actor (reliable, structured)
      3. Other URLs (restaurant's own site, ChowNow, etc.) → existing
         JSON-LD / __NEXT_DATA__ structured extraction
      4. Nothing found → empty list (no Claude web-search price extraction —
         too unreliable and expensive)
    """
    try:
        from .ai_menu_extract import _get_client
        client = _get_client()
        if not client:
            return []
    except Exception:
        return []

    # Step 1: Ask Claude to find up to 3 delivery-platform URLs (cascade)
    url_prompt = f"""\
Find menu URLs for "{place_name}" at {place_address}.

Prefer in this order:
  1. The restaurant's OWN website with the menu page
     (e.g. https://casadilisa.com/dinner-menu/)
  2. A DoorDash store page (https://www.doordash.com/store/...)
  3. A Grubhub restaurant page (https://www.grubhub.com/restaurant/...)
  4. Any aggregator with their full menu (Slice, ChowNow, Yelp menu tab, etc.)

Return up to 4 URLs, one per line, in preference order. No commentary.
If nothing found, return exactly: NOT_FOUND"""

    menu_urls: List[str] = []
    try:
        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=500,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 3,
            }],
            messages=[{"role": "user", "content": url_prompt}],
        )
        import re as _re
        seen = set()
        for block in resp.content:
            if hasattr(block, "text") and block.text.strip():
                for url in _re.findall(r'https?://[^\s<>"\']+', block.text):
                    # Strip trailing punctuation Claude sometimes includes
                    url = url.rstrip(".,);]'\"")
                    if url not in seen:
                        seen.add(url)
                        menu_urls.append(url)
        if menu_urls:
            log.info("Found %d candidate URL(s) for %s", len(menu_urls), place_name)
    except Exception as e:
        log.warning("URL search failed for %s: %s", place_name, e)

    try:
        from .apify_client import (
            scrape_menu_by_url,
            scrape_google_menu_panel,
            scrape_menus_r_us_by_search,
            is_configured as apify_ok,
        )
    except Exception:
        apify_ok = lambda: False  # noqa: E731
        scrape_menu_by_url = None  # type: ignore
        scrape_google_menu_panel = None  # type: ignore
        scrape_menus_r_us_by_search = None  # type: ignore

    # Step 2: Google menu panel first — highest-coverage source. Google
    # aggregates menus from Single Platform + restaurant websites into
    # the knowledge panel; we parse that page via menus-r-us.
    if apify_ok() and scrape_google_menu_panel:
        location = place_address or ""
        items = scrape_google_menu_panel(place_name, location)
        if items:
            log.info("Google menu panel extracted %d items for %s", len(items), place_name)
            return items

    # Step 3: For each Claude-found URL, route to the right Apify actor.
    # scrape_menu_by_url handles DoorDash/Grubhub natively and falls
    # back to menus-r-us for any other domain.
    if apify_ok() and menu_urls and scrape_menu_by_url:
        for url in menu_urls:
            items, platform = scrape_menu_by_url(url)
            if items:
                log.info("Apify (%s) extracted %d items for %s", platform, len(items), place_name)
                return items

    # Step 4: URL-less fallback — ask menus-r-us to resolve by name + location.
    if apify_ok() and scrape_menus_r_us_by_search:
        location = place_address or place_name
        items = scrape_menus_r_us_by_search(place_name, location)
        if items:
            log.info("Apify menus-r-us search-mode found %d items for %s", len(items), place_name)
            return items

    # Step 5: Last-resort JSON-LD / __NEXT_DATA__ parsing on any URL we have.
    for url in menu_urls:
        items = _extract_structured_menu(url, place_name)
        if items:
            log.info("Structured-data extracted %d items for %s", len(items), place_name)
            return items

    log.info("No menu data found for %s across %d URL(s)", place_name, len(menu_urls))
    return []


def _extract_structured_menu(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Fetch a delivery platform page and extract menu items from JSON-LD
    or other structured data embedded in the page."""
    import re as _re

    html = _fetch_page_content(url)
    if not html:
        return []

    items = []

    # Strategy 1: JSON-LD structured data (Schema.org)
    ld_blocks = _re.findall(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, _re.DOTALL
    )
    for block in ld_blocks:
        try:
            data = json.loads(block)
            items = _parse_jsonld_menu(data)
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 2: __NEXT_DATA__ (Next.js apps like GetSauce, ChowNow)
    next_match = _re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html, _re.DOTALL
    )
    if next_match:
        try:
            data = json.loads(next_match.group(1))
            items = _parse_nextjs_menu(data)
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: Look for embedded JSON with menu/price patterns
    json_blobs = _re.findall(r'\{[^{}]*"price"[^{}]*\}', html)
    if len(json_blobs) > 10:
        # Lots of price objects — try to extract from the page with Claude
        text = _extract_menu_text(html)
        if text and len(text) > 200:
            items = _claude_extract_competitor_menu(place_name, text)
            if items:
                return items

    return []


def _parse_jsonld_menu(data: Any) -> List[Dict[str, Any]]:
    """Parse Schema.org JSON-LD menu data."""
    items = []

    # Handle @graph wrapper
    if isinstance(data, dict) and "@graph" in data:
        for entry in data["@graph"]:
            if isinstance(entry, dict) and entry.get("@type") == "Restaurant":
                data = entry
                break
        else:
            return []

    # Find the menu
    menu = None
    if isinstance(data, dict):
        if data.get("@type") == "Restaurant":
            menu = data.get("hasMenu", {})
        elif data.get("@type") == "Menu":
            menu = data

    if not menu or not isinstance(menu, dict):
        return []

    sections = menu.get("hasMenuSection", [])
    if not isinstance(sections, list):
        return []

    for sec in sections:
        if not isinstance(sec, dict):
            continue
        cat = sec.get("name", "Other")
        menu_items = sec.get("hasMenuItem", [])
        if not isinstance(menu_items, list):
            continue
        for mi in menu_items:
            if not isinstance(mi, dict):
                continue
            name = mi.get("name", "").strip()
            if not name:
                continue
            offer = mi.get("offers", {})
            if not isinstance(offer, dict):
                offer = {}
            try:
                price = float(offer.get("price", 0))
            except (ValueError, TypeError):
                price = 0
            if price > 0:
                items.append({
                    "name": name,
                    "price_cents": int(price * 100),
                    "category": cat,
                })
    return items


def _parse_nextjs_menu(data: dict) -> List[Dict[str, Any]]:
    """Parse menu data from Next.js __NEXT_DATA__ (used by GetSauce, etc.)."""
    items = []
    try:
        pp = data.get("props", {}).get("pageProps", {})
        loc = pp.get("location", {})
        menu = loc.get("menu", {})
        cats = menu.get("categories", [])
        for cat in cats:
            cat_name = cat.get("name", "Other")
            for mi in cat.get("items", []):
                name = mi.get("name", "").strip()
                price = mi.get("price", 0)
                if name and price:
                    items.append({
                        "name": name,
                        "price_cents": int(price),
                        "category": cat_name,
                    })
    except (AttributeError, TypeError):
        pass
    return items


def _claude_parse_menu_response(client, prompt: str, place_name: str) -> List[Dict[str, Any]]:
    """Send a prompt to Claude and parse the menu items response."""
    try:
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        return _parse_json_items(raw, place_name)
    except Exception as e:
        log.error("Claude menu extraction failed for %s: %s", place_name, e)
        return []


def _parse_json_items(raw: str, place_name: str) -> List[Dict[str, Any]]:
    """Parse a Claude response into a list of menu items."""
    import re
    # Strip markdown fencing
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # Find the JSON array
    arr_start = raw.find("[")
    arr_end = raw.rfind("]")
    if arr_start >= 0 and arr_end > arr_start:
        raw = raw[arr_start:arr_end + 1]
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        log.error("Failed to parse menu JSON for %s", place_name)
        return []
    if not isinstance(items, list):
        return []
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        cleaned.append({
            "name": str(item["name"]).strip(),
            "price_cents": int(item.get("price_cents") or 0),
            "category": str(item.get("category") or "Other").strip(),
        })
    return cleaned


def _save_menu_cache(
    place_id: str,
    place_name: str,
    website_url: Optional[str],
    menu_url: Optional[str],
    items: List[Dict],
    status: str,
    error: Optional[str],
    now: str,
) -> None:
    """Cache scraped menu results."""
    try:
        with db_connect() as conn:
            conn.execute(
                """INSERT INTO competitor_menus
                   (place_id, place_name, website_url, menu_url, menu_items,
                    item_count, scrape_status, error_message, created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(place_id) DO UPDATE SET
                       place_name = excluded.place_name,
                       website_url = excluded.website_url,
                       menu_url = excluded.menu_url,
                       menu_items = excluded.menu_items,
                       item_count = excluded.item_count,
                       scrape_status = excluded.scrape_status,
                       error_message = excluded.error_message,
                       updated_at = excluded.updated_at,
                       expires_at = excluded.expires_at""",
                (
                    place_id, place_name, website_url, menu_url,
                    json.dumps(items), len(items), status, error, now, now,
                    (datetime.utcnow() + timedelta(days=_MENU_CACHE_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
    except Exception as e:
        log.warning("Failed to cache menu for %s: %s", place_name, e)


def _get_cached_menu(place_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve cached scraped menu. Returns None if expired or not found."""
    try:
        with db_connect() as conn:
            row = conn.execute(
                """SELECT place_id, place_name, menu_url, menu_items,
                          item_count, scrape_status, expires_at
                   FROM competitor_menus WHERE place_id = ?""",
                (place_id,),
            ).fetchone()
        if not row:
            return None
        # Check expiry
        if row["expires_at"]:
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            if now > row["expires_at"]:
                return None  # expired — will be re-fetched
        items = json.loads(row["menu_items"]) if row["menu_items"] else []
        return {
            "place_id": row["place_id"],
            "place_name": row["place_name"],
            "menu_url": row["menu_url"],
            "items": items,
            "source": "scraped" if row["scrape_status"] in ("scraped", "web_search") else "not_found",
            "from_cache": True,
        }
    except Exception:
        return None