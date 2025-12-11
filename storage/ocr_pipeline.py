# storage/ocr_pipeline.py
"""
ServLine OCR Pipeline — Phase 3 (Segmentation + Category Inference) + Phase 4 pt.1–6

Phase 2 kept:
- Re-raster PDF pages at 400 DPI for sharper glyphs.
- Apply preprocess_page() → CLAHE + adaptive threshold + denoise + unsharp + deskew.
- Split two-column layouts with split_columns().
- Word→Line→Block grouping for legacy consumers.

Phase 3 pt.1:
- Text-block segmentation via ocr_utils.group_text_blocks()
- Preview-friendly blocks (xyxy + merged_text + block_type) for debug overlay

Phase 3 pt.2 (Day 23):
- Category inference on text blocks, now delegated to storage/category_infer.py
- Adds: category, category_confidence, rule_trace to text_blocks
- Mirrors category & confidence to preview_blocks for overlay UI

Phase 3 pt.4 (Day 24):
- Two-column merge helper to pair left-column names with right-column prices
  before category inference + AI cleanup.

Phase 3 pt.4b:
- Adaptive column splitting based on page width (helps tight gutters like
  real-world pizza menus) + logging of column counts per page.
- For very wide pages (>= 2400px) where split_columns() only finds 1 column,
  force a simple left/right half split as a fallback.

Phase 3 pt.6 (Day 25):
- Multi-price / variant extraction at the text-block level:
  - Detects price candidates in merged_text.
  - Builds OCRPriceCandidate + OCRVariant lists.
  - Attaches price_candidates + variants to text_blocks and mirrors onto preview_blocks.

Phase 4 pt.1 (Day 26):
- Semantic Block Understanding (block classifier + noise collapse + heading detection):
  - Classify each text block into role: heading / item_name / description / price / meta / noise
  - Drop high-garbage "noise" blocks while preserving prices and headings
  - Expose role + is_heading flags for downstream category + UI layers.

Phase 4 pt.2 (Day 26):
- Multi-line Description Reconstruction:
  - Clean bullets / leading junk characters
  - Join broken lines within a block into smoother sentences
  - Fix common hyphenated line splits ("CHICK-\nEN" → "CHICKEN")
  - Normalize whitespace so AI cleanup sees a cleaner text stream.

Phase 4 pt.3 (Day 27):
- Variant & Size Intelligence:
  - Analyze OCRVariant labels for size/count/wing-count patterns and flavor/style tokens.
  - Normalize sizes (e.g. 10"/10 in → "10in", 6 pc → "6pc").
  - Classify variants by kind: size | flavor | style | other.
  - Assign group keys so downstream can easily cluster variant families.

Phase 4 pt.4 (Day 27, downstream):
- Category Hierarchy v1 (storage/category_hierarchy.py):
  - Item-level inference of subcategories (e.g., "Calzones", "Subs & Grinders").
  - Runs after text-block segmentation when converting blocks → items.

Phase 4 pt.5–6 (Day 28/30, downstream):
- Price Integrity Engine + Draft-friendly variants:
  - Analyze item prices per category + variant family.
  - Auto-correct obvious decimal-shift prices when safe (e.g., 3475 → 34.75).
  - Attach price_flags and corrected_price_cents into preview JSON and draft items.

Phase 4 pt.11–12 (prep):
- Preview blocks carry full hierarchy + category/variant/role metadata, ready
  for Structured Draft Output v2 and Superimport prep.
"""

from __future__ import annotations
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import pytesseract
from pytesseract import image_to_osd

from . import ocr_utils
from . import category_infer
from . import variant_engine
from .ocr_types import (
    Block,
    Line,
    Word,
    BBox,
    OCRPriceCandidate,
    OCRVariant,
    TextBlock,
    OCRBlock,
    PreviewItem,
    StructuredSection,
    StructuredMenuPayload,
)  # TypedDicts; Phase-2 + Phase-3/4 compatibility

# -----------------------------
# Tunable heuristics
# -----------------------------

DEFAULT_DPI = 400  # bumped from 300 for high-clarity raster
LOW_CONF_DROP = 55.0  # drop words with conf < 55
GRAYSCALE = True
CONTRAST = 1.15
UNSHARP_RADIUS = 1.0
UNSHARP_PERCENT = 120
UNSHARP_THRESHOLD = 3

# Robust Tesseract settings (Phase 7 scaffolding for multi-pass)
BASE_OCR_CONFIG = r"--oem 3 -c preserve_interword_spaces=1"
OCR_CONFIG = BASE_OCR_CONFIG + " --psm 6"

# Phase 7 pt.1–2 feature flags (env-controlled; default OFF)
ENABLE_VISION_PREPROCESS = os.getenv("OCR_ENABLE_VISION_PREPROCESS", "0") == "1"
VISION_DEBUG_DIR = os.getenv("OCR_VISION_DEBUG_DIR") or ""

ENABLE_MULTIPASS_OCR = os.getenv("OCR_ENABLE_MULTIPASS_OCR", "0") == "1"
MULTIPASS_PSMS: List[int] = [6]
MULTIPASS_ROTATIONS: List[int] = [0, 90, 180, 270]


_ALLOWED_CHARS = r"A-Za-z0-9\$\.\,\-\/&'\"°\(\):;#\+ "
_ALLOWED_RE = re.compile(f"[^{_ALLOWED_CHARS}]+")
_REPEAT3 = re.compile(r"(.)\1\1+")
_NO_VOWEL_LONG = re.compile(r"\b[b-df-hj-np-tv-z]{4,}\b", re.I)

# Lightweight heading detector to boost confidence when a block looks like a header
_HEADING_HINT = re.compile(r"^[A-Z][A-Z\s&/0-9\-]{2,}$")

# Simple money-ish detector for two-column pairing
_PRICE_RE = re.compile(r"\b\d{1,3}(?:\.\d{2})?\b")

# Simple "meta/address" hints (storefront fluff, hours, etc.)
_META_HINTS = [
    "phone", "tel", "fax", "address", "street", "st.", "ave", "avenue",
    "blvd", "boulevard", "rd.", "road", "ct.", "court", "ln", "lane",
    "hours", "open", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "sun-thu", "fri-sat",
    "delivery", "deliveries", "pickup", "take-out", "take out", "carry out",
    "free delivery", "dine in", "eat in",
    "plus tax", "tax not included", "no substitutions",
    "visa", "mastercard", "american express", "discover",
]


def _alpha_ratio(s: str) -> float:
    if not s:
        return 0.0
    a = sum(c.isalpha() for c in s)
    return a / max(1, len(s))


def _symbol_ratio(s: str) -> float:
    if not s:
        return 1.0
    sym = sum(not (c.isalnum() or c.isspace()) for c in s)
    return sym / max(1, len(s))


def _clean_token(text: str) -> str:
    if not text:
        return ""
    t = _ALLOWED_RE.sub(" ", text)
    t = _REPEAT3.sub(r"\1\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t


def _token_is_garbage(tok: str) -> bool:
    if not tok:
        return True
    if _alpha_ratio(tok) < 0.45 and not any(ch.isdigit() for ch in tok):
        return True
    if _NO_VOWEL_LONG.search(tok):
        return True
    if len(tok) > 28 and _alpha_ratio(tok) < 0.6:
        return True
    if len(tok) <= 2 and not any(ch.isalnum() for tok in [tok] for ch in tok):
        return True
    if _symbol_ratio(tok) > 0.35:
        return True
    return False


def _is_pricey_text(text: str) -> bool:
    """
    Heuristic to decide if a block is primarily a price column chunk.

    Designed to catch things like:
      "12.99", "$9.50", "9.99 12.99", "8" while
    avoiding clobbering full item lines like:
      "2 Large Pizzas 1 Topping 19.99"
    which contain lots of letters.
    """
    if not text:
        return False
    text = text.strip()
    if not text:
        return False

    digits = sum(c.isdigit() for c in text)
    letters = sum(c.isalpha() for c in text)

    if digits == 0:
        return False

    # Strong hints: explicit currency or clean price regex with few letters
    if "$" in text:
        # "$12.99" or "$ 12.99"
        return True
    if _PRICE_RE.search(text) and letters <= 4:
        return True

    # Mostly numeric with minimal letters: treat as price-y
    if letters == 0 and 1 <= digits <= 6:
        return True
    if letters <= 2 and digits >= 2 and len(text) <= 10:
        return True

    return False


# -----------------------------
# Phase 7 pt.1 — Vision preprocessing scaffold
# -----------------------------


def _vision_debug_save(image: Image.Image, page_index: int, column_index: Optional[int], stage: str) -> None:
    """
    Debug-save helper for the Vision layer.

    Controlled by OCR_VISION_DEBUG_DIR. If unset, this is a no-op.
    Files are written as:
      page{page_index:03d}_c{column_index}_{stage}.png
    """
    if not VISION_DEBUG_DIR:
        return

    try:
        debug_root = Path(VISION_DEBUG_DIR)
        debug_root.mkdir(parents=True, exist_ok=True)
        col_suffix = f"_c{column_index}" if column_index is not None else ""
        filename = f"page{page_index:03d}{col_suffix}_{stage}.png"
        out_path = debug_root / filename
        image.save(out_path)
    except Exception:
        # Debug hooks must never break OCR
        return


def _vision_grayscale_normalize(image: Image.Image) -> Image.Image:
    """
    Placeholder for grayscale normalization.

    Phase 7 pt.1 scaffold only — returns the input image unchanged.
    """
    return image


def _vision_unsharp_placeholder(image: Image.Image) -> Image.Image:
    """
    Placeholder for unsharp masking in the Vision layer.

    Phase 7 pt.1 scaffold only — returns the input image unchanged.
    """
    return image


def _vision_denoise_placeholder(image: Image.Image) -> Image.Image:
    """
    Placeholder for denoise step in the Vision layer.

    Phase 7 pt.1 scaffold only — returns the input image unchanged.
    """
    return image


def _vision_shadow_removal_placeholder(image: Image.Image) -> Image.Image:
    """
    Placeholder for shadow-removal in the Vision layer.

    Phase 7 pt.1 scaffold only — returns the input image unchanged.
    """
    return image


def _vision_adaptive_threshold_placeholder(image: Image.Image) -> Image.Image:
    """
    Placeholder for adaptive thresholding in the Vision layer.

    Phase 7 pt.1 scaffold only — returns the input image unchanged.
    """
    return image


def vision_preprocess(image: Image.Image, page_index: int, column_index: Optional[int] = None) -> Image.Image:
    """
    Unified Vision OCR preprocessing entrypoint (Phase 7 pt.1 scaffold).

    For now this is a thin wrapper around ocr_utils.preprocess_page(), plus:
      - Debug-save hooks for each conceptual stage
      - Placeholder functions for:
          grayscale normalize
          unsharp mask
          denoise
          shadow-removal
          adaptive threshold

    All placeholder functions are identity transforms, so even when
    OCR_ENABLE_VISION_PREPROCESS=1, the effective output remains identical
    to ocr_utils.preprocess_page(image, do_deskew=True).
    """
    base = ocr_utils.preprocess_page(image, do_deskew=True)
    _vision_debug_save(base, page_index, column_index, "base")

    gray = _vision_grayscale_normalize(base)
    _vision_debug_save(gray, page_index, column_index, "gray")

    sharp = _vision_unsharp_placeholder(gray)
    _vision_debug_save(sharp, page_index, column_index, "unsharp")

    denoised = _vision_denoise_placeholder(sharp)
    _vision_debug_save(denoised, page_index, column_index, "denoise")

    de_shadowed = _vision_shadow_removal_placeholder(denoised)
    _vision_debug_save(de_shadowed, page_index, column_index, "shadow")

    thresh = _vision_adaptive_threshold_placeholder(de_shadowed)
    _vision_debug_save(thresh, page_index, column_index, "thresh")

    return thresh



# -----------------------------
# Price / variant extraction (Phase 3 pt.6)
# -----------------------------

def _parse_price_to_cents(raw: str) -> Optional[int]:
    """Best-effort parse of a price-like string into integer cents."""
    raw = raw.strip().replace("$", "")
    if not raw:
        return None
    try:
        if "." in raw:
            dollars_str, cents_str = raw.split(".", 1)
            dollars = int(re.sub(r"[^\d]", "", dollars_str) or "0")
            cents = int(re.sub(r"[^\d]", "", cents_str)[:2] or "0")
        else:
            dollars = int(re.sub(r"[^\d]", "", raw) or "0")
            cents = 0
        return dollars * 100 + cents
    except Exception:
        return None


def _find_price_candidates_with_positions(text: str) -> List[Tuple[OCRPriceCandidate, int]]:
    """
    Scan text for price-like tokens and return (candidate, start_index) tuples.

    This powers both price_candidates and variant label extraction.
    """
    results: List[Tuple[OCRPriceCandidate, int]] = []
    if not text:
        return results

    for m in _PRICE_RE.finditer(text):
        raw = m.group(0)
        cents = _parse_price_to_cents(raw)
        # Default confidence for regex hit; may be refined later.
        base_conf = 0.9
        cand: OCRPriceCandidate = {"text": raw, "confidence": base_conf}
        if cents is not None:
            cand["price_cents"] = cents
        results.append((cand, m.start()))
    return results


_CONNECTOR_TOKENS = {
    "and",
    "or",
    "&",
    "+",
    "w/",
    "w",
    "with",
    "for",
}


def _build_variants_from_text(
    text: str,
    priced: List[Tuple[OCRPriceCandidate, int]],
) -> List[OCRVariant]:
    """
    Infer variant labels (e.g., 'Sm', 'Lg', '16"') from tokens immediately
    preceding each price. Returns OCRVariant list; intended mostly for 2+ prices.
    """
    variants: List[OCRVariant] = []
    if not text or not priced:
        return variants

    # Tokenize while preserving char positions
    tokens: List[Tuple[str, int, int]] = []
    for tm in re.finditer(r"\S+", text):
        tok = tm.group(0)
        tokens.append((tok, tm.start(), tm.end()))

    for cand, price_start in priced:
        # Collect tokens ending before the price
        prior_tokens = [t for t in tokens if t[2] <= price_start]
        label_parts: List[str] = []

        for tok, ts, te in reversed(prior_tokens):
            stripped = tok.strip(".,;:-")
            if not stripped:
                continue
            low = stripped.lower()

            # Skip if this token itself looks like a price
            if _PRICE_RE.fullmatch(stripped):
                continue
            if low in _CONNECTOR_TOKENS:
                continue

            label_parts.append(stripped)
            if len(label_parts) >= 2:
                break

        label = " ".join(reversed(label_parts)).strip()

        price_cents = cand.get("price_cents")
        if price_cents is None:
            price_cents = _parse_price_to_cents(cand["text"])
        if price_cents is None:
            # If we truly can't parse, skip this as a variant; the raw candidate still exists.
            continue

        variants.append(
            {
                "label": label,
                "price_cents": price_cents,
                "confidence": float(cand["confidence"]),
            }
        )

    # Only treat as variants if we actually got 2+ prices parsed
    if len(variants) < 2:
        return []
    return variants


def annotate_prices_and_variants_on_text_blocks(text_blocks: List[Dict[str, Any]]) -> None:
    """
    Mutate each text_block dict by adding:
      - price_candidates: List[OCRPriceCandidate]
      - variants: List[OCRVariant]  (only when we detect >= 2 good prices)

    This runs AFTER two-column merge + category inference so merged_text has
    both item + price bits.
    """
    for tb in text_blocks:
        merged = tb.get("merged_text") or tb.get("text") or ""
        if not merged:
            continue

        priced = _find_price_candidates_with_positions(merged)
        if not priced:
            continue

        # Always attach raw price_candidates, even when no variants
        tb["price_candidates"] = [c for (c, _pos) in priced]

        # Try to build variants; only keep if we got a plausible list
        variants = _build_variants_from_text(merged, priced)
        if variants:
            tb["variants"] = variants


# -----------------------------
# OCR primitives
# -----------------------------

def _ocr_page(im: Image.Image) -> Dict[str, List]:
    """
    Single-pass Tesseract OCR using the default OCR_CONFIG.

    This remains the baseline behavior for the pipeline and is used
    whenever multi-pass is disabled.
    """
    return pytesseract.image_to_data(
        im,
        output_type=pytesseract.Output.DICT,
        config=OCR_CONFIG,
    )


def _run_single_ocr_pass(image: Image.Image, psm: int, rotation: int) -> Dict[str, List]:
    """
    Single OCR pass for a specific (rotation, psm) combination.

    Phase 7 pt.2 scaffold: uses BASE_OCR_CONFIG with a dynamic --psm.
    """
    working = image
    if rotation != 0:
        working = image.rotate(rotation, expand=True)

    config = f"{BASE_OCR_CONFIG} --psm {psm}"
    data = pytesseract.image_to_data(
        working,
        output_type=pytesseract.Output.DICT,
        config=config,
    )

    tokens = len(data.get("text", []))
    print(
        f"[Multipass] rotation={rotation} psm={psm} tokens={tokens}"
    )

    return data


def fuse_multipass_results(passes: List[Dict[str, Any]]) -> Dict[str, List]:
    """
    Placeholder confidence fusion for multi-pass OCR (Phase 7 pt.2).

    For now this simply returns the first pass's data unchanged.
    Later this will consider per-word confidences and combine results
    across PSMs and rotations.
    """
    if not passes:
        raise ValueError("No OCR passes provided to fuse_multipass_results()")

    # Each element of `passes` is a dict[str, list] from image_to_data.
    return passes[0]  # type: ignore[return-value]


def run_multipass_ocr(image: Image.Image, page_index: int, column_index: int) -> Dict[str, List]:
    """
    Multi-pass OCR wrapper.

    When ENABLE_MULTIPASS_OCR is False, this is exactly equivalent to
    a call to _ocr_page(image). When enabled, it runs multiple passes
    over rotations and PSM values, then fuses them via the placeholder
    fuse_multipass_results() helper.
    """
    if not ENABLE_MULTIPASS_OCR:
        return _ocr_page(image)

    passes: List[Dict[str, List]] = []

    for rotation in MULTIPASS_ROTATIONS:
        for psm in MULTIPASS_PSMS:
            data = _run_single_ocr_pass(image, psm=psm, rotation=rotation)
            passes.append(data)

    fused = fuse_multipass_results(passes)
    tokens = len(fused.get("text", []))
    print(
        f"[Multipass] page={page_index} col={column_index} fused_tokens={tokens} "
        f"psms={MULTIPASS_PSMS} rotations={MULTIPASS_ROTATIONS}"
    )

    return fused


def _make_word(i: int, data: Dict[str, List], conf_floor: float = LOW_CONF_DROP) -> Optional[Word]:
    raw = (data["text"][i] or "").strip()
    try:
        conf_raw = float(data["conf"][i])
    except Exception:
        conf_raw = -1.0

    if conf_raw < conf_floor:
        return None

    cleaned = _clean_token(raw)
    if not cleaned or _token_is_garbage(cleaned):
        return None

    # More defensive around bbox dimensions
    x = int(data["left"][i])
    y = int(data["top"][i])
    try:
        w = int(data["width"][i])
        h = int(data["height"][i])
    except Exception:
        return None

    # Clamp negatives to 0
    if w < 0:
        w = 0
    if h < 0:
        h = 0

    # Skip zero / 1-pixel “ghost” words
    if w <= 1 or h <= 1:
        return None

    return {
        "text": cleaned,
        "bbox": {"x": x, "y": y, "w": w, "h": h},
        "conf": conf_raw,
    }


# -----------------------------
# Two-column merge helper
# -----------------------------

def _center_y(tb: Dict[str, Any]) -> float:
    bbox = tb.get("bbox") or {}
    return float(bbox.get("y", 0) + (bbox.get("h", 0) or 0) / 2.0)


def merge_two_column_rows(page_text_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Geometry-based pairing of item text blocks with price blocks.

    Instead of relying on there being exactly 2 columns, we:
      - Identify "pricey" blocks via _is_pricey_text(merged_text/text).
      - For each price block, look LEFT for the nearest non-price block whose
        vertical center is within ~1.2 * median block height and whose
        horizontal gap isn't huge.
      - When found, append the price text to that block's merged_text and
        drop the price block from the final list.

    This works even when split_columns() treats a panel as one column, as long
    as prices appear as mostly-numeric chunks to the right of their items.
    """
    if not page_text_blocks:
        return page_text_blocks

    # Only consider blocks that actually have a bbox
    with_bbox = [tb for tb in page_text_blocks if tb.get("bbox")]
    if not with_bbox:
        return page_text_blocks

    heights = [tb["bbox"].get("h", 0) or 0 for tb in with_bbox]
    heights = [h for h in heights if h > 0]
    if heights:
        median_h = ocr_utils.median([float(h) for h in heights])
    else:
        median_h = 20.0
    vert_tol = max(5.0, 1.2 * float(median_h))

    # Estimate page width from block extents
    max_x = max(tb["bbox"]["x"] + tb["bbox"]["w"] for tb in with_bbox)
    min_x = min(tb["bbox"]["x"] for tb in with_bbox)
    page_width = max_x - min_x
    # Don't allow pairing across the entire page; keep it local-ish.
    max_horiz_gap = max(60.0, page_width * 0.45)

    def _text_of(tb: Dict[str, Any]) -> str:
        return (tb.get("merged_text") or tb.get("text") or "").strip()

    # Split into price-ish and non-price blocks
    price_blocks: List[Dict[str, Any]] = []
    text_blocks: List[Dict[str, Any]] = []
    for tb in with_bbox:
        txt = _text_of(tb)
        if not txt:
            text_blocks.append(tb)
            continue
        if _is_pricey_text(txt):
            price_blocks.append(tb)
        else:
            text_blocks.append(tb)

    if not price_blocks or not text_blocks:
        return page_text_blocks

    consumed_ids: set[int] = set()

    for pb in price_blocks:
        if not pb.get("bbox"):
            continue
        txt_price = _text_of(pb)
        if not txt_price:
            continue

        cy = _center_y(pb)
        px_left = pb["bbox"]["x"]  # left edge of the price block

        best_candidate = None
        best_score: Optional[Tuple[float, float]] = None  # (dy, abs(dx_gap))

        for tb in text_blocks:
            if tb is pb or not tb.get("bbox"):
                continue
            txt_item = _text_of(tb)
            if not txt_item:
                continue
            # Don't pair two price blocks together
            if _is_pricey_text(txt_item):
                continue

            bbox = tb["bbox"]
            item_right = bbox["x"] + bbox["w"]
            dx_gap = px_left - item_right  # positive if price is right of item

            if dx_gap < -10.0:
                # Item is entirely to the right of this price; skip.
                continue
            if dx_gap > max_horiz_gap:
                # Too far away horizontally to be a plausible row mate.
                continue

            dy = abs(_center_y(tb) - cy)
            if dy > vert_tol:
                continue

            score = (dy, abs(dx_gap))
            if best_candidate is None or score < best_score:
                best_candidate = tb
                best_score = score

        if best_candidate is None:
            continue

        # Merge price text into the chosen item block
        base_text = _text_of(best_candidate)
        merged = (base_text + " " + txt_price).strip()
        best_candidate["merged_text"] = merged

        meta = best_candidate.setdefault("meta", {})
        meta["two_column_merged"] = True
        meta["two_column_partner_id"] = pb.get("id")

        consumed_ids.add(id(pb))

    # Build final list, dropping any price blocks that were merged
    merged_blocks: List[Dict[str, Any]] = []
    for tb in page_text_blocks:
        if id(tb) in consumed_ids:
            continue
        merged_blocks.append(tb)

    return merged_blocks


# -----------------------------
# Phase 4 pt.1 — Block classification + noise collapse
# -----------------------------

def _block_garbage_ratio(tb: Dict[str, Any]) -> float:
    """
    Estimate how garbage-like a block is based on line-level heuristics.
    Uses ocr_utils.is_garbage_line(line, price_hit).
    """
    lines = tb.get("lines") or []
    if not lines:
        text = (tb.get("merged_text") or tb.get("text") or "").strip()
        if not text:
            return 1.0
        return 0.0

    total = 0
    garbage = 0
    for ln in lines:
        t = (ln.get("text") or "").strip()
        if not t:
            continue
        total += 1
        price_hit = bool(ocr_utils.find_price_candidates(t))
        if ocr_utils.is_garbage_line(t, price_hit):
            garbage += 1
    if total == 0:
        return 0.0
    return garbage / float(total)


def _classify_block_role(
    tb: Dict[str, Any],
    prev_role: Optional[str] = None,
    next_role: Optional[str] = None,
) -> str:
    """
    Classify a text block into a semantic role:
    - heading
    - item_name
    - description
    - price
    - meta
    - noise
    - item (fallback catch-all)
    """
    text = (tb.get("merged_text") or tb.get("text") or "").strip()
    if not text:
        return "noise"

    lower = text.lower()
    digits = sum(c.isdigit() for c in text)
    letters = sum(c.isalpha() for c in text)
    pricey = _is_pricey_text(text)
    garbage_ratio = _block_garbage_ratio(tb)

    # Hard drop: almost all garbage and not a price
    if garbage_ratio >= 0.85 and not pricey:
        return "noise"

    # Meta / address / hours / payment noise
    if any(h in lower for h in _META_HINTS):
        return "meta"

    # Strong price column
    if pricey and letters <= 5:
        return "price"

    # Heading candidates: short, mostly uppercase, or strong regex match
    line_count = len(tb.get("lines") or []) or (text.count("\n") + 1)
    if line_count <= 3 and len(text) <= 48:
        if _HEADING_HINT.match(text):
            return "heading"
        if letters:
            uppers = sum(1 for c in text if c.isupper())
            if uppers / float(letters) >= 0.65:
                return "heading"

    # Description: sentence-ish, multiple words, mostly lower/mixed-case, not price-y
    tokens = text.replace("\n", " ").split()
    token_count = len(tokens)
    if token_count >= 5 and digits <= 3 and letters:
        lowers = sum(1 for c in text if c.islower())
        if lowers / float(letters) >= 0.4 and not pricey:
            return "description"

    # Short-ish lines with a few digits → likely item names with sizes/counts
    if token_count <= 11 and digits <= 4:
        return "item_name"

    # Neighbor-based nudge: if we just saw a heading, bias toward item_name
    if prev_role == "heading" and token_count <= 14:
        return "item_name"

    # Fallback generic item
    return "item"


def classify_and_collapse_text_blocks(
    text_blocks: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Phase 4 pt.1 helper:

    - Assign tb["role"] to each text_block
    - Assign tb["is_heading"] / tb["is_noise"] booleans
    - Drop blocks whose role == "noise" to collapse visual garbage

    Returns a new list with noise blocks removed.
    """
    if not text_blocks:
        return text_blocks

    roles: List[Optional[str]] = [None] * len(text_blocks)

    # First pass: coarse classification without neighbor context
    for idx, tb in enumerate(text_blocks):
        roles[idx] = _classify_block_role(tb, None, None)

    # Second pass: refine with neighbors (mostly for heading-adjacent items)
    refined: List[Dict[str, Any]] = []
    for idx, tb in enumerate(text_blocks):
        prev_role = roles[idx - 1] if idx > 0 else None
        next_role = roles[idx + 1] if idx + 1 < len(text_blocks) else None
        role = _classify_block_role(tb, prev_role=prev_role, next_role=next_role)
        roles[idx] = role

        tb["role"] = role
        tb["is_heading"] = role == "heading"
        tb["is_noise"] = role == "noise"

        if role == "noise":
            # Collapse: drop from final text_blocks
            continue

        refined.append(tb)

    return refined


# -----------------------------
# Phase 4 pt.2 — Multi-line reconstruction
# -----------------------------

_BULLET_LEADER_RX = re.compile(r"^\s*[\u2022\u2023\u25E6\u2043\*\-]+[\s\u00A0]*")
_NUM_LEADER_RX = re.compile(r"^\s*\d+\s*[\.\)]\s*")


def _rebuild_multiline_text(raw: str) -> str:
    """
    Normalize multi-line text from a single TextBlock:

    - Strip blank lines
    - Remove basic bullet / numeric leaders
    - Merge lines with spaces
    - If previous chunk ends with '-', glue next line with no space
    - Collapse extra whitespace
    """
    if not raw:
        return ""

    # Split to logical lines
    lines = [ln.strip() for ln in raw.splitlines()]
    lines = [ln for ln in lines if ln]  # drop empties
    if not lines:
        return ""

    cleaned_lines: List[str] = []
    for ln in lines:
        # Strip bullets like "•", "*", "-" at the start
        ln2 = _BULLET_LEADER_RX.sub("", ln)
        # Strip simple "1." / "2)" leaders
        ln2 = _NUM_LEADER_RX.sub("", ln2)
        ln2 = ln2.strip()
        if ln2:
            cleaned_lines.append(ln2)

    if not cleaned_lines:
        return ""

    # Rebuild with hyphen-aware joining
    text = cleaned_lines[0]
    for ln in cleaned_lines[1:]:
        if text.endswith("-"):
            # Glue directly: "CHICK-\nEN" → "CHICKEN"
            text = text[:-1] + ln.lstrip()
        else:
            text = f"{text} {ln}"

    # Clean up whitespace artifacts
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def reconstruct_multiline_descriptions_on_text_blocks(
    text_blocks: List[Dict[str, Any]]
) -> None:
    """
    Phase 4 pt.2 helper:

    For each text block:
      - If role in {description, item, item_name, heading, meta}, normalize its
        merged_text to remove bullets and join lines into smoother text.
      - Price-only blocks are left alone so we don't destroy alignment hints.

    Mutates text_blocks in place (no return).
    """
    if not text_blocks:
        return

    for tb in text_blocks:
        merged = tb.get("merged_text") or tb.get("text") or ""
        if not merged:
            continue

        role = (tb.get("role") or "").lower()
        if role in {"price", "noise"}:
            # Price blocks remain as-is; noise blocks should already be filtered out.
            continue

        rebuilt = _rebuild_multiline_text(merged)
        if not rebuilt:
            # If our reconstruction somehow eats everything, keep the original.
            continue

        tb["merged_text"] = rebuilt
        meta = tb.setdefault("meta", {})
        meta["multiline_reconstructed"] = True


# -----------------------------
# Category inference via storage/category_infer
# -----------------------------

def infer_categories_on_text_blocks(text_blocks: List[Dict[str, Any]]) -> None:
    """
    Mutate each text_block dict, adding:
      - category: str|None
      - category_confidence: int
      - rule_trace: str (human-readable reason)

    This is a thin wrapper around storage/category_infer.infer_category_for_text
    so all category logic lives in one place.
    """
    for idx, tb in enumerate(text_blocks):
        merged = tb.get("merged_text") or tb.get("text") or ""
        if not merged:
            tb["category"] = None
            tb["category_confidence"] = 0
            tb["rule_trace"] = "empty_text"
            continue

        block_type = (tb.get("block_type") or "").lower()
        role = (tb.get("role") or "").lower()
        heading_like = (
            role == "heading"
            or block_type in {"heading", "header", "section", "title"}
            or bool(_HEADING_HINT.match(merged.strip()))
        )

        # Neighbor categories (if already assigned) – gives mild context.
        neighbors: List[str] = []
        if idx > 0:
            prev_cat = text_blocks[idx - 1].get("category")
            if prev_cat:
                neighbors.append(prev_cat)
        if idx + 1 < len(text_blocks):
            next_cat = text_blocks[idx + 1].get("category")
            if next_cat:
                neighbors.append(next_cat)

        guess = category_infer.infer_category_for_text(
            name=merged,
            description=None,
            price_cents=0,
            neighbor_categories=neighbors,
            fallback="Uncategorized",
        )

        # We treat fallback "Uncategorized" as "no strong category" at block level.
        final_category = guess.category if guess.category and guess.category != "Uncategorized" else None

        reason = guess.reason or "heuristic match"
        if heading_like:
            reason = reason + "; heading_like"

        tb["category"] = final_category
        tb["category_confidence"] = int(guess.confidence)
        tb["rule_trace"] = reason


# -----------------------------
# Grouping (Phase 2 legacy)
# -----------------------------

def _group_words_to_lines(words: List[Word]) -> List[Line]:
    if not words:
        return []
    heights = [w["bbox"]["h"] for w in words]
    widths = [w["bbox"]["w"] for w in words]
    median_h = max(1.0, ocr_utils.median([float(h) for h in heights]))
    line_y_tol = 0.6 * median_h

    lines: List[Line] = []
    cur_words: List[Word] = []

    def flush_line():
        nonlocal lines, cur_words
        if not cur_words:
            return
        xs = [w["bbox"]["x"] for w in cur_words]
        ys = [w["bbox"]["y"] for w in cur_words]
        xe = [w["bbox"]["x"] + w["bbox"]["w"] for w in cur_words]
        ye = [w["bbox"]["y"] + w["bbox"]["h"] for w in cur_words]
        bbox: BBox = {"x": min(xs), "y": min(ys), "w": max(xe) - min(xs), "h": max(ye) - min(ys)}
        line_text = " ".join(w["text"] for w in cur_words)
        line_text = _ALLOWED_RE.sub(" ", line_text)
        line_text = _REPEAT3.sub(r"\1\1", line_text)
        line_text = re.sub(r"\s{2,}", " ", line_text).strip()
        letters = sum(1 for c in line_text if c.isalpha())
        digits = sum(1 for c in line_text if c.isdigit())
        if len(line_text) < 3 or (letters < 2 and digits == 0):
            cur_words.clear()
            return
        lines.append({"text": line_text, "bbox": bbox, "words": cur_words[:]})
        cur_words = []

    last_y: Optional[float] = None
    for w in words:
        wy = w["bbox"]["y"]
        if last_y is None:
            cur_words = [w]
            last_y = wy
            continue
        if abs(wy - last_y) <= line_y_tol:
            cur_words.append(w)
            last_y = (last_y + wy) / 2.0
        else:
            cur_words.sort(key=lambda ww: ww["bbox"]["x"])
            flush_line()
            cur_words = [w]
            last_y = wy
    cur_words.sort(key=lambda ww: ww["bbox"]["x"])
    flush_line()
    lines.sort(key=lambda ln: (ln["bbox"]["y"], ln["bbox"]["x"]))
    return lines


def _group_lines_to_blocks(lines: List[Line]) -> List[Block]:
    if not lines:
        return []
    line_heights = [ln["bbox"]["h"] for ln in lines]
    median_line_h = max(1.0, ocr_utils.median([float(h) for h in line_heights]))
    line_gap_thr = 1.25 * median_line_h

    blocks: List[Block] = []
    cur: List[Line] = []

    def flush_block():
        nonlocal blocks, cur
        if not cur:
            return
        xs = [l["bbox"]["x"] for l in cur]
        ys = [l["bbox"]["y"] for l in cur]
        xe = [l["bbox"]["x"] + l["bbox"]["w"] for l in cur]
        ye = [l["bbox"]["y"] + l["bbox"]["h"] for l in cur]
        bbox: BBox = {"x": min(xs), "y": min(ys), "w": max(xe) - min(xs), "h": max(ye) - min(ys)}
        blocks.append({"id": str(uuid.uuid4()), "page": 1, "bbox": bbox, "lines": cur[:]})
        cur = []

    def overlap_ratio(a: BBox, b: BBox) -> float:
        ax1, ax2 = a["x"], a["x"] + a["w"]
        bx1, bx2 = b["x"], b["x"] + b["w"]
        inter = max(0, min(ax2, bx2) - max(ax1, bx1))
        denom = max(1, min(a["w"], b["w"]))
        return inter / float(denom)

    prev = None
    for ln in lines:
        if prev is None:
            cur = [ln]
            prev = ln
            continue
        dy = ln["bbox"]["y"] - prev["bbox"]["y"]
        horiz = overlap_ratio(prev["bbox"], ln["bbox"])
        if dy <= line_gap_thr or horiz >= 0.25:
            cur.append(ln)
        else:
            flush_block()
            cur = [ln]
        prev = ln
    flush_block()
    blocks.sort(key=lambda b: (b["bbox"]["x"], b["bbox"]["y"]))
    return blocks


# -----------------------------
# Main pipeline
# -----------------------------

def segment_document(
    pdf_path: Optional[str] = None,
    pdf_bytes: Optional[bytes] = None,
    dpi: int = DEFAULT_DPI,
) -> Dict[str, Any]:
    """Render a PDF or image file, run high-clarity OCR, and return blocks + Phase-3/4 text blocks."""
    if not pdf_path and not pdf_bytes:
        raise ValueError("Either pdf_path or pdf_bytes must be provided.")

    if pdf_path:
        pages = ocr_utils.pdf_to_images_from_path(pdf_path, dpi=dpi)
        source = pdf_path
    else:
        pages = ocr_utils.pdf_to_images_from_bytes(pdf_bytes, dpi=dpi)
        source = "bytes"

    all_blocks: List[Block] = []                   # Phase-2 block groups (legacy)
    all_text_blocks: List[Dict[str, Any]] = []     # Phase-3+ text blocks ({bbox{x,y,w,h}, lines, merged_text, block_type, role, ...})
    all_preview_blocks: List[OCRBlock] = []        # Phase-3/4 preview blocks (OCRBlock TypedDict)

    page_index = 1

    for im in pages:
        # Auto-rotate page if sideways
        try:
            osd = image_to_osd(im)
            if "Rotate: 90" in osd:
                im = im.rotate(-90, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated -90°")
            elif "Rotate: 270" in osd:
                im = im.rotate(90, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated 90°")
            elif "Rotate: 180" in osd:
                im = im.rotate(180, expand=True)
                print(f"[Auto-rotate] Page {page_index}: rotated 180°")
        except Exception:
            pass

        # 🔹 High-clarity preprocessing and adaptive column split
        if ENABLE_VISION_PREPROCESS:
            im_pre = vision_preprocess(im, page_index=page_index, column_index=None)
        else:
            im_pre = ocr_utils.preprocess_page(im, do_deskew=True)

        # Dynamic min_gap based on image width; helps real menus where

        # gutters are relatively narrow but consistent.
        width, height = im_pre.size
        # Roughly ~0.4–1% of page width, clamped to a reasonable range.
        min_gap_px = max(12, min(64, int(width * 0.0075)))

        columns = ocr_utils.split_columns(im_pre, min_gap_px=min_gap_px)

        # Option A: if page is very wide and we only found one column, force 2 columns.
        if width >= 2400 and len(columns) == 1:
            mid_x = width // 2
            left_img = im_pre.crop((0, 0, mid_x, height))
            right_img = im_pre.crop((mid_x, 0, width, height))
            columns = [left_img, right_img]
            print(
                f"[Columns] Page {page_index}: width={width}px, "
                f"min_gap_px={min_gap_px}, columns={len(columns)} (fallback forced 2-column split)"
            )
        else:
            print(f"[Columns] Page {page_index}: width={width}px, min_gap_px={min_gap_px}, columns={len(columns)}")

        # Collect text blocks for this page across all columns
        page_text_blocks: List[Dict[str, Any]] = []

        for col_idx, col_img in enumerate(columns, start=1):
            data = run_multipass_ocr(col_img, page_index=page_index, column_index=col_idx)
            words: List[Word] = []

            n = len(data.get("text", []))
            for i in range(n):
                w = _make_word(i, data)
                if w:
                    words.append(w)
            words.sort(key=lambda ww: (ww["bbox"]["y"], ww["bbox"]["x"]))

            # Phase-2 legacy lines/blocks
            lines = _group_words_to_lines(words)
            blocks = _group_lines_to_blocks(lines)
            for b in blocks:
                b["page"] = page_index
                b.setdefault("meta", {})["column"] = col_idx
            all_blocks.extend(blocks)

            # Phase-3: text-block segmentation
            tblocks = ocr_utils.group_text_blocks(lines)

            # Annotate page/column so we can merge across columns later
            for tb in tblocks:
                tb["page"] = page_index
                tb["column"] = col_idx

            page_text_blocks.extend(tblocks)

        # ----- Phase 3 pt.4: two-column merge on a per-page basis
        page_text_blocks = merge_two_column_rows(page_text_blocks)

        # ----- Phase 4 pt.1: classify blocks + collapse obvious noise
        page_text_blocks = classify_and_collapse_text_blocks(page_text_blocks)

        # ----- Phase 4 pt.2: reconstruct multi-line descriptions within each block
        reconstruct_multiline_descriptions_on_text_blocks(page_text_blocks)

        # ----- Category inference (mutates tblocks in place via shared helper)
        infer_categories_on_text_blocks(page_text_blocks)

        # ----- Phase 3 pt.6: price + base variant extraction on merged text blocks
        annotate_prices_and_variants_on_text_blocks(page_text_blocks)

        # ----- Phase 4 pt.3: enrich variants with size/flavor intelligence
        variant_engine.enrich_variants_on_text_blocks(page_text_blocks)

        # Compact preview records (xyxy coords), annotate page/column for overlay UI
        pblocks = ocr_utils.blocks_for_preview(page_text_blocks)
        for pb in pblocks:
            pb["page"] = page_index

            # Column may or may not already be present; look it up from text_blocks by id
            if pb.get("column") is None:
                col_from_tb = next(
                    (tb.get("column") for tb in page_text_blocks if tb.get("id") == pb.get("id")),
                    None,
                )
                if col_from_tb is not None:
                    pb["column"] = col_from_tb

            tb_for_pb = next(
                (tb for tb in page_text_blocks if tb.get("id") == pb.get("id")),
                None,
            )

            # Mirror category / hierarchy / inference info for overlay
            if tb_for_pb is not None:
                if pb.get("category") is None and "category" in tb_for_pb:
                    pb["category"] = tb_for_pb.get("category")
                if pb.get("category_confidence") is None and "category_confidence" in tb_for_pb:
                    pb["category_confidence"] = tb_for_pb.get("category_confidence")
                if "rule_trace" in tb_for_pb and pb.get("rule_trace") is None:
                    pb["rule_trace"] = tb_for_pb.get("rule_trace")

                # Hierarchy: subcategory + section_path
                if "subcategory" in tb_for_pb and pb.get("subcategory") is None:
                    pb["subcategory"] = tb_for_pb.get("subcategory")
                if "section_path" in tb_for_pb and pb.get("section_path") is None:
                    pb["section_path"] = tb_for_pb.get("section_path")

                # Mirror price/variant info + roles for overlay + preview JSON
                if "price_candidates" in tb_for_pb:
                    pb["price_candidates"] = tb_for_pb["price_candidates"]
                if "variants" in tb_for_pb:
                    pb["variants"] = tb_for_pb["variants"]
                if "role" in tb_for_pb:
                    pb["role"] = tb_for_pb["role"]
                if "is_heading" in tb_for_pb:
                    pb["is_heading"] = tb_for_pb["is_heading"]
                if "is_noise" in tb_for_pb:
                    pb["is_noise"] = tb_for_pb["is_noise"]
                if tb_for_pb.get("meta") and tb_for_pb["meta"].get("multiline_reconstructed"):
                    pb.setdefault("meta", {})["multiline_reconstructed"] = True

        all_text_blocks.extend(page_text_blocks)
        all_preview_blocks.extend(pblocks)

        page_index += 1

    segmented: Dict[str, Any] = {
        "pages": len(pages),
        "dpi": dpi,
        "blocks": all_blocks,                  # Phase-2 compatible
        "text_blocks": all_text_blocks,        # Phase-3+ TextBlock dicts (+category fields, +prices/variants, +roles)
        "preview_blocks": all_preview_blocks,  # Phase-3/4 compact overlay records (+category, +hierarchy, +prices/variants, +roles)
        "meta": {
            "source": source,
            "engine": "tesseract",
            "version": str(pytesseract.get_tesseract_version()),
            "config": OCR_CONFIG,
            "conf_floor": LOW_CONF_DROP,
            "mode": (
                "high_clarity+segmentation+two_column_merge+"
                "category_infer+multi_price_variants+block_roles+"
                "multiline_reconstruct+variant_enrich+category_hierarchy+"
                "price_integrity_prep+structured_v2_prep+"
                "vision_scaffold+multipass_scaffold"
            ),
            "preprocess": "clahe+adaptive+denoise+unsharp+deskew",
            "vision_layer": {
                "enabled": ENABLE_VISION_PREPROCESS,
                "debug_dir": VISION_DEBUG_DIR or None,
            },
            "multipass": {
                "enabled": ENABLE_MULTIPASS_OCR,
                "psms": MULTIPASS_PSMS,
                "rotations": MULTIPASS_ROTATIONS,
            },
        },
    }
    return segmented



if __name__ == "__main__":
    sample = segment_document(pdf_path="fixtures/sample_menus/pizza_real.pdf")
    print(
        list(sample.keys()),
        "Blocks:", len(sample["blocks"]),
        "TextBlocks:", len(sample.get("text_blocks", [])),
        "PreviewBlocks:", len(sample.get("preview_blocks", []))
    )
    # Quick glance at inferred categories / roles / variants
    cats = [
        (
            tb.get("category"),
            tb.get("category_confidence"),
            tb.get("role"),
            (tb.get("merged_text") or "")[:40],
            tb.get("variants"),
        )
        for tb in sample.get("text_blocks", [])
    ]
    print("Sample categories/roles/variants:", [c for c in cats if c[0] or c[2] or c[4]])
