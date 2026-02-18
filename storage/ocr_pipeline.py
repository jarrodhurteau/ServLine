# storage/ocr_pipeline.py
"""
ServLine OCR Pipeline — Phase 3 (Segmentation + Category Inference) + Phase 4 pt.1–6

Phase 2 kept:
- Re-raster PDF pages at 400 DPI for sharper glyphs.
- Apply preprocess_page() → CLAHE + denoise + unsharp + deskew (OCR work image; NOT binary threshold).
- Split two-column layouts with split_columns() (thresholding is used internally here for gutter detection).
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

from . import ocr_utils
from . import category_infer
from . import variant_engine
from . import cross_item
from . import semantic_confidence
from .parsers.menu_grammar import enrich_grammar_on_text_blocks
from .parsers.combo_vocab import is_combo_food
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

from .menu_corrections import correct_ocr_text

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

ENABLE_MULTIPASS_OCR = os.getenv("OCR_ENABLE_MULTIPASS_OCR", "1") == "1"

# Debug logging (env-controlled; default OFF)
DEBUG_MULTIPASS_LOGS = os.getenv("OCR_DEBUG_MULTIPASS_LOGS", "0") == "1"

# Phase 7 pt.7 — Multi-PSM fusion
# Phase 7 pt.8 — Rotation sweep for mis-oriented uploads (OCR-only fallback)
MULTIPASS_PSMS: List[int] = [6, 4, 11]
MULTIPASS_ROTATIONS: List[int] = [0, 90, 180, 270]



def _effective_ocr_config_string() -> str:
    """
    Return an honest config description for debug/meta payloads.

    When multipass is enabled, segment_document() runs multiple PSMs
    and fuses the resulting tokens. This function makes meta["config"]
    reflect that reality for demo/debug credibility.
    """
    if ENABLE_MULTIPASS_OCR:
        psms = ",".join(str(p) for p in MULTIPASS_PSMS)
        rots = ",".join(str(r) for r in MULTIPASS_ROTATIONS)
        return f"{BASE_OCR_CONFIG} --psm [{psms}] rotations=[{rots}] (fused)"
    return OCR_CONFIG



# -----------------------------
# Maintenance Day 44 — OCR input proof artifacts (diagnostics only)
# -----------------------------
DEBUG_SAVE_OCR_INPUT_ARTIFACTS = os.getenv("OCR_DEBUG_SAVE_INPUT_ARTIFACTS", "0") == "1"
DEBUG_INPUT_DIR = os.getenv("OCR_DEBUG_INPUT_DIR") or ""



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
    t = correct_ocr_text(t)
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
    if len(tok) <= 2 and not any(ch.isalnum() for ch in tok):
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
        return


def _debug_save_ocr_input(image: Image.Image, page_index: int, column_index: Optional[int], stage: str) -> None:
    """
    Maintenance Day 44:
    Persist the exact image artifact that is passed into Tesseract OCR.

    Controlled by:
      OCR_DEBUG_SAVE_INPUT_ARTIFACTS=1
      OCR_DEBUG_INPUT_DIR=/path

    Output files:
      page{page_index:03d}_c{column_index}_{stage}.png
    """
    if not DEBUG_SAVE_OCR_INPUT_ARTIFACTS:
        return
    if not DEBUG_INPUT_DIR:
        return

    try:
        out_root = Path(DEBUG_INPUT_DIR)
        out_root.mkdir(parents=True, exist_ok=True)
        col_suffix = f"_c{column_index}" if column_index is not None else ""
        filename = f"page{page_index:03d}{col_suffix}_{stage}.png"
        out_path = out_root / filename
        image.save(out_path)
        print(f"[OCR-Input] saved {stage} -> {out_path}")
    except Exception as _e:
        print(f"[OCR-Input] (warn) could not save {stage}: {_e}")


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

    All placeholder functions are identity transforms.

    IMPORTANT:
    The OCR work image is whatever ocr_utils.preprocess_page() returns.
    This must remain a human-readable work image (NOT an adaptive-threshold/binary artifact).

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


# Day 58: detect raw OCR tokens like "WIFRIES", "WICHEESE" that were not
# normalized because the grammar parser works on a separate text copy.
from .parsers.combo_vocab import COMBO_FOODS as _COMBO_FOODS_SET

_WI_COMBO_ALTS = "|".join(
    re.escape(f) for f in sorted(
        (f for f in _COMBO_FOODS_SET if " " not in f),
        key=len, reverse=True,
    )
)
_WI_COMBO_RE = re.compile(
    r"^wi/?(" + _WI_COMBO_ALTS + r")$",
    re.IGNORECASE,
)


def _extract_wi_combo(token_lower: str) -> Optional[str]:
    """If *token_lower* looks like 'wifries' or 'wi/fries', return the food part."""
    m = _WI_COMBO_RE.match(token_lower)
    return m.group(1).lower() if m else None


def _build_variants_from_text(
    text: str,
    priced: List[Tuple[OCRPriceCandidate, int]],
    grammar: Optional[Dict[str, Any]] = None,
) -> List[OCRVariant]:
    """
    Infer variant labels (e.g., 'Sm', 'Lg', '16"') from tokens immediately
    preceding each price. Returns OCRVariant list; intended mostly for 2+ prices.

    Day 58: Detects combo modifier patterns (w/ FRIES) and preserves
    the "with FOOD" label structure, applying kind_hint="combo".
    """
    variants: List[OCRVariant] = []
    if not text or not priced:
        return variants

    # Combo hints from grammar parse (Day 58)
    combo_hints: List[str] = []
    if grammar:
        combo_hints = grammar.get("combo_hints", [])

    # Tokenize while preserving char positions
    tokens: List[Tuple[str, int, int]] = []
    for tm in re.finditer(r"\S+", text):
        tok = tm.group(0)
        tokens.append((tok, tm.start(), tm.end()))

    for cand, price_start in priced:
        # Collect tokens ending before the price
        prior_tokens = [t for t in tokens if t[2] <= price_start]
        label_parts: List[str] = []
        is_combo = False

        for tok, ts, te in reversed(prior_tokens):
            stripped = tok.strip(".,;:-")
            if not stripped:
                continue
            low = stripped.lower()

            # Skip if this token itself looks like a price
            if _PRICE_RE.fullmatch(stripped):
                continue

            # Day 58: detect WI+FOOD tokens (e.g., "WIFRIES") in raw OCR text
            wi_food = _extract_wi_combo(low)
            if wi_food:
                label_parts.append(f"W/{wi_food.title()}")
                is_combo = True
                break

            # Day 58: combo food detection — preserve "with + food" pairs
            if is_combo_food(low):
                if label_parts and label_parts[-1].lower() in ("with", "w/"):
                    # Combine: collected "with" + this food -> "W/Food"
                    label_parts.pop()
                    label_parts.append(f"W/{stripped.title()}")
                    is_combo = True
                    break
                elif combo_hints and low in combo_hints:
                    # Grammar confirms combo context -> prefix with "W/"
                    label_parts.append(f"W/{stripped.title()}")
                    is_combo = True
                    break
                else:
                    # Standalone food, no combo context — use raw name
                    label_parts.append(stripped)
                    is_combo = True
                    break

            # Connector tokens: keep "with"/"w/" tentatively for combo building
            if low in _CONNECTOR_TOKENS:
                if low in ("with", "w/"):
                    label_parts.append(low)
                    if len(label_parts) >= 2:
                        break
                continue

            label_parts.append(stripped)
            if len(label_parts) >= 2:
                break

        # Build final label
        label = " ".join(reversed(label_parts)).strip()

        # Clean up: bare "with" or "w/" with no food following is incomplete
        if label.lower() in ("with", "w/"):
            label = ""

        price_cents = cand.get("price_cents")
        if price_cents is None:
            price_cents = _parse_price_to_cents(cand["text"])
        if price_cents is None:
            # If we truly can't parse, skip this as a variant; the raw candidate still exists.
            continue

        variant: OCRVariant = {
            "label": label,
            "price_cents": price_cents,
            "confidence": float(cand["confidence"]),
        }
        if is_combo:
            variant["kind_hint"] = "combo"
        variants.append(variant)

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
        variants = _build_variants_from_text(merged, priced, tb.get("grammar"))
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


def _run_single_ocr_pass(image: Image.Image, psm: int, rotation: int) -> Dict[str, Any]:
    """
    Single OCR pass for a specific (rotation, psm) combination.

    rotation is interpreted as clockwise degrees.
    We rotate the image for OCR only, then later un-rotate bboxes back
    into the original image coordinate space.

    Returns:
      {
        "psm": int,
        "rotation": int,
        "orig_size": (w, h),
        "data": Dict[str, List]
      }
    """
    orig_w, orig_h = image.size

    working = image
    if rotation != 0:
        # PIL rotates counter-clockwise for positive angles.
        # We interpret rotation as clockwise degrees, so rotate by -rotation.
        working = image.rotate(-rotation, expand=True)

    config = f"{BASE_OCR_CONFIG} --psm {psm}"
    data = pytesseract.image_to_data(
        working,
        output_type=pytesseract.Output.DICT,
        config=config,
    )

    tokens = len(data.get("text", []))
    if DEBUG_MULTIPASS_LOGS:
        print(f"[Multipass] rotation={rotation} psm={psm} tokens={tokens}")

    return {
        "psm": int(psm),
        "rotation": int(rotation),
        "orig_size": (int(orig_w), int(orig_h)),
        "data": data,
    }



def fuse_multipass_results(passes: List[Dict[str, Any]]) -> Dict[str, List]:
    """
    Phase 7 pt.7 — Multi-PSM fusion.

    Input `passes` elements are:
      {"psm": int, "rotation": int, "data": Dict[str, List]}

    Output is a Dict[str, List] compatible with _make_word():
      keys: "text", "conf", "left", "top", "width", "height"
    """
    if not passes:
        raise ValueError("No OCR passes provided to fuse_multipass_results()")

    def _safe_int(v: Any) -> int:
        try:
            return int(v)
        except Exception:
            return 0

    def _safe_float(v: Any) -> float:
        try:
            return float(v)
        except Exception:
            return -1.0

    def _bbox_iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2 = ax + aw
        ay2 = ay + ah
        bx2 = bx + bw
        by2 = by + bh

        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)

        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0

        area_a = max(1, aw) * max(1, ah)
        area_b = max(1, bw) * max(1, bh)
        denom = area_a + area_b - inter
        if denom <= 0:
            return 0.0
        return inter / float(denom)

    def _bbox_overlap_ratio(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
        """
        Overlap ratio relative to the smaller box area.
        Useful when IoU is low due to slightly different box sizing.
        """
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        ax2 = ax + aw
        ay2 = ay + ah
        bx2 = bx + bw
        by2 = by + bh

        ix1 = max(ax, bx)
        iy1 = max(ay, by)
        ix2 = min(ax2, bx2)
        iy2 = min(ay2, by2)

        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0

        area_a = max(1, aw) * max(1, ah)
        area_b = max(1, bw) * max(1, bh)
        denom = float(max(1, min(area_a, area_b)))
        return inter / denom
    
    def _unrotate_bbox_to_original(
        bbox: Tuple[int, int, int, int],
        rotation_clockwise: int,
        orig_w: int,
        orig_h: int,
    ) -> Tuple[int, int, int, int]:
        """
        Convert a bbox from the rotated OCR image coordinate space back into the
        original (unrotated) image coordinate space.

        We rotate the image for OCR using:
          working = image.rotate(-rotation_clockwise, expand=True)

        So rotation_clockwise ∈ {0,90,180,270}.

        We map by transforming the bbox corners using the inverse transform
        from rotated->original and then taking min/max.

        Returns: (x, y, w, h) in original image coords.
        """
        x, y, w, h = bbox
        if rotation_clockwise % 360 == 0:
            return (int(x), int(y), int(w), int(h))

        def _inv_point(px: int, py: int) -> Tuple[int, int]:
            r = int(rotation_clockwise) % 360

            # Inverse mapping from rotated (working) coords -> original coords.
            # rotation is clockwise, and working was created by rotating original clockwise.
            if r == 90:
                # CW 90: original->rotated: (x,y)->(H-1-y, x)
                # inverse: rotated->original: (x',y')->(y', H-1-x')
                return (int(py), int(orig_h - 1 - px))

            if r == 180:
                # CW 180: original->rotated: (x,y)->(W-1-x, H-1-y)
                # inverse is same
                return (int(orig_w - 1 - px), int(orig_h - 1 - py))

            if r == 270:
                # CW 270 == CCW 90: original->rotated: (x,y)->(y, W-1-x)
                # inverse: (x',y')->(W-1-y', x')
                return (int(orig_w - 1 - py), int(px))

            return (int(px), int(py))

        # corners in rotated space
        x1, y1 = int(x), int(y)
        x2, y2 = int(x + w), int(y)
        x3, y3 = int(x), int(y + h)
        x4, y4 = int(x + w), int(y + h)

        pts = [
            _inv_point(x1, y1),
            _inv_point(x2, y2),
            _inv_point(x3, y3),
            _inv_point(x4, y4),
        ]

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]

        min_x = max(0, min(xs))
        min_y = max(0, min(ys))
        max_x = min(orig_w, max(xs))
        max_y = min(orig_h, max(ys))

        new_w = max(0, int(max_x - min_x))
        new_h = max(0, int(max_y - min_y))

        return (int(min_x), int(min_y), int(new_w), int(new_h))


    def _extract_candidates(pass_obj: Dict[str, Any]) -> List[Dict[str, Any]]:
        psm = int(pass_obj.get("psm", 0))
        rotation = int(pass_obj.get("rotation", 0))

        orig_size = pass_obj.get("orig_size") or (0, 0)
        try:
            orig_w = int(orig_size[0])
            orig_h = int(orig_size[1])
        except Exception:
            orig_w, orig_h = 0, 0

        data = pass_obj.get("data") or {}
        texts = data.get("text", []) or []
        confs = data.get("conf", []) or []
        lefts = data.get("left", []) or []
        tops = data.get("top", []) or []
        widths = data.get("width", []) or []
        heights = data.get("height", []) or []

        out: List[Dict[str, Any]] = []
        n = len(texts)
        for i in range(n):
            raw = (texts[i] or "").strip()
            conf = _safe_float(confs[i]) if i < len(confs) else -1.0
            if conf < LOW_CONF_DROP:
                continue

            cleaned = _clean_token(raw)
            if not cleaned or _token_is_garbage(cleaned):
                continue

            x = _safe_int(lefts[i]) if i < len(lefts) else 0
            y = _safe_int(tops[i]) if i < len(tops) else 0
            w = _safe_int(widths[i]) if i < len(widths) else 0
            h = _safe_int(heights[i]) if i < len(heights) else 0

            if w <= 1 or h <= 1:
                continue
            if w < 0:
                w = 0
            if h < 0:
                h = 0

            bbox = (int(x), int(y), int(w), int(h))

            # Un-rotate bbox back into original image coordinate space so all
            # downstream geometry is consistent (grouping, preview overlays, etc.)
            if rotation != 0 and orig_w > 0 and orig_h > 0:
                bbox = _unrotate_bbox_to_original(
                    bbox=bbox,
                    rotation_clockwise=rotation,
                    orig_w=orig_w,
                    orig_h=orig_h,
                )

            out.append(
                {
                    "text": cleaned,
                    "conf": float(conf),
                    "bbox": bbox,
                    "psm": psm,
                    "rotation": rotation,
                }
            )
        return out


    # Collect candidates per pass
    per_pass_candidates: List[List[Dict[str, Any]]] = []
    for p in passes:
        per_pass_candidates.append(_extract_candidates(p))

    # Cluster by (text + overlapping bbox)
    # Each cluster:
    #   {"text": str, "bbox": (x,y,w,h), "votes": set[int], "best": candidate}
    clusters: List[Dict[str, Any]] = []

    # Thresholds tuned to be tolerant across PSM bbox jitter
    IOU_THR = 0.35
    OVERLAP_THR = 0.60

    for pass_idx, cand_list in enumerate(per_pass_candidates):
        for c in cand_list:
            placed = False
            for cl in clusters:
                if cl["text"] != c["text"]:
                    continue
                a = cl["bbox"]
                b = c["bbox"]
                if _bbox_iou(a, b) >= IOU_THR or _bbox_overlap_ratio(a, b) >= OVERLAP_THR:
                    cl["votes"].add(pass_idx)
                    # Keep the best candidate by confidence; break ties by larger area (often more stable boxes)
                    best = cl["best"]
                    if c["conf"] > best["conf"]:
                        cl["best"] = c
                        cl["bbox"] = c["bbox"]
                    elif c["conf"] == best["conf"]:
                        ax, ay, aw, ah = best["bbox"]
                        bx, by, bw, bh = c["bbox"]
                        if (bw * bh) > (aw * ah):
                            cl["best"] = c
                            cl["bbox"] = c["bbox"]
                    placed = True
                    break
            if not placed:
                clusters.append(
                    {
                        "text": c["text"],
                        "bbox": c["bbox"],
                        "votes": set([pass_idx]),
                        "best": c,
                    }
                )

    # Decide which clusters to keep:
    # - keep if appears in >=2 passes (high confidence in the token)
    # - OR keep if single-pass with decent confidence (don't drop valid words)
    # 
    # Lowered threshold: 92.0 was too aggressive and dropped most valid text.
    # 70.0 keeps reasonable single-pass tokens while still filtering junk.
    SINGLE_PASS_CONF_KEEP = 70.0

    kept: List[Dict[str, Any]] = []
    for cl in clusters:
        vote_count = len(cl["votes"])
        best = cl["best"]
        if vote_count >= 2:
            # Multi-pass agreement — high confidence, keep it
            kept.append(best)
        else:
            # Single-pass token — keep if confidence is decent
            if best["conf"] >= SINGLE_PASS_CONF_KEEP:
                kept.append(best)

    # Sort by reading order
    kept.sort(key=lambda d: (d["bbox"][1], d["bbox"][0]))

    # Build a minimal "image_to_data-like" dict used by _make_word()
    fused_text: List[str] = []
    fused_conf: List[str] = []
    fused_left: List[int] = []
    fused_top: List[int] = []
    fused_width: List[int] = []
    fused_height: List[int] = []

    for k in kept:
        x, y, w, h = k["bbox"]
        fused_text.append(str(k["text"]))
        fused_conf.append(str(float(k["conf"])))
        fused_left.append(int(x))
        fused_top.append(int(y))
        fused_width.append(int(w))
        fused_height.append(int(h))

    fused: Dict[str, List] = {
        "text": fused_text,
        "conf": fused_conf,
        "left": fused_left,
        "top": fused_top,
        "width": fused_width,
        "height": fused_height,
    }

    print(
        f"[Multipass-Fuse] passes={len(passes)} "
        f"candidates={sum(len(x) for x in per_pass_candidates)} "
        f"clusters={len(clusters)} kept={len(kept)} "
        f"psms={sorted({int(p.get('psm', 0)) for p in passes})} "
        f"rotations={sorted({int(p.get('rotation', 0)) for p in passes})}"
    )

    return fused


def run_rotation_multipass_candidates(
    image: Image.Image,
    page_index: int,
    column_index: int,
    rotations: Optional[List[int]] = None,
) -> Dict[int, Dict[str, Any]]:
    """
    Day 47 — Phase 7 pt.9

    Produce multi-pass OCR candidates for each full-page rotation.
    This function does NOT score, choose a winner, or persist anything.

    rotations:
      - If provided, only these rotations are evaluated.
      - If None, defaults to MULTIPASS_ROTATIONS.

    Returns:
      {
        rotation_deg: {
          "rotation": int,
          "passes": List[Dict[str, Any]],
          "fused": Dict[str, List]
        },
        ...
      }
    """
    if not ENABLE_MULTIPASS_OCR:
        return {
            0: {
                "rotation": 0,
                "passes": [],
                "fused": _ocr_page(image),
            }
        }

    rotations_to_try = rotations if rotations is not None else MULTIPASS_ROTATIONS

    candidates: Dict[int, Dict[str, Any]] = {}

    for rotation in rotations_to_try:
        rot_passes: List[Dict[str, Any]] = []
        for psm in MULTIPASS_PSMS:
            pass_obj = _run_single_ocr_pass(image, psm=psm, rotation=rotation)
            rot_passes.append(pass_obj)

        fused_rot = fuse_multipass_results(rot_passes)
        tokens = len(fused_rot.get("text", []) or [])

        print(
            f"[Pt9-Candidates] page={page_index} col={column_index} "
            f"rotation={int(rotation)} fused_tokens={tokens} psms={MULTIPASS_PSMS}"
        )

        candidates[int(rotation)] = {
            "rotation": int(rotation),
            "passes": rot_passes,
            "fused": fused_rot,
        }

    return candidates


def score_rotation_fused_data(data: Dict[str, List]) -> Dict[str, Any]:
    """
    Day 47 — Phase 7 pt.10, updated pt10-v2

    Deterministic scoring inputs for a rotation's fused output.
    Returns a dict so we can persist richer metadata than a single float.

    v2 changes: fragmentation-aware scoring — penalizes rotations where
    OCR fragments text into many short tokens (wrong-rotation signal).
    Wrong rotations produce many single-char fragments with high confidence,
    inflating both usable_tokens and total_conf. The v2 formula uses:
      - avg_conf: per-token quality (not total)
      - coherence: min(avg_chars_per_token / 4.0, 1.5) — penalizes fragments
      - sqrt(usable): diminishing returns on token count
    """
    texts = data.get("text", []) or []
    confs = data.get("conf", []) or []

    usable = 0
    total_conf = 0.0
    total_chars = 0

    n = len(texts)
    for i in range(n):
        t = (texts[i] or "").strip()
        if not t:
            continue
        try:
            c = float(confs[i]) if i < len(confs) else -1.0
        except Exception:
            c = -1.0
        if c < 0:
            continue

        usable += 1
        total_conf += c
        total_chars += len(t)

    avg_conf = (total_conf / float(usable)) if usable > 0 else 0.0
    avg_chars_per_token = (total_chars / float(usable)) if usable > 0 else 0.0

    # v2: fragmentation-aware score
    # coherence: rewards multi-char words, penalizes 1-2 char fragments
    #   1.0 at avg 4 chars, caps at 1.5 for avg >= 6 chars
    coherence = min(avg_chars_per_token / 4.0, 1.5) if usable > 0 else 0.0
    # content: sqrt dampens raw token count so fragmentation can't dominate
    content = float(usable) ** 0.5

    score = avg_conf * coherence * content

    return {
        "score": float(score),
        "usable_tokens": int(usable),
        "total_conf": float(total_conf),
        "avg_conf": float(avg_conf),
        "total_chars": int(total_chars),
        "avg_chars_per_token": float(avg_chars_per_token),
        "coherence": float(coherence),
    }


def run_multipass_ocr(
    image: Image.Image,
    page_index: int,
    column_index: int,
    meta_out: Optional[Dict[str, Any]] = None,
    rotations: Optional[List[int]] = None,
) -> Dict[str, List]:
    """
    Multi-pass OCR wrapper.

    Phase 7 pt.9:
      - Execute candidates across rotations (0/90/180/270).
      - For each rotation, run PSM passes (6,4,11) and fuse within-rotation.

    Phase 7 pt.10:
      - Deterministically score each rotation's fused output.
      - Choose the best rotation with explicit tie-break rules.
      - Populate meta_out with scoring + selection metadata (if provided).

    rotations:
      - If provided, only these rotations are evaluated for this call.
      - If None, defaults to MULTIPASS_ROTATIONS.

    When ENABLE_MULTIPASS_OCR is False, behavior remains identical to _ocr_page(image).
    """
    if not ENABLE_MULTIPASS_OCR:
        data = _ocr_page(image)
        if meta_out is not None:
            meta_out["enabled"] = False
            meta_out["selected_rotation"] = 0
            meta_out["selected_psms"] = []
            meta_out["rotation_scores"] = {0: {"score": 0.0, "usable_tokens": 0, "total_conf": 0.0, "avg_conf": 0.0}}
            meta_out["scoring_version"] = "pt10-v2"
        return data

    candidates = run_rotation_multipass_candidates(
        image,
        page_index=page_index,
        column_index=column_index,
        rotations=rotations,
    )

    rotation_scores: Dict[int, Dict[str, Any]] = {}
    rotation_token_counts: Dict[int, int] = {}

    # Phase 1: Score all rotations
    for rotation, obj in candidates.items():
        fused_rot = obj.get("fused") or {}
        score_obj = score_rotation_fused_data(fused_rot)
        rotation_scores[int(rotation)] = score_obj

        tokens = len(fused_rot.get("text", []) or [])
        rotation_token_counts[int(rotation)] = int(tokens)

        print(
            f"[Pt10-RotationScore] page={page_index} col={column_index} "
            f"rotation={int(rotation)} fused_tokens={tokens} score={float(score_obj['score']):.2f} "
            f"usable_tokens={int(score_obj['usable_tokens'])} avg_conf={float(score_obj['avg_conf']):.2f} "
            f"avg_chars={float(score_obj.get('avg_chars_per_token', 0)):.2f} "
            f"coherence={float(score_obj.get('coherence', 0)):.3f}"
        )

    # Phase 2: Cross-rotation outlier penalty (pt10-v2)
    # Wrong rotations can produce wildly more tokens (e.g. 4-5x) because
    # Tesseract reads rotated text as overlapping/duplicate word detections.
    # Detect outlier token counts and penalize their scores.
    adjusted_scores: Dict[int, float] = {}
    if len(rotation_scores) >= 3:
        token_list = sorted(
            rotation_scores[r]["usable_tokens"] for r in rotation_scores
        )
        median_tokens = token_list[len(token_list) // 2]

        for rot in rotation_scores:
            raw_score = float(rotation_scores[rot]["score"])
            usable = rotation_scores[rot]["usable_tokens"]
            if median_tokens > 0 and usable > median_tokens * 2.5:
                # Squared penalty: 4x median tokens → score / 16
                ratio = float(median_tokens) / float(usable)
                penalty = ratio * ratio  # squared
                adjusted = raw_score * penalty
                print(
                    f"[Pt10-OutlierPenalty] page={page_index} col={column_index} "
                    f"rotation={rot} usable={usable} median={median_tokens} "
                    f"ratio={ratio:.3f} raw_score={raw_score:.2f} adjusted={adjusted:.2f}"
                )
                adjusted_scores[rot] = adjusted
            else:
                adjusted_scores[rot] = raw_score
    else:
        for rot in rotation_scores:
            adjusted_scores[rot] = float(rotation_scores[rot]["score"])

    # Phase 3: Select best rotation using adjusted scores
    best_rotation: Optional[int] = None
    best_score: Optional[float] = None
    best_fused: Optional[Dict[str, List]] = None

    for rotation in sorted(adjusted_scores.keys()):
        cur_score = adjusted_scores[rotation]
        fused_rot = candidates[rotation].get("fused") or {}

        if best_rotation is None:
            best_rotation = int(rotation)
            best_score = cur_score
            best_fused = fused_rot
            continue

        # Primary: higher adjusted score wins
        if cur_score > float(best_score) + 0.01:
            best_rotation = int(rotation)
            best_score = cur_score
            best_fused = fused_rot
            continue

        # Tie band: prefer rotation 0 when extremely close
        if abs(cur_score - float(best_score)) <= 0.01:
            if int(best_rotation) != 0 and int(rotation) == 0:
                best_rotation = 0
                best_score = cur_score
                best_fused = fused_rot

    if best_fused is None or best_rotation is None:
        data = _ocr_page(image)
        if meta_out is not None:
            meta_out["enabled"] = True
            meta_out["selected_rotation"] = 0
            meta_out["selected_psms"] = MULTIPASS_PSMS[:]
            meta_out["rotation_scores"] = rotation_scores
            meta_out["rotation_token_counts"] = rotation_token_counts
            meta_out["scoring_version"] = "pt10-v2"
        return data

    tokens_best = len(best_fused.get("text", []) or [])
    print(
        f"[Pt10-Selected] page={page_index} col={column_index} "
        f"selected_rotation={int(best_rotation)} fused_tokens={tokens_best} "
        f"psms={MULTIPASS_PSMS} rotations={MULTIPASS_ROTATIONS}"
    )
    if int(best_rotation) != 0:
        print(
            f"[Pt10-Warn] page={page_index} col={column_index} "
            f"rotation_sweep_corrected={int(best_rotation)} (input may have bad orientation metadata)"
        )

    if meta_out is not None:
        meta_out["enabled"] = True
        meta_out["selected_rotation"] = int(best_rotation)
        meta_out["selected_psms"] = MULTIPASS_PSMS[:]
        meta_out["rotation_scores"] = rotation_scores
        meta_out["rotation_token_counts"] = rotation_token_counts
        meta_out["scoring_version"] = "pt10-v2"

    return best_fused



def _make_word(i: int, data: Dict[str, List], conf_floor: float = LOW_CONF_DROP) -> Optional[Word]:
    texts = data.get("text", []) or []
    confs = data.get("conf", []) or []
    lefts = data.get("left", []) or []
    tops = data.get("top", []) or []
    widths = data.get("width", []) or []
    heights = data.get("height", []) or []

    if i >= len(texts):
        return None

    raw = (texts[i] or "").strip()
    try:
        conf_raw = float(confs[i]) if i < len(confs) else -1.0
    except Exception:
        conf_raw = -1.0


    if conf_raw < conf_floor:
        return None

    cleaned = _clean_token(raw)
    if not cleaned or _token_is_garbage(cleaned):
        return None

    # More defensive around bbox dimensions
    try:
        x = int(lefts[i]) if i < len(lefts) else 0
        y = int(tops[i]) if i < len(tops) else 0
        w = int(widths[i]) if i < len(widths) else 0
        h = int(heights[i]) if i < len(heights) else 0
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
    # FIXED: Use much stricter tolerance to prevent cross-column merges
    # Old: max(60.0, page_width * 0.45) could be 1980px on wide pages!
    # New: Cap at 150px to keep merges within same column (based on real merge data showing legitimate merges are <100px)
    max_horiz_gap = min(150.0, max(60.0, page_width * 0.08))
    print(f"[OCR_DEBUG] merge_two_column_rows: page_width={page_width}px, max_horiz_gap={max_horiz_gap:.0f}px")

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

        # DEBUG: Log successful merges
        dx_gap = px_left - (best_candidate["bbox"]["x"] + best_candidate["bbox"]["w"])
        print(f"[OCR_DEBUG] Merged price block (gap={dx_gap:.0f}px): '{txt_price[:30]}' -> '{base_text[:40]}'")

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

    # DEBUG: Verify modified version is being called
    print(f"[OCR_DEBUG] _group_words_to_lines called with {len(words)} words - MODIFIED VERSION with horizontal gap checking")

    # Calculate tolerances based on typical word geometry
    heights = [w["bbox"]["h"] for w in words]
    widths = [w["bbox"]["w"] for w in words]
    median_h = max(1.0, ocr_utils.median([float(h) for h in heights]))
    median_w = max(1.0, ocr_utils.median([float(w) for w in widths]))

    # Vertical tolerance: words on the same line should be vertically close
    line_y_tol = 0.6 * median_h

    # Horizontal tolerance: words on the same line should be horizontally connected
    # Allow up to ~3x median word width as gap (accounts for spaces, some padding)
    max_horiz_gap = max(40.0, median_w * 3.0)

    lines: List[Line] = []
    cur_words: List[Word] = []
    cur_y_min: Optional[float] = None
    cur_y_max: Optional[float] = None

    def flush_line() -> None:
        nonlocal lines, cur_words, cur_y_min, cur_y_max
        if not cur_words:
            return

        xs = [w["bbox"]["x"] for w in cur_words]
        ys = [w["bbox"]["y"] for w in cur_words]
        xe = [w["bbox"]["x"] + w["bbox"]["w"] for w in cur_words]
        ye = [w["bbox"]["y"] + w["bbox"]["h"] for w in cur_words]

        bbox: BBox = {
            "x": min(xs),
            "y": min(ys),
            "w": max(xe) - min(xs),
            "h": max(ye) - min(ys),
        }

        line_text = " ".join(w["text"] for w in cur_words)
        line_text = _ALLOWED_RE.sub(" ", line_text)
        line_text = _REPEAT3.sub(r"\1\1", line_text)
        line_text = re.sub(r"\s{2,}", " ", line_text).strip()

        letters = sum(1 for c in line_text if c.isalpha())
        digits = sum(1 for c in line_text if c.isdigit())

        if len(line_text) < 3 or (letters < 2 and digits == 0):
            cur_words.clear()
            cur_y_min = None
            cur_y_max = None
            return

        lines.append(
            {
                "text": line_text,
                "bbox": bbox,
                "words": cur_words[:],
            }
        )
        cur_words.clear()
        cur_y_min = None
        cur_y_max = None

    for w in words:
        wy = w["bbox"]["y"]
        wh = w["bbox"]["h"]
        wx = w["bbox"]["x"]
        wy_bottom = wy + wh

        if cur_y_min is None:
            # Start first line
            cur_words = [w]
            cur_y_min = wy
            cur_y_max = wy_bottom
            continue

        # Check if this word fits within the vertical span of the current line
        # Don't use running average - check against actual min/max to prevent drift
        potential_y_min = min(cur_y_min, wy)
        potential_y_max = max(cur_y_max, wy_bottom)
        potential_span = potential_y_max - potential_y_min

        # CRITICAL: Check height consistency to prevent merging words from different items
        # Words on the same line should have similar heights (within 2x ratio)
        # e.g., "Olive"(h=59) + "CHEESY"(h=121) should NOT merge - 2.05x ratio!
        if cur_words:
            cur_heights = [ww["bbox"]["h"] for ww in cur_words]
            avg_height_in_line = sum(cur_heights) / len(cur_heights)
            height_ratio = max(wh / avg_height_in_line, avg_height_in_line / wh)

            if height_ratio > 2.0:
                # Word height too different - start new line
                print(f"[OCR_DEBUG] Word rejected: height_ratio={height_ratio:.2f}x (word_h={wh}, line_avg_h={avg_height_in_line:.0f})")
                cur_words.sort(key=lambda ww: ww["bbox"]["x"])
                flush_line()
                cur_words = [w]
                cur_y_min = wy
                cur_y_max = wy_bottom
                continue

        # Check if adding this word would keep the line height reasonable
        if potential_span <= (median_h * 1.8):  # Allow 1.8x median height for slight variations
            # Also check horizontal proximity to prevent merging distant columns
            if cur_words:
                # Calculate what the line width would be if we add this word
                all_x_left = [ww["bbox"]["x"] for ww in cur_words] + [wx]
                all_x_right = [ww["bbox"]["x"] + ww["bbox"]["w"] for ww in cur_words] + [wx + w["bbox"]["w"]]
                potential_line_width = max(all_x_right) - min(all_x_left)

                # Also calculate gap to nearest word (left or right)
                cur_words_sorted = sorted(cur_words, key=lambda ww: ww["bbox"]["x"])
                # Find nearest neighbor
                min_gap = float('inf')
                for cw in cur_words_sorted:
                    cw_x = cw["bbox"]["x"]
                    cw_x_end = cw_x + cw["bbox"]["w"]
                    # Gap from current word to new word
                    if wx >= cw_x_end:
                        gap = wx - cw_x_end
                    else:
                        gap = cw_x - (wx + w["bbox"]["w"])
                    if abs(gap) < abs(min_gap):
                        min_gap = gap

                # Reject if line would be too wide OR gap to nearest word is too large
                # Use max line width of ~800px as threshold (about 2-3 columns on a typical menu)
                max_reasonable_line_width = max(800.0, median_w * 20.0)

                if potential_line_width > max_reasonable_line_width or abs(min_gap) > max_horiz_gap:
                    # Too wide or too distant - start new line
                    # DEBUG: Log why word was rejected
                    if potential_line_width > max_reasonable_line_width:
                        print(f"[OCR_DEBUG] Word rejected: line_width={potential_line_width:.0f}px > max={max_reasonable_line_width:.0f}px")
                    if abs(min_gap) > max_horiz_gap:
                        print(f"[OCR_DEBUG] Word rejected: horiz_gap={min_gap:.0f}px > max={max_horiz_gap:.0f}px")
                    cur_words.sort(key=lambda ww: ww["bbox"]["x"])
                    flush_line()
                    cur_words = [w]
                    cur_y_min = wy
                    cur_y_max = wy_bottom
                else:
                    # Within both vertical and horizontal tolerance - add to line
                    cur_words.append(w)
                    cur_y_min = potential_y_min
                    cur_y_max = potential_y_max
            else:
                # Should not happen, but handle gracefully
                cur_words.append(w)
                cur_y_min = potential_y_min
                cur_y_max = potential_y_max
        else:
            # Vertical span too large - start new line
            cur_words.sort(key=lambda ww: ww["bbox"]["x"])
            flush_line()
            cur_words = [w]
            cur_y_min = wy
            cur_y_max = wy_bottom

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

    def flush_block() -> None:
        nonlocal blocks, cur
        if not cur:
            return

        xs = [l["bbox"]["x"] for l in cur]
        ys = [l["bbox"]["y"] for l in cur]
        xe = [l["bbox"]["x"] + l["bbox"]["w"] for l in cur]
        ye = [l["bbox"]["y"] + l["bbox"]["h"] for l in cur]

        bbox: BBox = {
            "x": min(xs),
            "y": min(ys),
            "w": max(xe) - min(xs),
            "h": max(ye) - min(ys),
        }

        blocks.append(
            {
                "id": str(uuid.uuid4()),
                "page": 1,
                "bbox": bbox,
                "lines": cur[:],
            }
        )
        cur.clear()

    def overlap_ratio(a: BBox, b: BBox) -> float:
        ax1 = a["x"]
        ax2 = a["x"] + a["w"]
        bx1 = b["x"]
        bx2 = b["x"] + b["w"]
        inter = max(0.0, min(ax2, bx2) - max(ax1, bx1))
        denom = max(1.0, min(a["w"], b["w"]))
        return inter / float(denom)

    prev: Optional[Line] = None

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
    page_orientations: List[Dict[str, Any]] = []

    # Day 47 Phase 7 pt.10 — per-column multipass selection metadata (audit only)
    multipass_runs_meta: List[Dict[str, Any]] = []

    for im in pages:

        # Deterministic orientation normalize (EXIF → OSD → probe)
        deg_applied = 0
        try:
            im, deg_applied = ocr_utils.normalize_orientation(im)
            print(f"[Orientation] Page {page_index}: applied_clockwise={deg_applied}")
        except Exception:
            deg_applied = 0

        page_orientations.append(
            {
                "page": int(page_index),
                "degrees_applied_clockwise": int(deg_applied),
            }
        )

        # 🔹 High-clarity preprocessing and adaptive column split
        if ENABLE_VISION_PREPROCESS:
            im_pre = vision_preprocess(im, page_index=page_index, column_index=None)
            print(f"[OCR-Input] page={page_index} preprocess=vision_preprocess (OCR work image)")
        else:
            im_pre = ocr_utils.preprocess_page(im, do_deskew=True)
            print(f"[OCR-Input] page={page_index} preprocess=ocr_utils.preprocess_page (OCR work image)")

        _debug_save_ocr_input(
            im_pre,
            page_index=page_index,
            column_index=None,
            stage="work_page"
        )

        # Dynamic min_gap based on image width; helps real menus where
        # gutters are relatively narrow but consistent.
        width, height = im_pre.size
        # Roughly ~0.4–1% of page width, clamped to a reasonable range.
        min_gap_px = max(12, min(64, int(width * 0.0075)))

        columns = ocr_utils.split_columns(im_pre, min_gap_px=min_gap_px)

        # Option A: if page is very wide and we only found one column, force 2 columns.
        # DISABLED FOR TESTING — may be slicing through text on multi-column menus
        # if width >= 2400 and len(columns) == 1:
        #     mid_x = width // 2
        #     left_img = im_pre.crop((0, 0, mid_x, height))
        #     right_img = im_pre.crop((mid_x, 0, width, height))
        #     columns = [left_img, right_img]
        #     print(
        #         f"[Columns] Page {page_index}: width={width}px, "
        #         f"min_gap_px={min_gap_px}, columns={len(columns)} (fallback forced 2-column split)"
        #     )
        # else:
        print(f"[Columns] Page {page_index}: width={width}px, min_gap_px={min_gap_px}, columns={len(columns)}")

        # Collect text blocks for this page across all columns
        page_text_blocks: List[Dict[str, Any]] = []

        for col_idx, col_img in enumerate(columns, start=1):
            _debug_save_ocr_input(
                col_img,
                page_index=page_index,
                column_index=col_idx,
                stage="work_col"
            )

            print(
                f"[OCR-Input] page={page_index} col={col_idx} "
                f"image_size={col_img.size} "
                f"vision_layer={ENABLE_VISION_PREPROCESS} "
                f"multipass={ENABLE_MULTIPASS_OCR}"
            )

            col_multipass_meta: Dict[str, Any] = {}

            # Let multipass always try all rotations — its scoring is more accurate
            # than the orientation probe for finding the best OCR orientation
            rotations_for_this_page: Optional[List[int]] = None
            # DISABLED: Don't restrict rotations based on orientation probe
            # if deg_applied != 0:
            #     rotations_for_this_page = [0]
            #     print(
            #         f"[Orientation] Page {page_index}: deg_applied={int(deg_applied)} -> multipass rotations restricted to [0]"
            #     )

            data = run_multipass_ocr(
                col_img,
                page_index=page_index,
                column_index=col_idx,
                meta_out=col_multipass_meta,
                rotations=rotations_for_this_page,
            )


            if col_multipass_meta:
                multipass_runs_meta.append(
                    {
                        "page": int(page_index),
                        "column": int(col_idx),
                        "multipass": col_multipass_meta,
                    }
                )

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

        # ----- Phase 8 pt.1: grammar parse enrichment (Sprint 8.1 Day 55)
        enrich_grammar_on_text_blocks(page_text_blocks)

        # ----- Phase 4 pt.1: classify blocks + collapse obvious noise
        page_text_blocks = classify_and_collapse_text_blocks(page_text_blocks)

        # ----- Phase 4 pt.2: reconstruct multi-line descriptions within each block
        reconstruct_multiline_descriptions_on_text_blocks(page_text_blocks)

        # ----- Category inference (mutates tblocks in place via shared helper)
        infer_categories_on_text_blocks(page_text_blocks)

        # ----- Phase 3 pt.6: price + base variant extraction on merged text blocks
        annotate_prices_and_variants_on_text_blocks(page_text_blocks)

        # ----- Phase 8 pt.2: grammar-to-variant bridge (Sprint 8.2 Day 56)
        variant_engine.apply_size_grid_context(page_text_blocks)

        # ----- Phase 4 pt.3: enrich variants with size/flavor intelligence
        variant_engine.enrich_variants_on_text_blocks(page_text_blocks)

        # ----- Sprint 8.2 Day 57: validate variant price ordering
        variant_engine.validate_variant_prices(page_text_blocks)

        # ----- Sprint 8.2 Day 59: cross-variant consistency checks
        variant_engine.check_variant_consistency(page_text_blocks)

        # ----- Sprint 8.2 Day 60: variant confidence scoring
        variant_engine.score_variant_confidence(page_text_blocks)

        # ----- Sprint 8.3 Day 61: cross-item consistency checks
        cross_item.check_cross_item_consistency(page_text_blocks)

        # ----- Sprint 8.4 Day 66: semantic confidence scoring
        semantic_confidence.score_semantic_confidence(page_text_blocks)

        # Compact preview records (xyxy coords), annotate page/column for overlay UI
        pblocks = ocr_utils.blocks_for_preview(page_text_blocks)
        for tb, pb in zip(page_text_blocks, pblocks):
            pb["page"] = page_index

            if tb.get("column") is not None:
                pb["column"] = tb.get("column")

            # Mirror category / hierarchy / inference info for overlay
            if "category" in tb:
                pb["category"] = tb.get("category")
            if "category_confidence" in tb:
                pb["category_confidence"] = tb.get("category_confidence")
            if "rule_trace" in tb:
                pb["rule_trace"] = tb.get("rule_trace")

            # Hierarchy: subcategory + section_path
            if "subcategory" in tb:
                pb["subcategory"] = tb.get("subcategory")
            if "section_path" in tb:
                pb["section_path"] = tb.get("section_path")

            # Mirror price/variant info + roles for overlay + preview JSON
            if "price_candidates" in tb:
                pb["price_candidates"] = tb["price_candidates"]
            if "variants" in tb:
                pb["variants"] = tb["variants"]
            if "role" in tb:
                pb["role"] = tb["role"]
            if "is_heading" in tb:
                pb["is_heading"] = tb["is_heading"]
            if "is_noise" in tb:
                pb["is_noise"] = tb["is_noise"]
            if "price_flags" in tb:
                pb["price_flags"] = tb["price_flags"]
            if "semantic_confidence" in tb:
                pb["semantic_confidence"] = tb["semantic_confidence"]
            if "semantic_confidence_details" in tb:
                pb["semantic_confidence_details"] = tb["semantic_confidence_details"]
            if tb.get("meta") and tb["meta"].get("multiline_reconstructed"):
                pb.setdefault("meta", {})["multiline_reconstructed"] = True

            # Mirror grammar parse metadata for overlay / preview JSON
            if "grammar" in tb:
                pb["grammar"] = tb["grammar"]

        all_text_blocks.extend(page_text_blocks)
        all_preview_blocks.extend(pblocks)

        page_index += 1

    segmented: Dict[str, Any] = {
        "pages": len(pages),
        "dpi": dpi,
        "blocks": all_blocks,                  # Phase-2 compatible
        "text_blocks": all_text_blocks,        # Phase-3+ TextBlock dicts (+category fields, +prices/variants, +roles)
        "preview_blocks": all_preview_blocks,  # Phase-3/4 compact overlay records (+category, +hierarchy, +prices/variants, +roles)

        # ------------------------------------------------------------
        # NEW (Day 43 Phase 7 pt.3): Layout Debug payload
        #
        # This is what portal/app.py should store under dbg["layout_debug"]
        # so /drafts/<id>/layout-debug.json can return it.
        # ------------------------------------------------------------
        "layout_debug": {
            "ok": True,
            "pages": len(pages),
            "dpi": dpi,
            "preview_blocks": all_preview_blocks,
            "meta": {
                "source": source,
                "engine": "tesseract",
                "version": str(pytesseract.get_tesseract_version()),
                "config": _effective_ocr_config_string(),
                "conf_floor": LOW_CONF_DROP,
                "vision_layer": {
                    "enabled": ENABLE_VISION_PREPROCESS,
                    "debug_dir": VISION_DEBUG_DIR or None,
                },
                "multipass": {
                    "enabled": ENABLE_MULTIPASS_OCR,
                    "psms": MULTIPASS_PSMS,
                    "rotations": MULTIPASS_ROTATIONS,
                    "runs": multipass_runs_meta,
                },
                "orientation": page_orientations,
            },
        },

        "meta": {
            "source": source,
            "engine": "tesseract",
            "version": str(pytesseract.get_tesseract_version()),
            "config": _effective_ocr_config_string(),
            "conf_floor": LOW_CONF_DROP,
            "mode": (
                "high_clarity+segmentation+two_column_merge+"
                "category_infer+multi_price_variants+block_roles+"
                "multiline_reconstruct+variant_enrich+category_hierarchy+"
                "price_integrity_prep+structured_v2_prep+"
                "vision_scaffold+multipass_scaffold"
            ),
            "preprocess": "clahe+denoise+unsharp+deskew",
            "vision_layer": {
                "enabled": ENABLE_VISION_PREPROCESS,
                "debug_dir": VISION_DEBUG_DIR or None,
            },
            "multipass": {
                "enabled": ENABLE_MULTIPASS_OCR,
                "psms": MULTIPASS_PSMS,
                "rotations": MULTIPASS_ROTATIONS,
                "runs": multipass_runs_meta,
            },
            "orientation": page_orientations,
        },
    }
    return segmented


def run_layout_debug_for_pdf(
    pdf_path: str,
    dpi: int = DEFAULT_DPI,
) -> Dict[str, Any]:
    """
    Day 43 Phase 7 pt.3–4

    Canonical entrypoint for layout debug generation.
    This is what portal/app.py should call when it wants
    segmentation + preview_blocks + layout_debug payload.
    """
    segmented = segment_document(pdf_path=pdf_path, dpi=dpi)

    # Defensive: guarantee shape even if upstream changes
    layout_debug = segmented.get("layout_debug")
    if not layout_debug:
        layout_debug = {
            "ok": False,
            "error": "segment_document did not produce layout_debug",
        }

    return layout_debug
