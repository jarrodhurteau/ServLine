# storage/apify_client.py
"""
Apify actor client for competitor menu scraping.

Replaces Claude web search for DoorDash / Grubhub menu extraction with
dedicated Apify actors that return structured menu data reliably.

Used by storage.price_intel._claude_web_search_menu to fetch real
competitor prices once a delivery-platform URL has been identified.

Requires APIFY_API_TOKEN in environment (loaded via .env).
Set APIFY_DEMO_MODE=true to run actors in free demo mode (no charge,
partial sample data) — useful during development.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

APIFY_BASE = "https://api.apify.com/v2"

# Actor identifiers (username~actor-name format for URL paths)
ACTOR_DOORDASH = "alizarin_refrigerator-owner~doordash-scraper"
ACTOR_GRUBHUB = "alizarin_refrigerator-owner~grubhub-scraper"
# Day 141.7: generic restaurant-website scraper. Works on any URL and has
# a search-mode fallback (name + city). ~$0.02-0.05 per successful scrape,
# pay-only-on-success. Preferred over delivery-platform scrapers because
# restaurant websites carry richer/cleaner menu data at 30x lower cost.
ACTOR_MENUS_R_US = "menus-r-us~restaurant-menu-scraper"
# Day 141.7: fallback URL discovery. When Claude web search can't find
# a restaurant's website, this actor queries Google Maps by name and
# returns the `website` field from their business panel.
ACTOR_GOOGLE_MAPS = "compass~crawler-google-places"

RUN_TIMEOUT_SECONDS = 180


def _get_token() -> Optional[str]:
    tok = os.environ.get("APIFY_API_TOKEN", "").strip()
    return tok or None


def _demo_mode() -> bool:
    return os.environ.get("APIFY_DEMO_MODE", "").strip().lower() in ("1", "true", "yes")


def _run_actor_sync(actor_id: str, input_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Run an Apify actor synchronously and return its dataset items.

    Uses the run-sync-get-dataset-items endpoint, which blocks until the
    actor finishes and returns the dataset as JSON array.
    """
    token = _get_token()
    if not token:
        log.warning("APIFY_API_TOKEN not set — skipping Apify call")
        return []

    url = (
        f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
        f"?token={urllib.parse.quote(token)}"
    )
    body = json.dumps(input_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=RUN_TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        log.warning("Apify actor %s HTTP error %s: %s", actor_id, e.code, e.reason)
        return []
    except (urllib.error.URLError, TimeoutError) as e:
        log.warning("Apify actor %s connection error: %s", actor_id, e)
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Apify actor %s returned non-JSON output", actor_id)
        return []

    if not isinstance(data, list):
        log.warning("Apify actor %s returned unexpected shape: %s", actor_id, type(data))
        return []

    return data


_PRICE_RE = re.compile(r"[-+]?\$?\s*(\d+(?:\.\d{1,2})?)")


def _parse_price_cents(val: Any) -> int:
    """Accept int cents, float dollars, or string like '$12.99' → int cents."""
    if val is None:
        return 0
    if isinstance(val, bool):
        return 0
    if isinstance(val, int):
        # Heuristic: >= 1000 and no decimals suggests already cents
        return val if val >= 1000 else val * 100
    if isinstance(val, float):
        return int(round(val * 100))
    if isinstance(val, str):
        m = _PRICE_RE.search(val)
        if not m:
            return 0
        try:
            return int(round(float(m.group(1)) * 100))
        except ValueError:
            return 0
    return 0


def _normalize_items(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize actor output into [{name, price_cents, category}].

    Actor output varies — items may be top-level, or nested under a
    `menu`/`menus`/`items` key of a store object. We flatten defensively.
    """
    out: List[Dict[str, Any]] = []

    def _emit(name: Any, price: Any, category: Any) -> None:
        n = (str(name).strip() if name else "")
        if not n:
            return
        cents = _parse_price_cents(price)
        cat = str(category).strip() if category else "Other"
        out.append({"name": n, "price_cents": cents, "category": cat or "Other"})

    def _walk(node: Any, inherited_cat: str = "") -> None:
        if isinstance(node, list):
            for child in node:
                _walk(child, inherited_cat)
            return
        if not isinstance(node, dict):
            return

        # Category container pattern (e.g. {"name": "Appetizers", "items": [...]})
        cat_name = inherited_cat
        for ck in ("category", "categoryName", "menuCategory", "section", "sectionName"):
            v = node.get(ck)
            if isinstance(v, str) and v.strip():
                cat_name = v.strip()
                break

        # Item-shaped node: has name + price
        name = node.get("name") or node.get("itemName") or node.get("title")
        price = (
            node.get("price")
            if "price" in node
            else node.get("priceCents")
            if "priceCents" in node
            else node.get("displayPrice")
            if "displayPrice" in node
            else node.get("cost")
        )
        if name and price is not None:
            _emit(name, price, cat_name or node.get("category", ""))

        # Recurse into common containers
        for key in ("menu", "menus", "items", "menuItems", "products",
                    "categories", "sections", "data", "results", "store", "restaurant"):
            if key in node:
                # If this is a named category container, carry its name down
                child_cat = cat_name
                if key in ("items", "menuItems", "products") and isinstance(node.get("name"), str):
                    child_cat = node["name"].strip() or cat_name
                _walk(node[key], child_cat)

    _walk(raw)
    return out


def _normalize_menus_r_us(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten menus-r-us output to [{name, price_cents, category}]."""
    if not raw or not isinstance(raw, list):
        return []
    first = raw[0]
    if not isinstance(first, dict) or not first.get("success"):
        return []
    menu = first.get("menu") or {}
    categories = menu.get("categories") if isinstance(menu, dict) else None
    if not isinstance(categories, list):
        return []
    out: List[Dict[str, Any]] = []
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_name = str(cat.get("name") or "Other").strip() or "Other"
        for it in cat.get("items") or []:
            if not isinstance(it, dict):
                continue
            name = (it.get("name") or "").strip()
            if not name:
                continue
            out.append({
                "name": name,
                "price_cents": _parse_price_cents(it.get("price")),
                "category": cat_name,
                "description": (it.get("description") or "").strip() or None,
            })
    return out


def _name_roughly_matches(got: str, want: str) -> bool:
    """Fuzzy name check — shared token required, after stripping common
    restaurant suffixes. Used to reject menus-r-us mis-routed responses
    like returning 'Salty Flame Restaurant' when we asked for 'Casa Di Lisa'."""
    if not got or not want:
        return False
    junk = {"restaurant", "pizza", "pizzeria", "bar", "grill", "cafe",
            "kitchen", "the", "and", "&", "of", "la", "el", "di", "da"}
    g = {w for w in got.lower().replace("&", " ").replace(",", " ").split() if w and w not in junk}
    w = {t for t in want.lower().replace("&", " ").replace(",", " ").split() if t and t not in junk}
    if not g or not w:
        return False
    return bool(g & w)


def scrape_menus_r_us_by_url(
    page_url: str,
    expected_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Scrape a restaurant menu from any URL via menus-r-us.

    Passes explicit `mode: "url"` so the actor doesn't fall back to
    search-mode defaults (which ignore our URL). If `expected_name` is
    given, validates the returned restaurantName matches — rejects
    otherwise. Returns [] on any failure or mismatch.
    """
    raw = _run_actor_sync(ACTOR_MENUS_R_US, {"mode": "url", "url": page_url})
    if not raw or not raw[0].get("success"):
        return []
    returned_name = raw[0].get("restaurantName") or ""
    if expected_name:
        if not returned_name:
            log.info("menus-r-us URL returned unlabeled result for '%s' — rejecting", expected_name)
            return []
        if not _name_roughly_matches(returned_name, expected_name):
            log.info(
                "menus-r-us URL returned wrong place: wanted '%s', got '%s'",
                expected_name, returned_name,
            )
            return []
    items = _normalize_menus_r_us(raw)
    log.info("menus-r-us URL %s → %d items (name=%s)", page_url, len(items), returned_name or "?")
    return items


def scrape_menus_r_us_by_search(
    restaurant_name: str,
    cuisine: str,
    location: str,
) -> List[Dict[str, Any]]:
    """
    Use menus-r-us proper search mode to discover restaurants by cuisine +
    location, then filter to the one matching our target name.

    The actor's search mode returns a LIST of nearby restaurants of that
    cuisine, each with its own scraped menu. We iterate the list to find
    the entry whose restaurantName matches `restaurant_name` (fuzzy) and
    return just that entry's items.
    """
    raw = _run_actor_sync(ACTOR_MENUS_R_US, {
        "mode": "search",
        "query": cuisine or "restaurant",
        "location": location,
    })
    if not raw:
        log.info("menus-r-us search returned no records for %s / %s", cuisine, location)
        return []

    # Find the entry whose name matches our target
    match = None
    for rec in raw:
        if not isinstance(rec, dict) or not rec.get("success"):
            continue
        name = rec.get("restaurantName") or ""
        if name and _name_roughly_matches(name, restaurant_name):
            match = rec
            break

    if not match:
        log.info(
            "menus-r-us search did not find a name match for '%s' in %d results",
            restaurant_name, len(raw),
        )
        return []

    items = _normalize_menus_r_us([match])
    log.info("menus-r-us search matched '%s' → %d items", restaurant_name, len(items))
    return items


def find_website_via_google_maps(
    restaurant_name: str,
    location_hint: str = "",
) -> Optional[str]:
    """Look up a restaurant on Google Maps and return its `website` URL.

    Used as a fallback when Claude web search can't surface a site.
    Many small local restaurants have their website only on Google's
    business panel, not in organic search results.

    Returns the URL or None. ~$0.002 per call.
    """
    query = f"{restaurant_name} {location_hint}".strip()
    raw = _run_actor_sync(ACTOR_GOOGLE_MAPS, {
        "searchStringsArray": [query],
        "maxCrawledPlacesPerSearch": 1,
        "language": "en",
        "countryCode": "us",
    })
    if not raw or not isinstance(raw, list):
        return None
    first = raw[0] if isinstance(raw[0], dict) else None
    if not first:
        return None
    website = (first.get("website") or "").strip()
    if website:
        log.info("Google Maps URL fallback for '%s' -> %s", restaurant_name, website)
        return website
    return None


def scrape_doordash_menu(store_url: str) -> List[Dict[str, Any]]:
    """Scrape a DoorDash store URL. Returns normalized items or []."""
    demo = _demo_mode()
    raw = _run_actor_sync(ACTOR_DOORDASH, {
        "storeUrl": store_url,
        "demoMode": demo,
    })
    if not raw:
        return []
    items = _normalize_items(raw)
    log.info("Apify DoorDash %s → %d items (demo=%s)", store_url, len(items), demo)
    return items


def scrape_grubhub_menu(store_url: str) -> List[Dict[str, Any]]:
    """Scrape a Grubhub restaurant URL. Returns normalized items or []."""
    demo = _demo_mode()
    raw = _run_actor_sync(ACTOR_GRUBHUB, {
        "scrapeType": "restaurant_profile",
        "storeUrl": store_url,
        "includeMenu": True,
        "demoMode": demo,
    })
    if not raw:
        return []
    items = _normalize_items(raw)
    log.info("Apify Grubhub %s → %d items (demo=%s)", store_url, len(items), demo)
    return items


def _platform_for_url(url: str) -> Optional[str]:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return None
    if "doordash.com" in host:
        return "doordash"
    if "grubhub.com" in host or "seamless.com" in host:
        return "grubhub"
    # ubereats deferred — add once v2 URL-based actor is chosen
    return None


def scrape_menu_by_url(
    url: str,
    expected_name: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Dispatch a URL to the best-fit Apify actor.

    Day 141.7 ordering — use the specialist actor for delivery platforms
    (complete menus), menus-r-us for everything else:
      1. DoorDash / Grubhub URLs → their dedicated actors first (built for
         those platforms, return full menus). menus-r-us fallback if they fail.
      2. Any other URL → menus-r-us (restaurant sites, Toast, Square, PDF).

    `expected_name` is passed to menus-r-us for name-match validation.
    Returns (items, platform_name) or ([], None) if nothing scrapes.
    """
    platform = _platform_for_url(url)

    # Delivery platforms: dedicated actor first, menus-r-us fallback
    if platform == "doordash":
        items = scrape_doordash_menu(url)
        if items:
            return items, "doordash"
        items = scrape_menus_r_us_by_url(url, expected_name=expected_name)
        if items:
            return items, "menus_r_us"
        return [], None

    if platform == "grubhub":
        items = scrape_grubhub_menu(url)
        if items:
            return items, "grubhub"
        items = scrape_menus_r_us_by_url(url, expected_name=expected_name)
        if items:
            return items, "menus_r_us"
        return [], None

    # Everything else: menus-r-us (restaurant websites, aggregators)
    items = scrape_menus_r_us_by_url(url, expected_name=expected_name)
    if items:
        return items, "menus_r_us"
    return [], None


def is_configured() -> bool:
    """True if APIFY_API_TOKEN is available."""
    return _get_token() is not None
