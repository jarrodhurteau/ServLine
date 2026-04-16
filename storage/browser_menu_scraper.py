# storage/browser_menu_scraper.py
"""
Browser-driven menu extraction — the universal scraper.

Opens a URL in headless Chromium (Playwright), waits for JS to render,
takes a full-page screenshot, then passes the screenshot to our existing
Vision Claude menu extractor (the same pipeline that handles user menu
uploads).

This replaces the Apify actors, menus-r-us, and JSON-LD paths for sites
where those fail. Works on:
  - Image-based menus (scanned PDFs, JPEG uploads, photo menus)
  - JS-rendered SPAs (React, Vue, weird CMS builders like Duda/Wix)
  - Pages with server-side HTML we just couldn't parse
  - Even DoorDash/Grubhub pages — we just screenshot what they render

Cost per call: ~$0.02-0.05 (Claude Vision on a 1-2MP screenshot)
Time: ~30-60s (Playwright cold-start + page load + screenshot + extract)

Dual-use: also designed to power a future "Import menu from website URL"
option for restaurant owners. Same code path, different caller.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

# Chromium viewport — tall enough that Playwright's full_page screenshot
# captures scrollable content without truncation issues on very long pages.
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 1024

# Full-page screenshots can get huge on long menus. Claude Vision's
# 5MB cap applies to the BASE64-encoded payload, which is ~1.33x the raw
# file. A 4.5MB PNG becomes a 6MB base64 upload and gets rejected. Set
# the threshold at 3.6MB raw so we stay under 5MB after encoding.
SCREENSHOT_MAX_BYTES = 3_600_000
NAV_TIMEOUT_MS = 45_000  # slow sites (Ferrentino's) need >25s
SETTLE_WAIT_MS = 2_500

# Common URL patterns restaurants use for their menu page. Tried in
# order when the nav has no obvious "menu" link.
_COMMON_MENU_SUBPATHS = ("/menu", "/our-menu", "/menus", "/food", "/dinner-menu", "/order")

# Minimum $-signs we expect on a real menu page. Screenshots with fewer
# probably captured a landing page / error / splash instead.
MIN_PRICE_MARKERS = 3

# Maximum sub-menu pages to crawl per site. Real restaurants rarely split
# menus across more than 3-4 pages (lunch / dinner / drinks / desserts).
MAX_SUBPAGES = 4

# Words that usually indicate a menu page link in nav/anchors
_MENU_LINK_HINTS = (
    "menu", "food", "dine-in", "order", "dinner", "lunch", "breakfast",
    "takeout", "drinks", "beverages", "desserts", "appetizers", "sides",
    "specials", "pizza", "grinders", "subs", "burgers", "wings", "salads",
    "wraps", "entrees", "pasta", "our menu", "full menu", "view menu",
)

# Overlay dismiss selectors — consent banners, age gates, newsletters.
# Tried in order; first hit wins. Each must be clickable within 500ms.
_OVERLAY_SELECTORS = (
    # Cookie consent
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
    "button:has-text('Got it')",
    "button:has-text('OK')",
    # Age gate
    "button:has-text('I am 21')",
    "button:has-text('Yes, I am 21')",
    "button:has-text('Enter')",
    # Newsletter / promo
    "button:has-text('No thanks')",
    "button:has-text('Maybe later')",
    "button:has-text('Not now')",
    # Generic close
    "button[aria-label='Close']",
    "button[aria-label='close']",
    "[aria-label='Dismiss']",
    "[id*='cookie'] button",
    "[class*='cookie'] button",
    "[class*='consent'] button",
    "[class*='modal'] button[class*='close']",
    "[class*='popup'] button[class*='close']",
)


def scrape_menu_via_browser(
    url: str,
    place_name: Optional[str] = None,
    *,
    navigate_to_menu: bool = True,
    max_subpages: int = MAX_SUBPAGES,
) -> List[Dict[str, Any]]:
    """
    Render a restaurant site, screenshot its menu(s), extract items via
    Claude Vision. Returns [] on any failure.

    Quality passes:
      1. Navigate to the menu page if the URL isn't already one.
      2. Collect all menu-related sub-page links from nav (lunch, dinner,
         drinks). Screenshot each, extract, merge.
      3. Dismiss cookie / age / newsletter overlays before screenshotting.
      4. Scroll to bottom so infinite-scroll content is in the screenshot.
      5. Sanity check: reject screenshots that don't contain price markers.
      6. Retry once with extra settle time if first capture looks empty.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.warning("browser scraper: playwright not installed")
        return []

    screenshots: List[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                # Two-stage navigation: DOM first (fast, most sites), then
                # give JS time to render. Falls back to domcontentloaded
                # if networkidle doesn't settle within the timeout.
                try:
                    page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                    page.wait_for_timeout(SETTLE_WAIT_MS)
                    try:
                        page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass  # good enough with domcontentloaded
                except Exception as e:
                    log.warning("browser scraper: initial nav to %s failed: %s", url, e)
                    raise
                _try_dismiss_overlays(page)

                # Figure out which pages to screenshot. Build a list
                # starting with the best menu page. Dedupe by path (fragments
                # like #Sides don't render a different page).
                def _canon(u: str) -> str:
                    parsed = urlparse(u)
                    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

                pages_to_capture: List[str] = []
                seen_canon: set = set()

                def _add_page(u: str) -> None:
                    c = _canon(u)
                    if c in seen_canon:
                        return
                    seen_canon.add(c)
                    pages_to_capture.append(u)

                if navigate_to_menu and not _looks_like_menu_url(url):
                    best_menu_href = _find_menu_link(page)
                    if best_menu_href:
                        _add_page(best_menu_href)
                        log.info("browser scraper: primary menu page %s", best_menu_href)
                    else:
                        # Common URL pattern fallback. Sites often have
                        # /menu even if the nav doesn't link it textually
                        # (e.g. link is an image).
                        parsed = urlparse(url)
                        origin = f"{parsed.scheme}://{parsed.netloc}"
                        for sub in _COMMON_MENU_SUBPATHS:
                            _add_page(origin + sub)
                        log.info("browser scraper: no menu link found, trying common subpaths")
                _add_page(url)

                # Discover sub-menu pages (lunch, dinner, drinks, etc.)
                if max_subpages > 1:
                    for href in _find_all_menu_subpages(page, base_url=url):
                        _add_page(href)
                        if len(pages_to_capture) >= max_subpages:
                            break

                log.info(
                    "browser scraper: %d distinct page(s) queued for %s: %s",
                    len(pages_to_capture), url, pages_to_capture,
                )

                # Capture each page. Keep only screenshots that pass the
                # sanity check so we don't waste Vision calls on junk.
                # PDF URLs are handled separately (Playwright downloads
                # them instead of rendering).
                for target in pages_to_capture[:max_subpages]:
                    if target.lower().split("?")[0].endswith(".pdf"):
                        pdf_shots = _render_pdf_to_screenshots(target)
                        screenshots.extend(pdf_shots)
                    else:
                        shot = _capture_page(page, target)
                        if shot:
                            screenshots.append(shot)
            finally:
                browser.close()
    except Exception as e:
        log.warning("browser scraper: playwright session failed for %s: %s", url, e)

    if not screenshots:
        return []

    # Extract from each screenshot, merge + dedupe by (name, price_cents).
    all_items: List[Dict[str, Any]] = []
    seen: set = set()
    for shot in screenshots:
        try:
            items = _extract_from_screenshot(shot, place_name or "")
        except Exception as e:
            log.warning("browser scraper: extract failed on %s: %s", shot, e)
            items = []
        for it in items:
            key = ((it.get("name") or "").strip().lower(), it.get("price_cents") or 0)
            if key[0] and key not in seen:
                seen.add(key)
                all_items.append(it)
        try: os.unlink(shot)
        except OSError: pass

    log.info("browser scraper: %d unique items from %d page(s) for %s",
             len(all_items), len(screenshots), url)
    return all_items


def _capture_page(page, target_url: str) -> Optional[str]:
    """Navigate to `target_url`, scroll to bottom, screenshot, sanity-check.
    Returns temp-file path on success or None if the page doesn't look
    like a real menu (too few price markers)."""
    try:
        page.goto(target_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        page.wait_for_timeout(SETTLE_WAIT_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
    except Exception as e:
        log.info("browser scraper: nav to %s failed: %s", target_url, e)
        return None

    _try_dismiss_overlays(page)
    _scroll_to_bottom(page)
    _try_dismiss_overlays(page)  # new overlays may appear after scroll

    # Advisory sanity check: if we see very few price markers AND the URL
    # doesn't look like a menu URL, likely a landing page — skip to save
    # a Vision call. Menu-looking URLs always get captured; Vision itself
    # is the source of truth (some sites render prices as images or
    # data-attributes that our regex misses).
    if not _looks_like_menu_url(target_url) and not _page_looks_like_menu(page):
        log.info(
            "browser scraper: %s is not a menu URL AND has no $ markers — skipping",
            target_url,
        )
        return None

    fd, shot_path = tempfile.mkstemp(suffix=".png", prefix="menuscrn_")
    os.close(fd)
    try:
        page.screenshot(path=shot_path, full_page=True)
    except Exception as e:
        log.warning("browser scraper: screenshot failed for %s: %s", target_url, e)
        try: os.unlink(shot_path)
        except OSError: pass
        return None

    size = os.path.getsize(shot_path)
    if size < 10_000:
        log.info("browser scraper: screenshot too small (%d bytes), rejecting", size)
        try: os.unlink(shot_path)
        except OSError: pass
        return None
    # Claude Vision rejects images > 5MB. Long menu pages regularly hit
    # this — compress to JPEG with step-down quality until we fit.
    if size > SCREENSHOT_MAX_BYTES:
        compressed = _compress_for_vision(shot_path)
        if compressed and compressed != shot_path:
            try: os.unlink(shot_path)
            except OSError: pass
            shot_path = compressed
            size = os.path.getsize(shot_path)
            log.info("browser scraper: compressed to %d bytes", size)
    log.info("browser scraper: captured %s (%d bytes) from %s", shot_path, size, target_url)
    return shot_path


def _render_pdf_to_screenshots(pdf_url: str) -> List[str]:
    """Download a PDF menu and convert each page to a JPEG screenshot.

    Some restaurants (Kaptain Jimmy's, older sites) publish their menu
    as one or more PDFs. Playwright can't navigate to them (triggers a
    download). We fetch manually, render pages, feed each to Vision
    alongside regular HTML screenshots.

    Returns a list of temp-file paths (one per page). Empty on failure.
    """
    try:
        import urllib.request as _ur
        req = _ur.Request(pdf_url, headers={"User-Agent": "ServLine/1.0"})
        with _ur.urlopen(req, timeout=30) as resp:
            pdf_bytes = resp.read(16 * 1024 * 1024 + 1)
        if len(pdf_bytes) > 16 * 1024 * 1024:
            log.warning("pdf too big (>16MB), skipping: %s", pdf_url)
            return []
    except Exception as e:
        log.info("pdf download failed for %s: %s", pdf_url, e)
        return []

    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        log.warning("pdf2image not installed — can't render PDF menus")
        return []

    try:
        # DPI 150 keeps file size modest while staying legible for Vision
        pages = convert_from_bytes(pdf_bytes, dpi=150, fmt="jpeg")
    except Exception as e:
        log.warning("pdf2image failed for %s: %s (is Poppler installed?)", pdf_url, e)
        return []

    out: List[str] = []
    for i, pil_img in enumerate(pages):
        try:
            fd, path = tempfile.mkstemp(suffix=".jpg", prefix=f"pdfpage{i}_")
            os.close(fd)
            pil_img.save(path, "JPEG", quality=85, optimize=True)
            size = os.path.getsize(path)
            if size > SCREENSHOT_MAX_BYTES:
                # Re-save with lower quality. PDF pages tend to be clean,
                # so we can drop quality aggressively without losing readability.
                for q in (70, 55, 40):
                    pil_img.save(path, "JPEG", quality=q, optimize=True)
                    if os.path.getsize(path) <= SCREENSHOT_MAX_BYTES:
                        break
            out.append(path)
        except Exception as e:
            log.warning("pdf page save failed: %s", e)
    log.info("pdf %s rendered to %d pages", pdf_url, len(out))
    return out


def _compress_for_vision(png_path: str) -> Optional[str]:
    """Convert a PNG screenshot to JPEG at step-down quality until under
    the Vision size limit. Returns the JPEG path, or the original path
    if compression failed."""
    try:
        from PIL import Image
    except ImportError:
        log.warning("browser scraper: Pillow not installed, cannot compress oversize screenshot")
        return png_path
    try:
        img = Image.open(png_path).convert("RGB")
    except Exception as e:
        log.warning("browser scraper: PIL couldn't open %s: %s", png_path, e)
        return png_path

    # Also cap total pixel dimensions — Vision resizes anything bigger
    # anyway, and we save bandwidth by downscaling up front.
    MAX_DIM = 4096
    w, h = img.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        log.info("browser scraper: downscaled from %dx%d to %dx%d", w, h, img.size[0], img.size[1])

    out_fd, out_path = tempfile.mkstemp(suffix=".jpg", prefix="menuscrn_")
    os.close(out_fd)
    for quality in (90, 80, 70, 60, 50, 40):
        try:
            img.save(out_path, "JPEG", quality=quality, optimize=True)
        except Exception as e:
            log.warning("browser scraper: JPEG save failed at Q=%d: %s", quality, e)
            continue
        size = os.path.getsize(out_path)
        if size <= SCREENSHOT_MAX_BYTES:
            log.info("browser scraper: compressed at quality=%d -> %d bytes", quality, size)
            return out_path
    log.warning("browser scraper: even Q=40 exceeds %d bytes; sending as-is",
                SCREENSHOT_MAX_BYTES)
    return out_path


def _scroll_to_bottom(page) -> None:
    """Scroll through the page in steps so lazy-loaded images/menus render.
    Restaurants often use LazyLoad/IntersectionObserver for menu sections."""
    try:
        page.evaluate(
            """
            async () => {
              const sleep = ms => new Promise(r => setTimeout(r, ms));
              const step = 600;
              let y = 0;
              while (y < document.body.scrollHeight) {
                window.scrollTo(0, y);
                await sleep(150);
                y += step;
              }
              window.scrollTo(0, 0);
            }
            """
        )
        page.wait_for_timeout(500)
    except Exception as e:
        log.debug("browser scraper: scroll-to-bottom failed: %s", e)


def _page_looks_like_menu(page) -> bool:
    """Heuristic: real menu pages have several $ markers. Landing pages,
    error pages, and splash pages typically have 0-2."""
    try:
        html = page.content()
    except Exception:
        return False
    price_count = len(re.findall(r"\$\s?\d+(?:\.\d{1,2})?", html))
    return price_count >= MIN_PRICE_MARKERS


def _find_all_menu_subpages(page, base_url: str) -> List[str]:
    """Return all in-site anchors that look like menu sub-pages, scored."""
    try:
        anchors = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()}))",
        )
    except Exception:
        return []
    base_host = urlparse(base_url).hostname or ""
    scored = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        text = (a.get("text") or "").strip().lower()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        host = urlparse(href).hostname or ""
        if host and base_host and host != base_host:
            continue  # off-site
        score = 0
        for hint in _MENU_LINK_HINTS:
            if hint in text:
                score += 3
            if hint in href.lower():
                score += 2
        if text in ("menu", "full menu", "our menu"):
            score += 5
        if score > 0:
            scored.append((score, href))
    # Dedupe while preserving highest score for each URL
    best: Dict[str, int] = {}
    for score, href in scored:
        if score > best.get(href, 0):
            best[href] = score
    return [h for h, _ in sorted(best.items(), key=lambda kv: -kv[1])]


def _looks_like_menu_url(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    return any(hint in path for hint in ("/menu", "/food", "/dinner", "/lunch", "/breakfast"))


def _find_menu_link(page) -> Optional[str]:
    """Look for the most menu-like link on the page. Returns absolute URL or None."""
    try:
        anchors = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: (e.textContent || '').trim()}))",
        )
    except Exception:
        return None

    scored = []
    for a in anchors:
        href = (a.get("href") or "").strip()
        text = (a.get("text") or "").strip().lower()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        score = 0
        for hint in _MENU_LINK_HINTS:
            if hint in text:
                score += 3
            if hint in href.lower():
                score += 2
        # Bonus if text is just "Menu" on its own (classic nav link)
        if text == "menu":
            score += 5
        if score > 0:
            scored.append((score, href))

    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def _try_dismiss_overlays(page) -> None:
    """Click through common cookie / age-gate / popup dialogs so they
    don't overlay the menu screenshot. Best-effort, silent on failure.
    Stops after 3 successful dismissals to avoid clicking through the
    actual menu UI."""
    dismissed = 0
    for sel in _OVERLAY_SELECTORS:
        if dismissed >= 3:
            break
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=400):
                el.click(timeout=800)
                page.wait_for_timeout(250)
                dismissed += 1
        except Exception:
            continue


def _extract_from_screenshot(screenshot_path: str, place_name: str) -> List[Dict[str, Any]]:
    """Send the screenshot through our Vision Claude extractor."""
    try:
        from storage.ai_menu_extract import extract_menu_items_via_claude
    except Exception as e:
        log.warning("browser scraper: ai_menu_extract unavailable: %s", e)
        return []

    try:
        raw = extract_menu_items_via_claude(
            ocr_text="",  # screenshot-only mode
            image_path=screenshot_path,
        )
    except Exception as e:
        log.warning("browser scraper: Vision extract raised: %s", e)
        return []

    if not raw:
        return []

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)

        # Resolve price: prefer top-level, fall back to the lowest
        # `sizes[].price` (many multi-size items have price=0 at the top
        # level with real prices nested under sizes).
        price_cents = it.get("price_cents")
        if price_cents is None:
            price = it.get("price")
            if isinstance(price, (int, float)) and price > 0:
                price_cents = int(round(float(price) * 100))
        if not price_cents:
            sizes = it.get("sizes") or []
            if isinstance(sizes, list):
                size_prices = []
                for s in sizes:
                    if isinstance(s, dict):
                        sp = s.get("price")
                        if isinstance(sp, (int, float)) and sp > 0:
                            size_prices.append(float(sp))
                if size_prices:
                    price_cents = int(round(min(size_prices) * 100))

        out.append({
            "name": name,
            "price_cents": int(price_cents or 0),
            "category": (it.get("category") or "Other").strip() or "Other",
        })
    return out
