"""Vision-Language Model menu extractor.

Pipeline: Playwright loads a competitor's menu page → screenshot the
rendered page (chunked vertically for tall menus) → Claude Opus
vision extracts items as `category|name|price_cents`.

Why this exists: `_claude_web_search_menu` uses Claude web search which
synthesizes menu data from search snippets. That synthesis is noisy —
items get miscategorized, sizes drift, prices get attached to the
wrong items. The VLM-on-screenshot path eliminates that noise: Claude
sees the actual rendered menu and reads it directly.

Cost: ~$0.025 per restaurant (measured), 10-30s wall-clock per
uncached restaurant. Cached for 30 days in `competitor_menus`.
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

log = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-opus-4-7"
MAX_IMAGE_BYTES = 4_500_000   # Anthropic limit is 5MB; leave headroom
MAX_IMAGE_DIM = 7500          # Anthropic rejects > 8000px any dim


_EXTRACTION_PROMPT = """This is one or more screenshots of a restaurant's
menu page (sequential vertical slices of the same page, top to bottom).

Extract every menu item visible. Return a pipe-delimited table — one
item per line, NO header row, NO prose, NO markdown fence:

  category|name|price_cents

Rules:
- Only include items with explicit visible prices.
- Use the EXACT item name as shown (don't invent variants).
- price_cents is integer cents only (e.g., $11.99 → 1199).
- Use the menu's own section/header as the category (e.g.,
  "Specialty Pizzas", "Calzones"). Don't invent your own taxonomy.
- Include size/portion variants as separate rows when each has its
  own price.
- Ignore navigation, footers, hours, address, social buttons, cart UI.
- Dedupe items that appear in the overlap region between chunks.
- If the screenshots show no menu, return nothing (empty output).
"""

_SKIP_LINK_TOKENS = (
    "bereavement", "catering", "kids", "lunch special",
    "wine list", "drink", "cocktail", "events",
)
_COMMON_MENU_PATHS = (
    "/menu", "/our-menu", "/menus", "/food", "/order",
    "/menu/", "/food-menu", "/our-food", "/dine-in",
)


def extract_menu_from_url(
    url: str,
    place_name: str = "",
    *,
    platform: Optional[str] = None,
    timeout_s: int = 60,
) -> List[Dict[str, Any]]:
    """Top-level: navigate to URL, run platform-aware extraction.

    Strategy ordered fastest → slowest. APIs are PRIMARY; browser
    paths are FALLBACKS. When an API path fails unexpectedly we log
    at WARNING level with prefix `EXTRACTOR_FAILURE` so a downstream
    alert/monitoring rule can pick it up — that means a fast path
    that was supposed to work didn't, and we want to know promptly
    so it can be fixed.

      1. **Slice JSON API** — HTTP fetch + 1-2 API calls, ~5 sec
      2. **Allhungry JSON API** — HTTP fetch + per-cat JSON, ~5 sec
      3. **Slice modal click-through** — fallback if API fails
      4. **Universal click-through** — slow, generic
      5. **Screenshot + Claude vision** — last resort, no sizes

    Returns: list of {name, price_cents, category} dicts.
    """
    if not url:
        return []

    # === PRIMARY: API-based extractors ===

    if platform == "Slice":
        try:
            items = _extract_slice_via_api(url, place_name)
            if items:
                log.info("Slice API: %s → %d items", place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=Slice path=api place=%r url=%r "
                "reason=zero_items_returned — falling back to modal click",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=Slice path=api place=%r url=%r "
                "reason=%s: %s — falling back to modal click",
                place_name, url, type(e).__name__, e,
            )

    if platform == "Allhungry":
        try:
            items = _extract_allhungry_via_api(url, place_name)
            if items:
                log.info("Allhungry API: %s → %d items",
                         place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=Allhungry path=api place=%r "
                "url=%r reason=zero_items_returned — falling back to clicks",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=Allhungry path=api place=%r "
                "url=%r reason=%s: %s — falling back to clicks",
                place_name, url, type(e).__name__, e,
            )

    if platform == "ChowNow":
        try:
            items = _extract_chownow_via_api(url, place_name)
            if items:
                log.info("ChowNow API: %s → %d items",
                         place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=ChowNow path=api place=%r "
                "url=%r reason=zero_items_returned — falling back to clicks",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=ChowNow path=api place=%r "
                "url=%r reason=%s: %s — falling back to clicks",
                place_name, url, type(e).__name__, e,
            )

    if platform == "Clover":
        try:
            items = _extract_clover_via_html(url, place_name)
            if items:
                log.info("Clover Online: %s → %d items",
                         place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=Clover path=html place=%r "
                "url=%r reason=zero_items_returned — falling back to clicks",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=Clover path=html place=%r "
                "url=%r reason=%s: %s — falling back to clicks",
                place_name, url, type(e).__name__, e,
            )

    if platform == "Toast":
        try:
            items = _extract_toast_via_html(url, place_name)
            if items:
                log.info("Toast: %s → %d items", place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=Toast path=html place=%r "
                "url=%r reason=zero_items_returned — falling back to clicks",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=Toast path=html place=%r "
                "url=%r reason=%s: %s — falling back to clicks",
                place_name, url, type(e).__name__, e,
            )

    if platform == "DoorDash Storefront":
        try:
            items = _extract_doordash_storefront_via_html(url, place_name)
            if items:
                log.info("DoorDash Storefront: %s → %d items",
                         place_name, len(items))
                return items
            log.warning(
                "EXTRACTOR_FAILURE platform=DoorDashStorefront path=html "
                "place=%r url=%r reason=zero_items_returned — falling "
                "back to clicks",
                place_name, url,
            )
        except Exception as e:
            log.warning(
                "EXTRACTOR_FAILURE platform=DoorDashStorefront path=html "
                "place=%r url=%r reason=%s: %s — falling back to clicks",
                place_name, url, type(e).__name__, e,
            )

    # === FALLBACK 1: Slice modal click-through ===

    if platform == "Slice":
        try:
            items = _extract_slice_via_modals(url, place_name)
            if items:
                log.info("Slice modal-click (fallback): %s → %d items",
                         place_name, len(items))
                return items
        except Exception as e:
            log.warning("Slice modal-click fallback failed for %s: %s",
                        place_name, e)

    # === FALLBACK 2: Universal click-through (modal OR page-nav) ===

    try:
        items = _extract_via_clickthroughs(url, place_name)
        if items:
            log.info("Click-through (fallback): %s → %d items",
                     place_name, len(items))
            return items
    except Exception as e:
        log.warning("Click-through fallback failed for %s: %s", place_name, e)

    # === FALLBACK 3: Screenshot + Claude vision (no sizes captured) ===

    try:
        chunks = _capture_menu_screenshots(url)
    except Exception as e:
        log.warning("VLM screenshot failed for %s (%s): %s",
                    place_name, url, e)
        return []
    if not chunks:
        return []
    try:
        items = _extract_with_claude(chunks, place_name)
        if items:
            log.info("Vision (last-resort): %s → %d items",
                     place_name, len(items))
        return items
    except Exception as e:
        log.warning("VLM Claude call failed for %s: %s", place_name, e)
        return []
    finally:
        for c in chunks:
            try:
                c.unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Universal click-through extractor
# ---------------------------------------------------------------------------
# Most ordering platforms expose size variants only after the user
# clicks an item card. The card shows the smallest/base price
# ("$17.95+"); the click triggers either a modal overlay (Slice,
# ChowNow) or a navigation to an item detail page (Allhungry, Toast,
# many custom WordPress menus that link to a separate ordering domain).
# This extractor handles both. After the click we read innerText and
# parse size variants with a flexible regex that works for the
# patterns seen across platforms.

_SIZE_SECTION_START = re.compile(
    r'(?im)^\s*(?:CHOOSE\s+(?:AN\s+OPTION|A\s+SIZE)|SELECT\s+SIZE|'
    r'SIZE\s*:?|SIZES?\s*:)\s*$'
) if False else None  # placeholder; we use string ops below for portability

import re as _re_clickthrough  # local alias to avoid shadowing
_SIZE_HEADERS_TUPLE = (
    "CHOOSE AN OPTION", "CHOOSE A SIZE", "SELECT SIZE", "SIZES",
    "SIZE", "SIZE:",
)
_END_HEADERS_TUPLE = (
    "CHOOSE CRUST", "ADD TOPPINGS", "TOPPINGS", "ADD-ONS", "ADD ONS",
    "ADDITIONAL", "EXTRAS", "QUANTITY", "ADD ITEM", "ADD TO ORDER",
    "TOTAL PRICE", "NOTES", "MAKE IT", "DRINK", "SIDES",
    "DELIVERY", "SPECIAL INSTRUCTIONS",
)
_PRICE_LINE = _re_clickthrough.compile(r'^\s*\$\s*(\d+(?:[.,]\d{1,2})?)\s*$')
_SAMELINE_PRICE = _re_clickthrough.compile(
    r'^(.*?)\s*[-–:]\s*\$\s*(\d+(?:[.,]\d{1,2})?)\s*$'
)


def _to_cents(price_str: str) -> int:
    if not price_str:
        return 0
    s = price_str.strip().replace(",", ".")
    try:
        if "." in s:
            d, c = s.split(".", 1)
            return int(d) * 100 + int((c + "00")[:2])
        return int(s) * 100
    except (ValueError, TypeError):
        return 0


def _parse_variants_universal(
    text: str, item_name: str, category: str,
) -> List[Dict[str, Any]]:
    """Pull size variants from a modal/page innerText. Handles both
    same-line ('Small - $9.99') and two-line ('Small\\n$9.99')
    patterns. Falls back to single-price extraction when no size
    section is found."""
    if not text or not item_name:
        return []
    upper_lines = text.splitlines()
    # Find size section start
    size_start = -1
    for i, ln in enumerate(upper_lines):
        u = ln.strip().upper()
        if any(u == h or u.startswith(h + " ") or u == h + ":"
               for h in _SIZE_HEADERS_TUPLE):
            size_start = i + 1
            break
    if size_start < 0:
        # No size section — single-price item. Extract first price.
        for ln in upper_lines:
            m = _re_clickthrough.search(r'\$\s*(\d+(?:[.,]\d{1,2})?)', ln)
            if m:
                cents = _to_cents(m.group(1))
                if cents:
                    return [{
                        "name": item_name,
                        "price_cents": cents,
                        "category": category,
                    }]
        return []

    # Find end of size section
    size_end = len(upper_lines)
    for i in range(size_start, len(upper_lines)):
        u = upper_lines[i].strip().upper()
        if any(u.startswith(e) for e in _END_HEADERS_TUPLE):
            size_end = i
            break

    section = upper_lines[size_start:size_end]
    # Skip leading 'Required, select only one' filler
    while section and any(section[0].strip().upper().startswith(p)
                          for p in ("REQUIRED", "OPTIONAL", "SELECT")):
        section = section[1:]

    rows: List[Dict[str, Any]] = []
    seen: set = set()

    # Try same-line pattern first
    for ln in section:
        m = _SAMELINE_PRICE.match(ln.strip())
        if not m:
            continue
        label = m.group(1).strip()
        cents = _to_cents(m.group(2))
        if not label or cents <= 0 or len(label) > 80:
            continue
        if "?" in label or label.upper() == label and len(label.split()) > 4:
            continue
        key = (label.lower(), cents)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "name": f"{item_name} {label}".strip(),
            "price_cents": cents,
            "category": category,
        })
    if rows:
        return rows

    # Two-line pattern (Slice-style: label, then price line)
    lines = [l.strip() for l in section if l.strip()]
    i = 0
    while i < len(lines) and len(rows) < 10:
        label = lines[i]
        if (label.startswith("$") or "?" in label or label.endswith(":")
                or len(label) > 80):
            i += 1
            continue
        # Look ahead 1-2 lines for a price-only line
        cents = 0
        consumed = 1
        for j in range(i + 1, min(i + 3, len(lines))):
            pm = _PRICE_LINE.match(lines[j])
            if pm:
                cents = _to_cents(pm.group(1))
                consumed = j - i + 1
                break
        if cents <= 0:
            i += 1
            continue
        key = (label.lower(), cents)
        if key not in seen:
            seen.add(key)
            rows.append({
                "name": f"{item_name} {label}".strip(),
                "price_cents": cents,
                "category": category,
            })
        i += consumed
    return rows


def _extract_via_clickthroughs(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Universal click-through. Open menu page, click each visible
    item card, parse the resulting state (modal overlay OR new page
    navigation), aggregate size variants for every item."""
    from playwright.sync_api import sync_playwright

    rows: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Find item cards. Cast a wide net: include li/div as well
            # as a/button — many platforms (Allhungry, Toast, custom
            # WP themes) use plain <li> with delegated click handlers,
            # not <a>/<button>. We filter to leaf-level elements
            # (those whose direct text content includes a price)
            # to avoid selecting category containers.
            items_meta = page.evaluate(
                """() => {
                    const out = [];
                    const cands = Array.from(document.querySelectorAll(
                        "a[href], button, [role='button'], [onclick], li, "
                        + "[class*='product'], [class*='item'], [class*='menu-item']"
                    ));
                    // De-dupe (an element matched by multiple selectors
                    // shows up once)
                    const seen = new Set();
                    let idx = -1;
                    for (const el of cands) {
                        idx++;
                        if (seen.has(el)) continue;
                        seen.add(el);
                        const text = (el.innerText || '').trim();
                        if (!text || text.length > 300) continue;
                        if (!/\\$\\s*\\d/.test(text)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 200 || r.width > 900) continue;
                        if (r.height < 40 || r.height > 280) continue;
                        // Skip parent CONTAINERS (cards-of-cards). A
                        // container has another card-sized $-price
                        // descendant. A leaf card has $-price spans
                        // but no other card-sized descendants.
                        let hasInnerCard = false;
                        for (const child of el.querySelectorAll('*')) {
                            const cr = child.getBoundingClientRect();
                            if (cr.width < 200 || cr.height < 40) continue;
                            const ct = (child.innerText || '').trim();
                            if (ct && /\\$\\s*\\d/.test(ct)) {
                                hasInnerCard = true; break;
                            }
                        }
                        if (hasInnerCard) continue;
                        // Skip footer/cart/order buttons
                        const lower = text.toLowerCase();
                        if (lower.includes('view order') ||
                            lower.includes('your cart') ||
                            lower.includes('checkout') ||
                            lower.startsWith('start order') ||
                            lower === 'order online' ||
                            lower.includes('add to cart') ||
                            lower.includes('add item')) continue;
                        let name = text.split('\\n')[0].trim();
                        if (name.length > 80) name = name.slice(0, 80);
                        // Skip lines that are just a price (no name)
                        if (/^\\$/.test(name)) continue;
                        // Find nearest preceding section heading. Skip
                        // siblings that themselves contain prices —
                        // those are other item cards, not category
                        // headers. (Without this, the second item in
                        // a section gets the FIRST item's name as
                        // its category because some templates use
                        // <h3>ItemName</h3> inside the card.)
                        let cat = '';
                        let node = el;
                        let hops = 0;
                        while (node && !cat && hops++ < 12) {
                            let sib = node.previousElementSibling;
                            while (sib && !cat) {
                                const sibText = (sib.innerText || '');
                                const sibIsCard = /\\$\\s*\\d/.test(sibText);
                                if (/^H[1-4]$/.test(sib.tagName) && !sibIsCard) {
                                    cat = (sib.innerText || '').trim();
                                } else if (!sibIsCard && sib.querySelector) {
                                    const h = sib.querySelector('h1, h2, h3, h4');
                                    if (h) cat = (h.innerText || '').trim();
                                }
                                sib = sib.previousElementSibling;
                            }
                            node = node.parentElement;
                        }
                        cat = (cat || 'Menu').slice(0, 60);
                        out.push({idx, name, category: cat});
                    }
                    return out;
                }"""
            )
            log.info("Click-through: %d clickable item cards on %s",
                     len(items_meta), url)

            if not items_meta:
                return []

            original_url = page.url

            # Cap iterations to a safe upper bound. Most restaurant
            # menus have 50-200 items; catering-heavy places (Rinaldi's
            # = 282) blow past 200. Click them all up to 350 to avoid
            # runaway. Click by item NAME — more robust than index
            # across union-selectors and survives DOM mutations.
            seen_names: set = set()
            for meta in items_meta[:350]:
                name = (meta.get("name") or "").strip()
                if not name or len(name) < 4:
                    continue
                # Dedupe by name — items appearing in multiple sections
                # (deals, featured) shouldn't be clicked repeatedly.
                name_key = name.lower()
                if name_key in seen_names:
                    continue
                seen_names.add(name_key)
                try:
                    page.get_by_text(name, exact=True).first.scroll_into_view_if_needed(timeout=2500)
                    page.get_by_text(name, exact=True).first.click(timeout=4000)
                    page.wait_for_timeout(1500)
                except Exception:
                    # Try non-exact match if exact fails
                    try:
                        page.get_by_text(name, exact=False).first.click(timeout=3000)
                        page.wait_for_timeout(1500)
                    except Exception as e:
                        log.debug("Click-through: %r click failed: %s",
                                  name, e)
                        continue

                state_text = ""
                navigated = page.url != original_url
                try:
                    if navigated:
                        # New page — read body
                        state_text = page.evaluate(
                            "() => document.body.innerText"
                        )[:8000]
                    else:
                        # Modal overlay — try common selectors
                        for sel in (
                            ".ReactModalPortal",
                            "[role='dialog']",
                            "[aria-modal='true']",
                            ".modal:visible",
                        ):
                            try:
                                t = page.locator(sel).last.inner_text(
                                    timeout=1000
                                )
                                if t and len(t) > 50:
                                    state_text = t
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass

                if state_text:
                    rows.extend(_parse_variants_universal(
                        state_text, meta["name"], meta["category"],
                    ))

                # Return to menu page for next iteration
                try:
                    if navigated:
                        page.go_back(wait_until="domcontentloaded",
                                     timeout=8000)
                        page.wait_for_timeout(800)
                    else:
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(300)
                except Exception:
                    # If navigation back fails, reload the menu page
                    try:
                        page.goto(original_url,
                                  wait_until="domcontentloaded")
                        page.wait_for_timeout(800)
                    except Exception:
                        break  # can't recover — bail
        finally:
            browser.close()

    log.info("Click-through: %s → %d rows extracted",
             place_name, len(rows))
    return rows


# ---------------------------------------------------------------------------
# Slice-specific extractor
# ---------------------------------------------------------------------------
# Slice's product grid shows ONE price per card (the smallest size).
# All other sizes are hidden inside a click-to-open modal. The generic
# screenshot path misses those sizes entirely. This extractor clicks
# each item, reads the modal text via DOM (deterministic — no vision
# API needed), and emits one row per (item, size).

# CSS modules → class names have hash suffixes that change with Slice
# build versions. Match on the stable prefix.
_SLICE_CARD_SEL = "[class*='styles_productContent']"
_SLICE_MODAL_SEL = ".ReactModalPortal"
# Markers that delimit the size-options section in the modal innerText
_SLICE_SIZE_HEADERS = (
    "CHOOSE AN OPTION", "CHOOSE A SIZE", "SELECT SIZE", "SIZE",
)
_SLICE_END_MARKERS = (
    "CHOOSE CRUST", "CHOOSE A CRUST", "ADD TOPPINGS", "EXTRA TOPPINGS",
    "ADD-ONS", "ADD ONS", "ADD EXTRA", "EXTRAS", "MAKE IT A COMBO",
    "ADDITIONAL", "DELIVERY INSTRUCTIONS", "SPECIAL INSTRUCTIONS",
    "NOTES FOR THE KITCHEN", "NOTES", "QUANTITY SELECTED",
    "ADD TO ORDER", "TOTAL PRICE", "HAVE AN ALLERGY",
    "REMOVE TOPPINGS", "DRINK", "DRINKS", "SIDES", "SIDE OPTIONS",
)


def _extract_slice_via_modals(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Open menu page, walk the item grid, click each item, read modal
    text for size variants. Returns a list of {name, price_cents,
    category} rows — one per (item, size)."""
    from playwright.sync_api import sync_playwright

    rows: List[Dict[str, Any]] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.set_default_timeout(15000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # Walk the page once to map every card to its category and
            # to the click-target locator (we'll re-query each iteration
            # because modal close can mutate the DOM).
            card_meta = page.evaluate("""() => {
                const cards = Array.from(
                    document.querySelectorAll("[class*='styles_productContent']")
                );
                const out = [];
                for (let i = 0; i < cards.length; i++) {
                    const card = cards[i];
                    const text = (card.innerText || '').trim();
                    if (!text || !text.includes('$')) continue;
                    // Walk up siblings/parents to find the nearest
                    // preceding H1/H2/H3 — that's the category header.
                    let cat = '';
                    let node = card;
                    while (node && !cat) {
                        let sib = node.previousElementSibling;
                        while (sib) {
                            const h = sib.querySelector
                                ? sib.querySelector('h1, h2, h3') || (
                                    /^H[123]$/.test(sib.tagName) ? sib : null
                                  )
                                : null;
                            if (h) { cat = (h.innerText || '').trim(); break; }
                            sib = sib.previousElementSibling;
                        }
                        node = node.parentElement;
                    }
                    out.push({index: i, category: cat, preview: text.slice(0, 80)});
                }
                return out;
            }""")

            log.info("Slice: %d item cards found on %s", len(card_meta), url)
            cards_loc = page.locator(_SLICE_CARD_SEL)

            for meta in card_meta:
                idx = meta["index"]
                category = meta["category"] or "Menu"
                try:
                    card = cards_loc.nth(idx)
                    card.scroll_into_view_if_needed(timeout=3000)
                    card.click(timeout=4000)
                    # Slice mounts an empty ReactModalPortal placeholder
                    # at page load + a second one (with the actual modal
                    # content) when an item opens. .last targets the
                    # populated one.
                    page.wait_for_selector(
                        _SLICE_MODAL_SEL, state="attached", timeout=5000,
                    )
                    page.wait_for_timeout(400)
                    modal_text = page.locator(_SLICE_MODAL_SEL).last.inner_text(
                        timeout=2000,
                    )
                except Exception as e:
                    log.debug("Slice: card %d click/modal failed: %s",
                              idx, e)
                    _close_slice_modal(page)
                    continue

                parsed = _parse_slice_modal(modal_text, category)
                rows.extend(parsed)
                _close_slice_modal(page)
        finally:
            browser.close()

    log.info("Slice: %s → %d rows extracted", place_name, len(rows))
    return rows


def _close_slice_modal(page) -> None:
    """Best-effort close: Escape key, then click outside."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    except Exception:
        pass


def _parse_slice_modal(modal_text: str, category: str) -> List[Dict[str, Any]]:
    """Parse a Slice product modal's innerText into one row per size.

    Modal structure observed:
        <ITEM NAME>
        <description...>
        CHOOSE AN OPTION
        Required, select only one
        Mini 10"
        $12.99
        Small 12" (8 Slices)
        $16.50
        ...
        CHOOSE CRUST           ← end-of-sizes marker
        ...
    """
    if not modal_text:
        return []
    lines = [ln.strip() for ln in modal_text.splitlines() if ln.strip()]
    if not lines:
        return []

    # First line = item name. Title-case → preserve as shown.
    item_name = lines[0]

    # Find size-section start
    size_start = -1
    for i, ln in enumerate(lines):
        upper = ln.upper().strip()
        if any(upper.startswith(h) for h in _SLICE_SIZE_HEADERS):
            size_start = i + 1
            break

    if size_start < 0:
        # No size section — single-price item. Find the first $ in the
        # modal text and treat it as the only price.
        for ln in lines[1:]:
            cents = _parse_price_cents(ln)
            if cents:
                return [{
                    "name": item_name,
                    "price_cents": cents,
                    "category": category,
                }]
        return []

    # Collect lines from size_start until we hit an end marker
    size_lines: List[str] = []
    for ln in lines[size_start:]:
        upper = ln.upper().strip()
        if any(upper.startswith(m) for m in _SLICE_END_MARKERS):
            break
        # "Required, select only one" / "Optional" prefix lines — skip
        if upper.startswith(("REQUIRED", "OPTIONAL", "SELECT ONLY",
                              "SELECT UP TO", "SELECT AT LEAST")):
            continue
        size_lines.append(ln)

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    i = 0
    MAX_SIZES_PER_ITEM = 10
    while i < len(size_lines) and len(rows) < MAX_SIZES_PER_ITEM:
        label = size_lines[i]
        # Filter out non-size labels: questions, headers ending with
        # ":", button-like ALL CAPS phrases, anything too long
        if (not label or len(label) > 60
                or "?" in label or label.endswith(":")
                or label.upper() == label and len(label.split()) > 3):
            i += 1
            continue
        # Next line should be the price; scan ahead at most 2 lines
        price_cents = None
        for j in range(i + 1, min(i + 3, len(size_lines))):
            cents = _parse_price_cents(size_lines[j])
            if cents:
                price_cents = cents
                i = j + 1
                break
        if price_cents is None:
            i += 1
            continue
        # Dedupe within a single item by (label, price)
        key = (label.lower(), price_cents)
        if key in seen:
            continue
        seen.add(key)
        # Combine item name + size label so cluster aggregation can
        # treat them as separate rows. e.g. "Enfield Special Pizza
        # Mini 10\""
        rows.append({
            "name": f"{item_name} {label}".strip(),
            "price_cents": price_cents,
            "category": category,
        })
    return rows


def _parse_price_cents(s: str) -> Optional[int]:
    """Parse '$12.99' / '12.99' / '$ 12.99' → 1299 cents. Returns None
    if not a price."""
    if not s:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    if s.startswith("$"):
        s = s[1:]
    if not s:
        return None
    try:
        if "." in s:
            dollars, cents = s.split(".", 1)
            cents = (cents + "00")[:2]
            n = int(dollars) * 100 + int(cents)
        else:
            n = int(s) * 100
    except ValueError:
        return None
    if 50 <= n <= 100000:  # 50¢ floor, $1000 ceiling — outside = junk
        return n
    return None


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def _capture_menu_screenshots(url: str) -> List[Path]:
    """Navigate to the URL, find the menu page if needed, screenshot,
    and return a list of image paths (chunks for tall menus).

    Caller is responsible for deleting the returned paths.
    """
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.gettempdir())
    raw_path = tmp / f"menuvlm_{os.urandom(4).hex()}.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 1800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)

            # If landing isn't already a menu, hunt for one
            if not _is_menu_page(page):
                _navigate_to_menu(page, url)

            # Light scroll to trigger lazy content
            try:
                page.evaluate(
                    "() => new Promise(r => { let y=0; "
                    "const t=setInterval(() => { window.scrollBy(0, 400); "
                    "y+=400; if(y>4000){clearInterval(t);r()} }, 200) })"
                )
                page.wait_for_timeout(1500)
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
            except Exception:
                pass

            page.screenshot(path=str(raw_path), full_page=True)
        finally:
            browser.close()

    return _chunk_image_for_vision(raw_path)


def _is_menu_page(page) -> bool:
    """Real menus have many priced items. Threshold of 25 $ separates
    a homepage promo box (8-12) from a real menu page."""
    try:
        content = (page.content() or "").lower()
        return content.count("$") >= 25
    except Exception:
        return False


def _navigate_to_menu(page, original_url: str) -> None:
    """Try common menu paths first, fall back to scoring page links."""
    base = urlparse(original_url)
    origin = urlunparse((base.scheme, base.netloc, "", "", "", ""))
    for path in _COMMON_MENU_PATHS:
        candidate = origin + path
        try:
            page.goto(candidate, wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            if _is_menu_page(page):
                log.info("VLM: menu detected at common path %s", candidate)
                return
        except Exception:
            continue
    # Reset to landing, score links
    try:
        page.goto(original_url, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    except Exception:
        return
    ml = _find_menu_link(page)
    if ml and ml != original_url:
        log.info("VLM: following menu link %s", ml)
        try:
            page.goto(ml, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        except Exception:
            pass


def _find_menu_link(page) -> Optional[str]:
    """Best-effort: highest-scoring menu link on the page. Skip PDFs
    (Playwright can't render them) and qualifier menus."""
    try:
        links = page.eval_on_selector_all(
            "a",
            """els => els.map(a => ({
                href: a.href || '',
                text: (a.innerText||'').trim()
            }))""",
        )
    except Exception:
        return None
    candidates = []
    for ln in links:
        text = (ln.get("text") or "").lower().strip()
        href = ln.get("href") or ""
        if not href or href.startswith("javascript:"):
            continue
        href_lower = href.lower().split("?")[0]
        if href_lower.endswith((".pdf", ".doc", ".docx")):
            continue
        if "menu" not in text:
            continue
        if any(tok in text for tok in _SKIP_LINK_TOKENS):
            continue
        score = (0 if text == "menu" else 1, len(text))
        candidates.append((score, href))
    candidates.sort()
    return candidates[0][1] if candidates else None


# ---------------------------------------------------------------------------
# Image chunking for vision API
# ---------------------------------------------------------------------------

def _chunk_image_for_vision(path: Path) -> List[Path]:
    """Anthropic vision rejects > 5MB or any dimension > 8000px. For
    tall menu screenshots, chunk vertically with overlap. Convert to
    JPEG for size headroom. Returns list of chunk paths."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("Pillow missing — can't chunk image. Install: pip install pillow")
        return [path] if path.stat().st_size <= MAX_IMAGE_BYTES else []

    img = Image.open(path)
    w, h = img.size

    # Width handling: scale uniformly if too wide (rare)
    if w > MAX_IMAGE_DIM:
        s = MAX_IMAGE_DIM / w
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
        w, h = img.size

    chunks: List[Path] = []
    chunk_h = MAX_IMAGE_DIM - 150  # 150px overlap between chunks
    y = 0
    idx = 0
    while y < h:
        bottom = min(y + MAX_IMAGE_DIM, h)
        crop = img.crop((0, y, w, bottom))
        out = path.with_name(f"{path.stem}_c{idx}.jpg")
        for q in (85, 75, 65, 55):
            crop.convert("RGB").save(out, "JPEG", quality=q, optimize=True)
            if out.stat().st_size <= MAX_IMAGE_BYTES:
                break
        chunks.append(out)
        idx += 1
        if bottom >= h:
            break
        y += chunk_h

    # Free the source PNG once we've chunked it
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass

    log.info("VLM: %d chunk(s), source %dx%d", len(chunks), w, h)
    return chunks


# ---------------------------------------------------------------------------
# Claude vision call
# ---------------------------------------------------------------------------

def _extract_with_claude(chunks: List[Path], place_name: str = "") -> List[Dict[str, Any]]:
    """Send chunks + extraction prompt to Claude Opus vision. Parse
    pipe-delimited response into item dicts."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — VLM disabled")
        return []
    client = anthropic.Anthropic(api_key=api_key)

    content: List[Any] = []
    for c in chunks:
        media_type = "image/jpeg" if c.suffix.lower() == ".jpg" else "image/png"
        img_b64 = base64.standard_b64encode(c.read_bytes()).decode()
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": img_b64,
            },
        })
    content.append({"type": "text", "text": _EXTRACTION_PROMPT})

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=16000,
        messages=[{"role": "user", "content": content}],
    )
    if not msg.content:
        return []
    text = msg.content[0].text.strip()
    # Strip occasional markdown fence
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    items = _parse_pipe_table(text)
    log.info("VLM: %s → %d items extracted", place_name or "(unknown)", len(items))
    return items


def _parse_pipe_table(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        cat = parts[0].strip()
        name = parts[1].strip()
        price_raw = parts[2].strip()
        price_clean = "".join(ch for ch in price_raw if ch.isdigit())
        if not name or not price_clean:
            continue
        try:
            price_cents = int(price_clean)
        except ValueError:
            continue
        if price_cents <= 0 or price_cents > 100000:  # > $1000 = junk
            continue
        out.append({
            "name": name,
            "price_cents": price_cents,
            "category": cat,
        })
    return out


# ---------------------------------------------------------------------------
# Allhungry direct-API extractor
# ---------------------------------------------------------------------------
# Allhungry is a React SPA. Clicking each item navigates to a new
# page, which via Playwright takes ~12 minutes for a typical menu.
# But the data is fetched via JSON endpoints we can hit directly:
#
#   /data/menu/categories/<restaurant_id>  → list of category groups
#   /data/menu/items/<group_id>            → items + size variants
#
# Same endpoints the Allhungry frontend uses. We send a realistic
# User-Agent and stagger requests politely (small delays + parallel
# cap) so we don't look like a scraper.

import urllib.request as _urllib_request
import urllib.error as _urllib_error
import urllib.parse as _urllib_parse
import json as _json
import re as _re_allhungry

_ALLHUNGRY_REST_ID_RE = _re_allhungry.compile(r'"id"\s*:\s*(\d+)')
_HTTP_HEADERS_REALISTIC = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/html;q=0.9",
    "Accept-Language": "en-US,en;q=0.9",
}


def _allhungry_base(url: str) -> str:
    """Strip path/query — keep just protocol://subdomain.allhungry.com."""
    p = _urllib_parse.urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _http_get(url: str, timeout: int = 10) -> Optional[str]:
    """Polite HTTP GET with realistic headers."""
    try:
        req = _urllib_request.Request(url, headers=_HTTP_HEADERS_REALISTIC)
        with _urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (_urllib_error.URLError, OSError, TimeoutError) as e:
        log.debug("HTTP GET failed for %s: %s", url, e)
        return None


def _extract_allhungry_via_api(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Pull a full Allhungry menu via their public JSON API.
    Total time: ~3-5 seconds for 200-300 items vs ~12 min via clicks.
    """
    base = _allhungry_base(url)
    if "allhungry.com" not in base:
        # Customer's main domain links to allhungry. Find the menu
        # sub-domain by loading the main page. SKIP image/asset CDN
        # subdomains (images.allhungry.com etc.) — those don't have
        # a menu API.
        main_html = _http_get(url)
        if not main_html:
            return []
        # Find all .allhungry.com sub-domains, skip CDN/asset hosts.
        # The findall captures bare sub-domain strings (no trailing
        # dot), so excludes are bare names too.
        EXCLUDE_HOSTS = {"images", "assets", "cdn", "static",
                         "fonts", "img", "media"}
        candidates = _re_allhungry.findall(
            r'https?://([a-z0-9-]+)\.allhungry\.com', main_html, _re_allhungry.I,
        )
        menu_host = None
        for sub in candidates:
            if sub.lower() not in EXCLUDE_HOSTS:
                menu_host = sub
                break
        if not menu_host:
            return []
        base = f"https://{menu_host}.allhungry.com"
        log.info("Allhungry: redirected from %s → %s", url, base)

    # 1. Find restaurant ID
    main_html = _http_get(base + "/")
    if not main_html:
        return []
    m = _ALLHUNGRY_REST_ID_RE.search(main_html)
    if not m:
        log.warning("Allhungry: no restaurant ID found at %s", base)
        return []
    rest_id = int(m.group(1))

    # 2. Fetch category list
    cats_body = _http_get(f"{base}/data/menu/categories/{rest_id}")
    if not cats_body:
        return []
    try:
        cats = _json.loads(cats_body)
    except _json.JSONDecodeError:
        return []
    if not isinstance(cats, list):
        return []

    log.info("Allhungry: %d categories for restaurant %d",
             len(cats), rest_id)

    # 3. Fetch each category's items in parallel (capped to avoid
    # looking like a scraper — 4 workers is the same concurrency a
    # real browser would use)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_category(cat_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
        cat_id = cat_meta.get("id")
        cat_name = (cat_meta.get("name") or "").strip()
        if not cat_id:
            return []
        body = _http_get(f"{base}/data/menu/items/{cat_id}")
        if not body:
            return []
        try:
            groups = _json.loads(body)
        except _json.JSONDecodeError:
            return []
        if not isinstance(groups, list):
            return []
        rows = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            group_name = (g.get("name") or cat_name).strip()
            for it in g.get("items", []) or []:
                if not isinstance(it, dict):
                    continue
                it_name = (it.get("name") or "").strip()
                if not it_name:
                    continue
                # Items with size variants
                sizes = it.get("sizeList") or []
                emitted = False
                for sz in sizes:
                    if not isinstance(sz, dict):
                        continue
                    label = (sz.get("name") or "").strip()
                    val = sz.get("value")
                    if val is None or not label:
                        continue
                    cents = int(round(float(val) * 100))
                    if cents <= 0:
                        continue  # skip "0.00" size placeholders
                    rows.append({
                        "name": f"{it_name} {label}".strip(),
                        "price_cents": cents,
                        "category": group_name,
                    })
                    emitted = True
                # Items without sizes — single price
                if not emitted:
                    base_price = it.get("price_nosize") or 0
                    try:
                        cents = int(round(float(base_price) * 100))
                    except (ValueError, TypeError):
                        cents = 0
                    if cents > 0:
                        rows.append({
                            "name": it_name,
                            "price_cents": cents,
                            "category": group_name,
                        })
        return rows

    all_rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(_fetch_category, c) for c in cats]
        for fut in as_completed(futs):
            try:
                all_rows.extend(fut.result())
            except Exception as e:
                log.warning("Allhungry category fetch failed: %s", e)

    log.info("Allhungry API: %s → %d rows extracted",
             place_name, len(all_rows))
    return all_rows


# ---------------------------------------------------------------------------
# Slice direct-API extractor
# ---------------------------------------------------------------------------
# Slice (slicelife.com) embeds the menu in the initial HTML's
# `window._initialDataContext` and fetches size variants via:
#   https://consumer.prod.slicelife.com/services/core/api/v3/menus/
#     <web_slug>/product-types?id=X&id=Y...
# with header `x-api-key: <REACT_APP_CONSUMER_API_KEY>` (also in the
# initial HTML).
#
# Total time: ~3-5 sec for a 400-item menu vs ~5 min via modal-click.

_SLICE_API_BASE = "https://consumer.prod.slicelife.com/services/core/api/v3/menus"


def _extract_slice_via_api(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Pull a full Slice menu via their public API.

    1. HTTP fetch the main page.
    2. Parse `window._initialDataContext` to extract:
         - web_slug ("ct/enfield/06082/enfield-pizza")
         - all categories, products with productTypeIds
       And from the embedded REACT_APP_CONSUMER_API_KEY:
         - x-api-key header value
    3. Batch all productTypeIds into one product-types API call.
    4. Map productTypeId → sizes, build items list.
    """
    body = _http_get(url, timeout=10)
    if not body:
        return []

    # Extract API key. It's embedded server-side in a config block.
    m = _re_allhungry.search(
        r'REACT_APP_CONSUMER_API_KEY["\']?\s*:\s*["\']([A-Za-z0-9_-]+)["\']',
        body,
    )
    if not m:
        # Not a Slice-hosted page. Custom restaurant domains often link
        # to slicelife.com via an "Order Online" button:
        #   https://slicelife.com/restaurants/<state>/<city>/<zip>/<slug>/menu
        # Find the link and re-fetch from there.
        m_link = _re_allhungry.search(
            r'https?://slicelife\.com/restaurants/[a-z]{2}/[a-z0-9-]+/'
            r'\d+/[a-z0-9-]+(?:/menu)?',
            body, _re_allhungry.I,
        )
        if not m_link:
            log.info(
                "Slice API: no slicelife.com link on %s — not Slice-hosted",
                url,
            )
            return []
        slice_url = m_link.group(0)
        log.info("Slice: redirected from %s → %s", url, slice_url)
        body = _http_get(slice_url, timeout=10)
        if not body:
            return []
        m = _re_allhungry.search(
            r'REACT_APP_CONSUMER_API_KEY["\']?\s*:\s*["\']([A-Za-z0-9_-]+)["\']',
            body,
        )
        if not m:
            log.warning("Slice API: REACT_APP_CONSUMER_API_KEY not found "
                        "even on slicelife.com page %s", slice_url)
            return []
        url = slice_url  # use the resolved URL for referer header below
    api_key = m.group(1)

    # Extract _initialDataContext via balanced-bracket parser
    ctx = _extract_initial_data_context(body)
    if not ctx:
        log.warning("Slice API: _initialDataContext not parseable at %s", url)
        return []
    try:
        shop = ctx["0"]["data"]["primaryShopRequest"]["data"]
        web_slug = shop.get("web_slug")
        categories = ctx["0"]["data"]["menuRequest"]["data"]["categories"]
    except (KeyError, TypeError):
        log.warning("Slice API: unexpected initialDataContext shape at %s",
                    url)
        return []
    if not web_slug or not categories:
        log.warning("Slice API: missing web_slug or categories at %s", url)
        return []

    # Collect every productTypeId with its (category, product name)
    pt_to_meta: Dict[int, Dict[str, Any]] = {}
    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cat_name = (cat.get("name") or "").strip() or "Menu"
        for prod in cat.get("products", []) or []:
            if not isinstance(prod, dict):
                continue
            name = (prod.get("name") or "").strip()
            if not name:
                continue
            base_price_str = prod.get("price") or ""
            base_cents = _to_cents(
                base_price_str.replace("$", "").strip()
                if isinstance(base_price_str, str) else str(base_price_str)
            )
            for pt_id in prod.get("productTypeIds") or []:
                pt_to_meta[int(pt_id)] = {
                    "name": name,
                    "category": cat_name,
                    "base_cents": base_cents,
                }

    if not pt_to_meta:
        # Single-price products with no productTypeIds — emit the base
        # price for each.
        rows = []
        for cat in categories:
            cat_name = (cat.get("name") or "").strip() or "Menu"
            for prod in cat.get("products", []) or []:
                price = prod.get("price") or ""
                if not isinstance(price, str):
                    continue
                cents = _to_cents(price.replace("$", "").strip())
                if cents > 0:
                    rows.append({
                        "name": prod.get("name", "").strip(),
                        "price_cents": cents,
                        "category": cat_name,
                    })
        return rows

    # Batch product-types lookup. The query string can be long; chunk
    # in 50-id groups to be polite + URL-length-safe.
    all_pt_ids = list(pt_to_meta.keys())
    rows: List[Dict[str, Any]] = []
    for i in range(0, len(all_pt_ids), 50):
        chunk = all_pt_ids[i:i + 50]
        qs = "&".join(f"id={pid}" for pid in chunk)
        api_url = f"{_SLICE_API_BASE}/{web_slug}/product-types?{qs}"
        try:
            req = _urllib_request.Request(api_url, headers={
                **_HTTP_HEADERS_REALISTIC,
                "x-api-key": api_key,
                "referer": url,
            })
            with _urllib_request.urlopen(req, timeout=15) as resp:
                pt_data = _json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            log.warning("Slice API: product-types call failed (%s): %s",
                        api_url[:120], e)
            continue

        # Slice's response shape:
        #   {"shopId": ..., "productTypes": [{"id", "name", "price",
        #     "productId", "addonIds"}, ...], "relationships": {...}}
        # Each productType IS a single size (NOT a parent with sub-
        # sizes). For a multi-size item like "Enfield Special Pizza",
        # the product has multiple productTypeIds, and each ID resolves
        # to a separate productType entry with its own size label
        # ("Mini 10\"", "Small 12\"", etc.) and price (already in cents).
        if not isinstance(pt_data, dict):
            continue
        for pt in pt_data.get("productTypes") or []:
            if not isinstance(pt, dict):
                continue
            pt_id = pt.get("id")
            if pt_id is None:
                continue
            meta = pt_to_meta.get(int(pt_id))
            if not meta:
                continue
            size_name = (pt.get("name") or "").strip()
            price = pt.get("price")
            if not isinstance(price, (int, float)) or price <= 0:
                continue
            cents = int(price)  # Slice serves prices in cents already
            rows.append({
                "name": (f"{meta['name']} {size_name}".strip()
                          if size_name and size_name.lower() != meta['name'].lower()
                          else meta['name']),
                "price_cents": cents,
                "category": meta["category"],
            })

    log.info("Slice API: %s → %d rows extracted", place_name, len(rows))
    return rows


def _extract_initial_data_context(html: str) -> Optional[Dict[str, Any]]:
    """Parse `window._initialDataContext = {...}` from a Slice page.
    Uses balanced-bracket scanning since the JSON contains escaped
    quotes that confuse a simple regex."""
    m = _re_allhungry.search(r'window\._initialDataContext\s*=\s*', html)
    if not m:
        return None
    start = m.end()
    if start >= len(html) or html[start] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return _json.loads(html[start:i + 1])
                except _json.JSONDecodeError:
                    return None
    return None


# ---------------------------------------------------------------------------
# Clover Online extractor
# ---------------------------------------------------------------------------
# Clover's online ordering pages (<slug>.cloveronline.com/menu/all) embed
# the entire menu in a Next.js Server Component payload inside the
# initial HTML response. The data is unicode-escaped (\\" instead of ")
# but otherwise structured JSON. We extract categories and items via
# regex over the unescaped body — single HTTP call, no auth.
#
# Schema:
#   Categories: {"<id>":{"id":"<id>","name":"...","items":[item_ids]}}
#   Items:      {"id":"X","name":"Y","price":N,"options":[],
#                "modifierGroupIds":[],"categoryIds":[...]}
#   Prices already in CENTS (e.g. 364 = $3.64)
#   Size variants in `options` array (empty when item has only one size)

_CLOVER_ITEM_RE = _re_allhungry.compile(
    r'\{"id":"([A-Z0-9]+)","name":"([^"]+)","itemType":[^,]*,'
    r'"description":(?:null|"[^"]*"),"options":(\[[^\]]*\]),"price":(\d+),'
    r'.*?"modifierGroupIds":\[([^\]]*)\]'
)
_CLOVER_CAT_BLOCK_RE = _re_allhungry.compile(
    r'"([A-Z0-9]+)":\{"id":"\1","name":"([^"]+)","sortOrder":\d+,'
    r'"items":(\[[^\]]*\])'
)
# Modifier group definitions: "<gid>":{"id":"<gid>","name":"<name>",
# "minRequired":N,"maxAllowed":N
_CLOVER_GROUP_DEF_RE = _re_allhungry.compile(
    r'"([A-Z0-9]{10,15})":\{"id":"\1","name":"([^"]+)",'
    r'"minRequired":(\d+),"maxAllowed":(\d+)'
)
# Modifier options: "name":"<n>","price":N,"groupId":"<gid>"
_CLOVER_MOD_OPT_RE = _re_allhungry.compile(
    r'"name":"([^"]+)","price":(\d+),"groupId":"([A-Z0-9]+)"'
)
# Heuristic: option-name tokens that indicate a size variant. When a
# group's options match these, treat the group as a size group even if
# its name doesn't contain "size" (e.g. "Pizza Choice" with Small/Large).
_CLOVER_SIZE_TOKENS = {
    "small", "medium", "large", "x-large", "xl", "xs", "regular",
    "kids", "personal", "single", "double", "triple",
    "1 piece", "2 pieces", "3 pieces", "1 pc", "2 pc", "3 pc",
    "one", "two", "three", "half", "whole", "full",
    "10\"", "12\"", "14\"", "16\"", "18\"",
    "10 inch", "12 inch", "14 inch", "16 inch", "18 inch",
    "12oz", "16oz", "20oz", "24oz", "32oz",
}


def _extract_clover_via_html(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Pull a full Clover Online menu via the embedded JSON in the
    initial HTML. ~1 sec per restaurant, no API calls needed."""
    base_url = url
    if "cloveronline.com" not in url:
        # Customer's main domain may link to a Clover Online sub-domain
        main_html = _http_get(url)
        if not main_html:
            return []
        m = _re_allhungry.search(
            r'https?://[a-z0-9-]+\.cloveronline\.com[^\"\'\s>]*',
            main_html, _re_allhungry.I,
        )
        if not m:
            return []
        base_url = m.group(0)
        # Trim to /menu/all if the link goes to a sub-page
        if "/menu" not in base_url:
            base_url = base_url.rstrip("/") + "/menu/all"
        log.info("Clover: redirected from %s → %s", url, base_url)

    body = _http_get(base_url, timeout=10)
    if not body:
        return []

    # The data is unicode-escaped inside Next.js __next_f.push() calls.
    # Decode escape sequences to get readable JSON.
    try:
        unescaped = body.encode("utf-8", errors="replace").decode(
            "unicode_escape", errors="replace"
        )
    except Exception as e:
        log.warning("Clover: unicode_escape decode failed for %s: %s",
                    place_name, e)
        return []

    # Build category index (item_id → category_name)
    cat_for_item: Dict[str, str] = {}
    for m in _CLOVER_CAT_BLOCK_RE.finditer(unescaped):
        cat_id, cat_name, items_raw = m.groups()
        # items_raw is like ["id1","id2","id3"] — extract IDs
        for iid in _re_allhungry.findall(r'"([A-Z0-9]+)"', items_raw):
            cat_for_item[iid] = cat_name

    # Build modifier-group → name and modifier-group → options[] maps
    # (sizes live in modifier groups, separate from items)
    group_name: Dict[str, str] = {}
    group_min_required: Dict[str, int] = {}
    group_max_allowed: Dict[str, int] = {}
    for m in _CLOVER_GROUP_DEF_RE.finditer(unescaped):
        gid = m.group(1)
        if gid in group_name:
            continue
        group_name[gid] = m.group(2)
        group_min_required[gid] = int(m.group(3))
        group_max_allowed[gid] = int(m.group(4))

    opts_by_group: Dict[str, List[tuple]] = {}
    seen_opt: set = set()
    for m in _CLOVER_MOD_OPT_RE.finditer(unescaped):
        opt_name = m.group(1)
        opt_price = int(m.group(2))
        gid = m.group(3)
        # Dedupe — RSC chunks repeat
        key = (gid, opt_name, opt_price)
        if key in seen_opt:
            continue
        seen_opt.add(key)
        opts_by_group.setdefault(gid, []).append((opt_name, opt_price))

    def _is_size_group(gid: str) -> bool:
        """A modifier group is a size group when (1) the customer must
        pick exactly one option, AND (2) either the group name says
        "size" or the option names look like sizes."""
        if group_min_required.get(gid, 0) != 1:
            return False
        if group_max_allowed.get(gid, 0) != 1:
            return False
        opts = opts_by_group.get(gid, [])
        if len(opts) < 2:
            return False
        gname = group_name.get(gid, "").lower()
        if "size" in gname:
            return True
        opt_names = {n.lower() for n, _ in opts}
        return bool(opt_names & _CLOVER_SIZE_TOKENS)

    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for m in _CLOVER_ITEM_RE.finditer(unescaped):
        iid, name, options_raw, price_str, group_ids_raw = m.groups()
        try:
            price_cents = int(price_str)
        except ValueError:
            continue
        cat = cat_for_item.get(iid, "Menu")
        # Dedupe by item id — items appear multiple times in Next.js RSC
        # chunks. Was (iid, price_cents); items with price=0 + size
        # group all collide on the same key — drop price from the key.
        if iid in seen:
            continue
        seen.add(iid)

        # Look for a size group among this item's modifierGroupIds
        item_gids = _re_allhungry.findall(r'"([A-Z0-9]+)"', group_ids_raw)
        size_gid = next((g for g in item_gids if _is_size_group(g)), None)

        if size_gid:
            # Emit one row per size option; sizes are absolute prices
            for opt_name, opt_price in opts_by_group[size_gid]:
                if opt_price <= 0:
                    continue
                rows.append({
                    "name": f"{name} {opt_name}".strip(),
                    "price_cents": opt_price,
                    "category": cat,
                })
            continue

        if price_cents <= 0:
            # No size group AND no base price → skip (configurator-only)
            continue

        # Inline options[] (rare on Clover; usually empty)
        if options_raw != "[]":
            opt_re = _re_allhungry.compile(
                r'\{"id":"[A-Z0-9]+","name":"([^"]+)","price":(\d+)'
            )
            opts = list(opt_re.finditer(options_raw))
            if opts:
                for om in opts:
                    opt_name = om.group(1)
                    opt_cents = int(om.group(2))
                    if opt_cents <= 0:
                        continue
                    rows.append({
                        "name": f"{name} {opt_name}".strip(),
                        "price_cents": opt_cents,
                        "category": cat,
                    })
                continue

        rows.append({
            "name": name,
            "price_cents": price_cents,
            "category": cat,
        })

    log.info("Clover Online: %s → %d rows extracted", place_name, len(rows))
    return rows


# ---------------------------------------------------------------------------
# ChowNow extractor
# ---------------------------------------------------------------------------
# ChowNow exposes a public JSON API at:
#   api.chownow.com/api/restaurant/<location_id>/menu
#
# No auth required. Restaurant URL pattern:
#   chownow.com/order/<company_id>/locations/<location_id>
#
# Schema:
#   {"menu_categories": [{"name", "items": [{"name", "price", "size",
#                                              "modifier_categories":[mc_ids]}]}],
#    "modifier_categories": [{"id", "name", "modifiers":[mod_ids]}],
#    "modifiers": [{"id", "name", "price"}]}
#
# Items with price > 0 = single-size — emit directly.
# Items with price == 0 (meta) = expand the first modifier_category
# whose options are price-bearing — those are the size/variant choices.
# Prices are in DOLLARS (float), not cents — multiply by 100.

_CHOWNOW_URL_RE = _re_allhungry.compile(
    r'chownow\.com/order/(\d+)/locations/(\d+)', _re_allhungry.I,
)


def _extract_chownow_via_api(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Pull a full ChowNow menu via /api/restaurant/<id>/menu/<ts>.
    ~2-3 sec per restaurant, no auth.

    ChowNow gates the menu endpoint on a future pickup-slot timestamp
    (YYYYMMDDhhmm in restaurant local time). The legacy /menu (no ts)
    returns {} now. We pull the next available slot from
    /api/restaurant/<id>'s order_ahead.days[].pickup_index_ranges and
    construct the timestamp from there."""
    location_id = _resolve_chownow_location_id(url)
    if not location_id:
        return []

    pickup_ts = _chownow_next_pickup_ts(location_id, place_name)
    if not pickup_ts:
        log.warning(
            "ChowNow: %s — could not determine next pickup slot",
            place_name,
        )
        return []

    api_url = (
        f"https://api.chownow.com/api/restaurant/{location_id}"
        f"/menu/{pickup_ts}"
    )
    body = _http_get(api_url, timeout=12)
    if not body:
        return []
    try:
        data = _json.loads(body)
    except _json.JSONDecodeError as e:
        log.warning("ChowNow: JSON parse failed for %s: %s", place_name, e)
        return []

    cats = data.get("menu_categories") or []
    mcs_by_id = {mc["id"]: mc for mc in (data.get("modifier_categories") or [])
                 if isinstance(mc, dict) and "id" in mc}
    mods_by_id = {m["id"]: m for m in (data.get("modifiers") or [])
                  if isinstance(m, dict) and "id" in m}

    rows: List[Dict[str, Any]] = []
    seen_item_ids: set = set()
    for cat in cats:
        if not isinstance(cat, dict):
            continue
        cat_name = (cat.get("name") or "").strip() or "Menu"
        # ChowNow puts featured items in "Popular Items" AND their real
        # category — skip the curated subset so each item appears once
        # under its true category.
        if cat_name.lower() == "popular items":
            continue
        for it in cat.get("items", []) or []:
            if not isinstance(it, dict):
                continue
            item_id = it.get("id")
            if item_id and item_id in seen_item_ids:
                continue
            if item_id:
                seen_item_ids.add(item_id)
            item_name = (it.get("name") or "").strip()
            if not item_name:
                continue
            price = it.get("price") or 0
            try:
                price_cents = int(round(float(price) * 100))
            except (ValueError, TypeError):
                price_cents = 0

            if price_cents > 0:
                # Single-price item
                rows.append({
                    "name": item_name,
                    "price_cents": price_cents,
                    "category": cat_name,
                })
                continue

            # Meta item — sizes live in the first modifier category
            # whose options have non-zero prices. Walk in order until
            # we find one.
            mc_ids = it.get("modifier_categories") or []
            for mc_id in mc_ids:
                mc = mcs_by_id.get(mc_id)
                if not mc:
                    continue
                opt_refs = mc.get("modifiers") or []
                # Resolve modifier IDs → modifier dicts (may be inline
                # or referenced)
                opts: List[Dict[str, Any]] = []
                for ref in opt_refs:
                    if isinstance(ref, str):
                        m = mods_by_id.get(ref)
                        if m:
                            opts.append(m)
                    elif isinstance(ref, dict):
                        opts.append(ref)
                # Check if at least one option has a real price
                has_priced = any(
                    (o.get("price") or 0) > 0 for o in opts
                )
                if not has_priced:
                    continue
                # This is the size/variant modifier — emit each option
                for o in opts:
                    o_name = (o.get("name") or "").strip()
                    o_price = o.get("price") or 0
                    try:
                        o_cents = int(round(float(o_price) * 100))
                    except (ValueError, TypeError):
                        o_cents = 0
                    if o_cents <= 0 or not o_name:
                        continue
                    rows.append({
                        "name": f"{item_name} {o_name}".strip(),
                        "price_cents": o_cents,
                        "category": cat_name,
                    })
                break  # only walk the first price-bearing modifier

    log.info("ChowNow API: %s → %d rows extracted", place_name, len(rows))
    return rows


def _resolve_chownow_location_id(url: str) -> Optional[str]:
    """Extract the ChowNow location_id from a URL. Customer's main
    domain may not be on chownow.com — in that case fetch the page
    and look for the redirect URL."""
    m = _CHOWNOW_URL_RE.search(url)
    if m:
        return m.group(2)
    # Customer's main domain — find the chownow link
    body = _http_get(url, timeout=10)
    if not body:
        return None
    m = _CHOWNOW_URL_RE.search(body)
    if not m:
        return None
    return m.group(2)


def _chownow_next_pickup_ts(
    location_id: str, place_name: str,
) -> Optional[str]:
    """Compute the YYYYMMDDhhmm token for the restaurant's next
    available pickup slot. ChowNow's /menu endpoint requires this.
    Walks order_ahead.days; for each day finds the first 1-bit in
    pickup_index_ranges and converts (index × precision_minutes) to
    a clock time."""
    info_url = f"https://api.chownow.com/api/restaurant/{location_id}"
    body = _http_get(info_url, timeout=10)
    if not body:
        return None
    try:
        info = _json.loads(body)
    except _json.JSONDecodeError:
        return None

    order_ahead = info.get("order_ahead") or {}
    days = order_ahead.get("days") or []
    # Precision lives on the delivery/pickup fulfillment branch
    fulfillment = info.get("fulfillment") or {}
    pickup_branch = (fulfillment.get("pickup")
                     or fulfillment.get("delivery") or {})
    inner = pickup_branch.get("order_ahead") or {}
    precision = int(inner.get("precision") or 15)

    for day in days:
        if not isinstance(day, dict):
            continue
        ranges = day.get("pickup_index_ranges") or []
        if not ranges:
            continue
        first = ranges[0]
        if not isinstance(first, dict) or "from" not in first:
            continue
        idx = int(first["from"])
        date_str = day.get("date") or ""
        if not date_str:
            continue
        # date_str: "YYYY-MM-DD"; idx is # of `precision`-minute slots
        # from midnight in the restaurant's local timezone.
        try:
            y, mo, d = date_str.split("-")
            mins = idx * precision
            hh, mm = divmod(mins, 60)
            return f"{y}{mo}{d}{hh:02d}{mm:02d}"
        except (ValueError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Toast extractor
# ---------------------------------------------------------------------------
# Toast online ordering (order.toasttab.com) sits behind Cloudflare bot
# detection — vanilla headless Chromium gets a 403 challenge page.
# playwright-stealth patches the browser fingerprint enough to slip
# through. The full menu is server-side rendered and embedded in
# window.__APOLLO_STATE__ as ~600KB of GraphQL cache; we regex-extract
# MenuItem and MenuGrouping records rather than parse the full JSON
# (the brace structure is hairy with escape sequences).
#
# Size variants don't live in the initial Apollo state — only the
# `prices` array (e.g. [low, high, default]) without size labels. To
# get labeled sizes we replay Toast's MenuItemDetails GraphQL call for
# each item with multi-value prices. We reuse the Playwright context's
# cookies (and Cloudflare clearance) for the GraphQL replay.
#
# MenuItem fields used: name, itemGroupGuid, prices[]
# MenuGrouping fields used: name, guid

_TOAST_GROUP_RE = _re_allhungry.compile(
    r'"__typename":"MenuGrouping","name":"([^"]+)","guid":"([a-f0-9-]+)"'
)
_TOAST_ITEM_START_RE = _re_allhungry.compile(r'"__typename":"MenuItem"')
_TOAST_NAME_RE = _re_allhungry.compile(r'"name":"([^"]+)"')
_TOAST_GROUP_GUID_RE = _re_allhungry.compile(r'"itemGroupGuid":"([a-f0-9-]+)"')
_TOAST_GUID_RE = _re_allhungry.compile(r'"guid":"([a-f0-9-]+)"')
_TOAST_PRICES_RE = _re_allhungry.compile(r'"prices":\[([0-9.,]+)\]')
_TOAST_RESTAURANT_GUID_RE = _re_allhungry.compile(
    r'"RestaurantLocation:([a-f0-9-]+)"'
)

# Persisted query hash for MenuItemDetails (Apollo client version 3297
# as of 2026-05-02). When Toast bumps client version, this hash may
# change and the size-variant fetch will return errors — the extractor
# logs EXTRACTOR_FAILURE and the self-heal agent picks it up.
_TOAST_PQ_HASH = "4590a1ec01f58177c81719d60f26d9b959a17673"
_TOAST_GRAPHQL_URL = (
    "https://ws-api.toasttab.com/do-federated-gateway/v1/graphql"
)


def _extract_toast_via_html(url: str, place_name: str) -> List[Dict[str, Any]]:
    """Pull Toast menu from window.__APOLLO_STATE__ in initial HTML.
    Items with single-value `prices` emit one row at the base price.
    Items with multi-value `prices` get a per-item GraphQL replay to
    pull the labeled size options. ~12 sec base + ~0.5s per multi-
    price item."""
    try:
        from playwright_stealth import Stealth
    except ImportError:
        log.warning(
            "Toast: playwright-stealth not installed — "
            "Cloudflare will block. Install with `pip install "
            "playwright-stealth`."
        )
        return []
    from playwright.sync_api import sync_playwright
    import datetime as _dt

    rows: List[Dict[str, Any]] = []

    with Stealth().use_sync(sync_playwright()) as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(10000)
            body = page.content()
            # Toast's GraphQL gateway requires a `toast-session-id`
            # header that's set by an early API call and stored on
            # window._session_id. Fetch it before any replay.
            try:
                session_id = page.evaluate(
                    "() => window._session_id || ''"
                ) or ""
            except Exception:
                session_id = ""

            # Build guid → category name map
            guid_to_cat: Dict[str, str] = {}
            for m in _TOAST_GROUP_RE.finditer(body):
                name = m.group(1).encode("utf-8", "replace").decode(
                    "unicode_escape", "replace",
                ).strip()
                guid = m.group(2)
                if guid not in guid_to_cat:
                    guid_to_cat[guid] = name

            # Restaurant guid for the GraphQL replay — embedded in the
            # Apollo state under "RestaurantLocation:<guid>"
            rg_m = _TOAST_RESTAURANT_GUID_RE.search(body)
            restaurant_guid = rg_m.group(1) if rg_m else ""

            # First pass: enumerate items, collect those needing
            # size expansion (multi-value prices)
            base_items: List[Dict[str, Any]] = []
            multi_price_items: List[Dict[str, Any]] = []
            seen: set = set()
            for m in _TOAST_ITEM_START_RE.finditer(body):
                block = body[m.start():m.start() + 800]
                guid_m = _TOAST_GUID_RE.search(block)
                if not guid_m:
                    continue
                guid = guid_m.group(1)
                if guid in seen:
                    continue
                seen.add(guid)

                name_m = _TOAST_NAME_RE.search(block)
                prices_m = _TOAST_PRICES_RE.search(block)
                if not (name_m and prices_m):
                    continue
                try:
                    prices = [float(x) for x in prices_m.group(1).split(",")
                              if x.strip()]
                except ValueError:
                    continue
                if not prices or prices[0] <= 0:
                    continue

                gguid_m = _TOAST_GROUP_GUID_RE.search(block)
                group_guid = gguid_m.group(1) if gguid_m else ""
                cat = guid_to_cat.get(group_guid, "Menu")
                rec = {
                    "guid": guid,
                    "group_guid": group_guid,
                    "name": name_m.group(1),
                    "category": cat,
                    "prices": prices,
                }
                # >1 unique price → has size variants. prices[0] ==
                # prices[1] just means [base, base] — single size.
                if len(set(prices)) > 1:
                    multi_price_items.append(rec)
                else:
                    base_items.append(rec)

            # Emit base rows for single-price items
            for it in base_items:
                rows.append({
                    "name": it["name"],
                    "price_cents": int(round(it["prices"][0] * 100)),
                    "category": it["category"],
                })

            # Replay MenuItemDetails for each multi-price item to pull
            # labeled size options. Skip if we couldn't extract the
            # restaurant guid or the session id (means the page didn't
            # fully load).
            if restaurant_guid and session_id and multi_price_items:
                now_iso = _dt.datetime.utcnow().strftime(
                    "%Y-%m-%dT%H:%M:%S+0000",
                )
                expanded = 0
                for it in multi_price_items:
                    try:
                        size_rows = _toast_fetch_size_variants(
                            ctx, restaurant_guid, session_id, it,
                            now_iso, url,
                        )
                    except Exception as e:
                        log.debug(
                            "Toast size fetch failed for %r: %s",
                            it["name"], e,
                        )
                        size_rows = []
                    if size_rows:
                        rows.extend(size_rows)
                        expanded += 1
                    else:
                        # Fall back to base price only
                        rows.append({
                            "name": it["name"],
                            "price_cents": int(round(it["prices"][0] * 100)),
                            "category": it["category"],
                        })
                log.info(
                    "Toast: %s — expanded sizes for %d/%d multi-price items",
                    place_name, expanded, len(multi_price_items),
                )
            else:
                # No GraphQL replay possible — emit base prices
                for it in multi_price_items:
                    rows.append({
                        "name": it["name"],
                        "price_cents": int(round(it["prices"][0] * 100)),
                        "category": it["category"],
                    })
        finally:
            b.close()

    log.info("Toast: %s → %d rows extracted", place_name, len(rows))
    return rows


def _toast_fetch_size_variants(
    ctx, restaurant_guid: str, session_id: str,
    item: Dict[str, Any], now_iso: str, page_url: str,
) -> List[Dict[str, Any]]:
    """POST a MenuItemDetails GraphQL request and parse out size-group
    options. Returns one row per size, or [] if no size group found
    (caller falls back to base price)."""
    payload = [{
        "operationName": "MenuItemDetails",
        "variables": {
            "input": {
                "itemGuid": item["guid"],
                "itemGroupGuid": item["group_guid"],
                "restaurantGuid": restaurant_guid,
                "dateTime": now_iso,
            },
            "nestingLevel": 10,
        },
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": _TOAST_PQ_HASH,
            },
        },
    }]
    resp = ctx.request.post(
        _TOAST_GRAPHQL_URL,
        data=_json.dumps(payload),
        headers={
            "Content-Type": "application/json",
            "Accept": "*/*",
            "apollographql-client-name": "sites-web-client",
            "apollographql-client-version": "3297",
            "toast-graphql-operation": "MenuItemDetails",
            "toast-persistent-query-hash": _TOAST_PQ_HASH,
            "toast-session-id": session_id,
            "Referer": page_url,
        },
        timeout=8000,
    )
    if resp.status != 200:
        return []
    try:
        data = _json.loads(resp.text())
    except _json.JSONDecodeError:
        return []
    if not isinstance(data, list) or not data:
        return []
    item_data = (data[0].get("data") or {}).get("menuItemDetails") or {}
    mod_groups = item_data.get("modifierGroups") or []
    # Find the size group — name "Size" or contains "Size", with
    # min=1, max=1 and price-bearing options
    for mg in mod_groups:
        if not isinstance(mg, dict):
            continue
        gname = (mg.get("name") or "").lower()
        if mg.get("minSelections") != 1:
            continue
        if mg.get("maxSelections") != 1:
            continue
        opts = mg.get("items") or mg.get("modifiers") or []
        priced = [(o.get("name"), o.get("price"))
                  for o in opts if isinstance(o, dict)
                  and (o.get("price") or 0) > 0]
        # Heuristic: this is a size group
        if "size" not in gname and len(priced) < 2:
            continue
        if not priced:
            continue
        rows: List[Dict[str, Any]] = []
        for opt_name, opt_price in priced:
            if not opt_name:
                continue
            rows.append({
                "name": f"{item['name']} {opt_name}".strip(),
                "price_cents": int(round(float(opt_price) * 100)),
                "category": item["category"],
            })
        if rows:
            return rows
    return []


# ---------------------------------------------------------------------------
# DoorDash Storefront extractor
# ---------------------------------------------------------------------------
# DoorDash Storefront (`order.online/store/<id>`) is DoorDash's white-label
# ordering platform sold to restaurants for direct ordering on their own
# sites — NOT the public DoorDash marketplace (doordash.com/store/...),
# which is a different surface with its own auth requirements.
#
# Like Toast, the page sits behind Cloudflare bot detection — vanilla
# Playwright gets a 403 challenge. playwright-stealth slips through.
# Once past Cloudflare, the menu is rendered as plain HTML in the
# initial response: each item has
#   <div data-testid="MenuItem" data-item-id="<id>">
#     ...
#     <button aria-label="<name> $<price>" ...>
# So a single regex over `aria-label` extracts every item with name +
# price. Categories are plain `<h2>` headers in document order; we walk
# the HTML linearly and assign each item to the most recent <h2> seen.
# Carousel sections like "Featured Items" / "Most Ordered" are skipped
# (they duplicate items already listed under their real category).
#
# Size variants live in optionLists fetched via the per-item `itemPage`
# GraphQL query. For the common case where sizes are baked into item
# names (e.g. "Mozzarella Sticks 6 Pieces"), the base extraction is
# already complete. A future enhancement can replay itemPage for items
# where the page indicates HAS_NESTED_OPTIONS — same pattern as Toast's
# MenuItemDetails replay.

_DDSF_ITEM_RE = _re_allhungry.compile(
    r'data-testid="MenuItem"\s+data-item-id="(\d+)"'
    r'.*?aria-label="([^"]+?)\s+\$([\d.]+)"',
    _re_allhungry.DOTALL,
)
_DDSF_H2_RE = _re_allhungry.compile(r'<h2[^>]*>([^<]+?)</h2>')
_DDSF_URL_RE = _re_allhungry.compile(
    r'https?://(?:www\.)?order\.online/store/\d+', _re_allhungry.I,
)
# Carousel section headers that duplicate items from real categories;
# items found before/inside these sections are skipped to avoid dupes.
_DDSF_SKIP_SECTIONS = {
    "featured items", "most ordered", "popular items", "top picks",
    "recommended for you", "recommended", "favorites",
}


def _extract_doordash_storefront_via_html(
    url: str, place_name: str,
) -> List[Dict[str, Any]]:
    """Pull a DoorDash Storefront menu from the rendered HTML.
    ~10-12 sec per restaurant including Cloudflare bypass."""
    try:
        from playwright_stealth import Stealth
    except ImportError:
        log.warning(
            "DoorDash Storefront: playwright-stealth not installed — "
            "Cloudflare will block. Install with `pip install "
            "playwright-stealth`.",
        )
        return []
    from playwright.sync_api import sync_playwright

    # Resolve custom domains that link/redirect to order.online
    target_url = url
    if "order.online/store/" not in url:
        try:
            main_html = _http_get(url, timeout=10)
        except Exception:
            main_html = None
        if main_html:
            m = _DDSF_URL_RE.search(main_html)
            if m:
                target_url = m.group(0)
                log.info(
                    "DoorDash Storefront: redirected from %s → %s",
                    url, target_url,
                )
        if "order.online/store/" not in target_url:
            return []

    with Stealth().use_sync(sync_playwright()) as p:
        b = p.chromium.launch(headless=True)
        try:
            ctx = b.new_context()
            page = ctx.new_page()
            page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(8000)
            body = page.content()
        finally:
            b.close()

    # Walk the HTML in order, tracking the current category from the
    # most recent <h2> header. Skip carousel sections; they duplicate
    # items that also appear under their real category below.
    rows: List[Dict[str, Any]] = []
    seen: set = set()

    # Find all h2 headers and item matches with their offsets, then
    # interleave by position so each item picks up its true category.
    headers = [(m.start(), m.group(1).strip())
               for m in _DDSF_H2_RE.finditer(body)]
    items = list(_DDSF_ITEM_RE.finditer(body))

    current_category = "Menu"
    skip_current = False
    h_idx = 0

    for item_m in items:
        item_pos = item_m.start()
        # Advance the category pointer to the latest h2 before this item
        while h_idx < len(headers) and headers[h_idx][0] < item_pos:
            current_category = headers[h_idx][1]
            skip_current = (
                current_category.lower() in _DDSF_SKIP_SECTIONS
            )
            h_idx += 1

        if skip_current:
            continue

        item_id = item_m.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)

        name = item_m.group(2).strip()
        try:
            price_cents = int(round(float(item_m.group(3)) * 100))
        except ValueError:
            continue
        if price_cents <= 0 or not name:
            continue

        rows.append({
            "name": name,
            "price_cents": price_cents,
            "category": current_category,
        })

    log.info(
        "DoorDash Storefront: %s → %d rows extracted",
        place_name, len(rows),
    )
    return rows
