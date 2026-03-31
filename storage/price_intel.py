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

# Cache TTL — don't re-query the same zip+cuisine combo within 7 days
CACHE_TTL_DAYS = 7

# Max nearby restaurants to store per search
MAX_RESULTS = 20

# Google Places API radius (meters).  ~8 km ≈ 5 miles
SEARCH_RADIUS_METERS = 8000

# Rate limiting: max API calls per minute
RATE_LIMIT_PER_MINUTE = 10
_call_timestamps: list[float] = []

# Cuisine type → Google Places search keyword
CUISINE_SEARCH_TERMS: Dict[str, str] = {
    "american": "american restaurant",
    "italian": "italian restaurant",
    "mexican": "mexican restaurant",
    "chinese": "chinese restaurant",
    "japanese": "japanese restaurant",
    "thai": "thai restaurant",
    "indian": "indian restaurant",
    "mediterranean": "mediterranean restaurant",
    "french": "french restaurant",
    "korean": "korean restaurant",
    "vietnamese": "vietnamese restaurant",
    "greek": "greek restaurant",
    "caribbean": "caribbean restaurant",
    "bbq": "bbq restaurant",
    "seafood": "seafood restaurant",
    "pizza": "pizza restaurant",
    "burger": "burger restaurant",
    "deli": "deli",
    "bakery": "bakery",
    "cafe": "cafe",
    "bar": "bar restaurant",
    "other": "restaurant",
}

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
    """Convert a US zip code to lat/lng via Google Geocoding API."""
    params = urllib.parse.urlencode({
        "address": zip_code,
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
    """Search Google Places Nearby for restaurants matching keyword."""
    params = urllib.parse.urlencode({
        "location": f"{lat},{lng}",
        "radius": SEARCH_RADIUS_METERS,
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

    zip_code = (restaurant.get("zip_code") or "").strip()
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
    keyword = CUISINE_SEARCH_TERMS.get(cuisine_type, "restaurant")
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
            """SELECT place_name, place_address, price_level, price_label,
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