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


def scrape_menus_r_us_by_url(page_url: str) -> List[Dict[str, Any]]:
    """Scrape a restaurant menu from any website URL via menus-r-us actor.
    Returns normalized items or [] on failure. Only charged on success."""
    raw = _run_actor_sync(ACTOR_MENUS_R_US, {"url": page_url})
    items = _normalize_menus_r_us(raw)
    log.info("Apify menus-r-us URL %s → %d items", page_url, len(items))
    return items


def scrape_google_menu_panel(restaurant_name: str, location: str) -> List[Dict[str, Any]]:
    """
    Extract the menu widget Google shows on a restaurant's search result page.

    Google aggregates menu data from Single Platform + restaurant websites
    and displays it in the knowledge panel. There's no official API for
    this, but menus-r-us can parse the Google search URL directly.

    Returns normalized items. Empty list if Google has no menu panel for
    this restaurant.
    """
    q = f"{restaurant_name} menu {location}".strip()
    google_url = (
        "https://www.google.com/search?q="
        + urllib.parse.quote(q)
    )
    raw = _run_actor_sync(ACTOR_MENUS_R_US, {"url": google_url})
    items = _normalize_menus_r_us(raw)
    log.info("Google menu panel for '%s' → %d items", restaurant_name, len(items))
    return items


def scrape_menus_r_us_by_search(restaurant_name: str, location: str) -> List[Dict[str, Any]]:
    """Search-mode fallback when we don't have a direct URL. Location is
    free-form (city, state or address) — the actor resolves it."""
    raw = _run_actor_sync(ACTOR_MENUS_R_US, {
        "searchTerm": restaurant_name,
        "location": location,
    })
    if not raw or not raw[0].get("success"):
        log.info("Apify menus-r-us search '%s' @ '%s' → no match", restaurant_name, location)
        return []
    # Verify the returned restaurant name loosely matches what we asked for
    returned = (raw[0].get("restaurantName") or "").lower()
    want = restaurant_name.lower()
    if returned and not (want in returned or returned in want):
        log.info(
            "Apify menus-r-us search matched wrong place: wanted '%s', got '%s'",
            restaurant_name, raw[0].get("restaurantName"),
        )
        return []
    items = _normalize_menus_r_us(raw)
    log.info("Apify menus-r-us search '%s' → %d items", restaurant_name, len(items))
    return items


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


def scrape_menu_by_url(url: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Dispatch a URL to the best-fit Apify actor.

    Day 141.7 ordering:
      1. DoorDash / Grubhub delivery-platform URLs → their dedicated actors.
      2. Anything else (restaurant websites, aggregators) → menus-r-us
         generic scraper, which handles any site and is far cheaper.

    Returns (items, platform_name) or ([], None) if nothing scrapes.
    """
    platform = _platform_for_url(url)
    if platform == "doordash":
        return scrape_doordash_menu(url), "doordash"
    if platform == "grubhub":
        return scrape_grubhub_menu(url), "grubhub"
    # Generic fallback: menus-r-us reads the restaurant's own site or
    # aggregator page and returns structured items.
    items = scrape_menus_r_us_by_url(url)
    if items:
        return items, "menus_r_us"
    return [], None


def is_configured() -> bool:
    """True if APIFY_API_TOKEN is available."""
    return _get_token() is not None
