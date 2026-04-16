# storage/menu_ocr_fallback.py
"""
Last-resort scrape path for competitor menus published as images.

Many small-restaurant websites put their menu on-page as a JPEG or PNG
(scanned PDF, hand-designed menu graphic, etc.). Apify actors, menus-r-us,
and JSON-LD all return 0 items for those sites. This module fills the gap:
fetch the page, find menu-likely images, run them through our existing
Vision Claude extractor.

Fires ONLY when all cheaper paths return nothing. ~$0.10-0.25 per call.
Slow (30-90s per competitor) so only worth it when everything else failed.

Heuristics for "menu-likely image":
  - <img> src or alt contains "menu"
  - <img> inside a section with "menu" in a nearby header
  - Common restaurant image paths: /menu/, /wp-content/.../menu*

Hard-capped at 5 images per competitor to bound cost.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

MAX_IMAGES_PER_SITE = 5
IMAGE_TIMEOUT = 30
DOWNLOAD_MAX_BYTES = 8 * 1024 * 1024  # 8 MB cap per image


def scrape_menu_via_image_ocr(
    site_url: str,
    place_name: str,
) -> List[Dict[str, Any]]:
    """Fetch site, find menu images, Vision-extract items. Returns [] on any failure."""
    html = _fetch_page(site_url)
    if not html:
        log.info("OCR fallback: could not fetch %s", site_url)
        return []

    image_urls = _find_menu_images(html, base_url=site_url)
    if not image_urls:
        log.info("OCR fallback: no menu images found on %s", site_url)
        return []

    log.info("OCR fallback: %d candidate menu images on %s", len(image_urls), site_url)

    all_items: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()
    for img_url in image_urls[:MAX_IMAGES_PER_SITE]:
        tmp_path = _download_to_temp(img_url)
        if not tmp_path:
            continue
        try:
            items = _extract_from_image(tmp_path, place_name)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        for it in items or []:
            key = (it.get("name") or "").strip().lower()
            if key and key not in seen_names:
                seen_names.add(key)
                all_items.append(it)

    log.info("OCR fallback: extracted %d deduped items for %s", len(all_items), place_name)
    return all_items


def _fetch_page(url: str) -> Optional[str]:
    """Reuse price_intel's page fetcher (handles Playwright fallback)."""
    try:
        from storage.price_intel import _fetch_page_content
        return _fetch_page_content(url)
    except Exception as e:
        log.warning("OCR fallback: _fetch_page_content failed on %s: %s", url, e)
        return None


_MENU_HINT_RE = re.compile(r"menu", re.IGNORECASE)
_IMG_TAG_RE = re.compile(r"<img\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"', re.IGNORECASE)


def _find_menu_images(html: str, base_url: str) -> List[str]:
    """Return image URLs that look like menu images. Deduped, in appearance order."""
    found: List[str] = []
    seen: Set[str] = set()

    for match in _IMG_TAG_RE.finditer(html):
        attrs_str = match.group(1)
        attrs: Dict[str, str] = {}
        for a in _ATTR_RE.finditer(attrs_str):
            attrs[a.group(1).lower()] = a.group(2)

        src = attrs.get("src") or attrs.get("data-src") or ""
        if not src:
            continue
        alt = attrs.get("alt", "")
        title = attrs.get("title", "")

        # Only keep obvious image formats
        src_lower = src.lower()
        if not any(src_lower.split("?")[0].endswith(ext) for ext in
                   (".jpg", ".jpeg", ".png", ".webp")):
            continue

        # Heuristic: src / alt / title mentions menu
        if not any(_MENU_HINT_RE.search(s) for s in (src, alt, title)):
            continue

        abs_url = urljoin(base_url, src)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        found.append(abs_url)

    return found


def _download_to_temp(url: str) -> Optional[str]:
    """Download an image to a temp file. Returns path or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ServLine/1.0"})
        with urllib.request.urlopen(req, timeout=IMAGE_TIMEOUT) as resp:
            data = resp.read(DOWNLOAD_MAX_BYTES + 1)
        if not data or len(data) > DOWNLOAD_MAX_BYTES:
            log.info("OCR fallback: skipping oversize/empty image %s", url)
            return None
        # Pick an extension based on URL
        ext = ".jpg"
        path = urlparse(url).path.lower()
        for candidate in (".jpg", ".jpeg", ".png", ".webp"):
            if path.endswith(candidate):
                ext = candidate
                break
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="menuocr_")
        os.close(fd)
        with open(tmp, "wb") as f:
            f.write(data)
        return tmp
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        log.info("OCR fallback: download failed for %s: %s", url, e)
        return None


def _extract_from_image(image_path: str, place_name: str) -> List[Dict[str, Any]]:
    """Run our Vision Claude extractor on a single menu image."""
    try:
        from storage.ai_menu_extract import extract_menu_items_via_claude
    except Exception as e:
        log.warning("OCR fallback: ai_menu_extract unavailable: %s", e)
        return []

    try:
        raw = extract_menu_items_via_claude(
            ocr_text="",  # vision-only mode
            image_path=image_path,
        )
    except Exception as e:
        log.warning("OCR fallback: extract raised for %s: %s", place_name, e)
        return []

    if not raw:
        return []

    # Normalize to the shape the rest of the pipeline expects.
    out: List[Dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        name = (it.get("name") or "").strip()
        if not name:
            continue
        price_cents = it.get("price_cents")
        if price_cents is None:
            price = it.get("price")
            if isinstance(price, (int, float)):
                price_cents = int(round(float(price) * 100))
            else:
                price_cents = 0
        out.append({
            "name": name,
            "price_cents": int(price_cents or 0),
            "category": (it.get("category") or "Other").strip() or "Other",
        })
    return out
