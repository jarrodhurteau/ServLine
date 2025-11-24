# storage/ocr_facade.py
"""
OCR façade — Phase 3 + Phase 4 (Structured Output v2 + Superimport Prep)
Bridges the segmenter to higher-level app code.

Public API:
- extract_menu_from_pdf(path) -> (categories_dict, debug_payload)
- health() -> engine + versions
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional, TypedDict
import os
import shutil
import sys
from datetime import datetime

import pytesseract

# --- Ensure repo root + portal/ imports work ---
ROOT = Path(__file__).resolve().parents[1]       # /servline
UPLOAD_FOLDER = ROOT / "uploads"                 # ← FIX: Centralized upload directory

if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(ROOT / "portal") not in sys.path:
    sys.path.append(str(ROOT / "portal"))

# --- OCR/Pipeline imports ---
from portal.storage.ocr_pipeline import segment_document  # type: ignore
from portal.storage.ocr_utils import normalize_orientation  # type: ignore

try:
    from .ai_ocr_helper import analyze_ocr_text  # type: ignore
except Exception as e:  # pragma: no cover
    analyze_ocr_text = None  # type: ignore
    print(f"[OCR] Warning: ai_ocr_helper import failed in ocr_facade: {e!r}")

from .category_hierarchy import build_grouped_hierarchy

try:
    from .ocr_types import StructuredMenuPayload  # type: ignore
except Exception:
    class StructuredMenuPayload(TypedDict):
        categories: List[Dict[str, Any]]
        extracted_at: str
        source: Dict[str, Any]
        meta: Dict[str, Any]

PIPELINE_VERSION = "phase-4-structured_v2+superimport_prep+ai-helper-rev9"


# ============================================================================
# Tesseract config + health
# ============================================================================
def _tesseract_cmd() -> str:
    cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    if cmd:
        return cmd

    which = shutil.which("tesseract") or shutil.which("tesseract.exe") or ""
    if which:
        return which

    for p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(p).exists():
            return p

    return ""


def health() -> Dict[str, Any]:
    cmd = _tesseract_cmd()
    version: Optional[str] = None

    if cmd:
        try:
            pytesseract.pytesseract.tesseract_cmd = cmd
        except Exception:
            pass

        try:
            version = str(pytesseract.get_tesseract_version())
        except Exception:
            version = None

    poppler_path = os.getenv("POPPLER_PATH") or ""

    return {
        "engine": "servline-ocr",
        "pipeline_version": PIPELINE_VERSION,
        "tesseract": {
            "cmd": cmd,
            "version": version,
            "found_on_disk": bool(cmd and Path(cmd).exists()),
        },
        "poppler": {
            "path_env": poppler_path,
            "present": bool(poppler_path and Path(poppler_path).exists()),
        },
    }


# ============================================================================
# Orientation helper
# ============================================================================
def _auto_rotate_pdf_if_needed(pdf_path: str) -> str:
    try:
        from pdf2image import convert_from_path
        poppler_path = os.getenv("POPPLER_PATH") or None

        pages = convert_from_path(pdf_path, dpi=150, poppler_path=poppler_path)
        if not pages:
            return pdf_path

        rotated_pages = []
        any_changed = False
        applied_degs: List[int] = []

        for im in pages:
            im2, deg = normalize_orientation(im)
            applied_degs.append(int(deg or 0))
            if int(deg or 0) != 0:
                any_changed = True
            if im2.mode != "RGB":
                im2 = im2.convert("RGB")
            rotated_pages.append(im2)

        if not any_changed:
            return pdf_path

        tmp_out = str(Path(pdf_path).with_name(Path(pdf_path).stem + "_upright_tmp.pdf"))
        rotated_pages[0].save(tmp_out, save_all=True, append_images=rotated_pages[1:])
        print(f"[Orientation] Applied per-page normalization: {applied_degs} → {os.path.basename(tmp_out)}")
        return tmp_out

    except Exception as e:
        print(f"[Orientation] Skipped (error: {e})")
        return pdf_path


# ============================================================================
# Layout → text flatten
# ============================================================================
def _layout_to_raw_text(layout: Dict[str, Any]) -> str:
    lines: List[str] = []
    for blk in layout.get("blocks", []):
        for ln in blk.get("lines", []):
            t = (ln.get("text") or "").strip()
            if t:
                lines.append(t)
        lines.append("")  # block break
    return "\n".join(lines).strip()


# ============================================================================
# AI → categories
# ============================================================================
def _group_items_into_categories(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cats: Dict[str, List[Dict[str, Any]]] = {}

    for it in items:
        cat = it.get("canonical_category") or it.get("category") or "Uncategorized"
        name = (it.get("name") or "").strip() or "Untitled"
        desc = it.get("description")
        variants = it.get("variants") or []

        sizes: List[Dict[str, Any]] = []

        if variants:
            for v in variants:
                lbl = str(v.get("label") or "Var").strip()
                raw = v.get("price", None)
                if raw is None and v.get("price_cents") is not None:
                    try:
                        raw = float(v["price_cents"]) / 100.0
                    except Exception:
                        raw = 0.0
                try:
                    price = float(raw or 0.0)
                except Exception:
                    price = 0.0

                if price > 0:
                    sizes.append({"label": lbl, "price": round(price, 2)})

        else:
            pcs = it.get("price_candidates") or []
            if pcs:
                try:
                    base = float(pcs[0].get("value") or 0.0)
                except Exception:
                    base = 0.0
                if base > 0:
                    sizes.append({"label": "Base", "price": round(base, 2)})

        cats.setdefault(cat, []).append({
            "name": name,
            "description": desc or None,
            "sizes": sizes,
        })

    preferred = [
        "Pizza", "Specialty Pizzas", "Burgers & Sandwiches", "Wings",
        "Salads", "Sides & Apps", "Beverages", "Uncategorized",
    ]
    ordered = [c for c in preferred if c in cats] + [c for c in cats if c not in preferred]

    return [{"name": cname, "items": cats[cname]} for cname in ordered]


# ============================================================================
# Structured → superimport bundle
# ============================================================================
def _build_superimport_items(structured: StructuredMenuPayload) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    flat: List[Dict[str, Any]] = []
    stats = {"categories": 0, "items": 0, "variants": 0, "zero_price_items": 0}

    categories = structured["categories"]
    stats["categories"] = len(categories)

    position = 0
    for cat in categories:
        cat_name = (cat.get("name") or "Uncategorized").strip() or "Uncategorized"

        for item in cat.get("items", []) or []:
            stats["items"] += 1
            name = (item.get("name") or "").strip() or "Untitled"
            desc = item.get("description")

            sizes = item.get("sizes") or []
            variants = []
            base_cents = 0

            for sz in sizes:
                lbl = str(sz.get("label") or "Base").strip()
                try:
                    price = float(sz.get("price", 0.0))
                except Exception:
                    price = 0.0
                cents = int(round(price * 100))
                variants.append({"label": lbl, "price_cents": cents})
                if cents > 0 and (base_cents == 0 or cents < base_cents):
                    base_cents = cents

            if base_cents == 0:
                stats["zero_price_items"] += 1

            stats["variants"] += len(variants)

            flat.append({
                "name": name,
                "description": desc,
                "category": cat_name,
                "price_cents": base_cents,
                "position": position,
                "variants": variants or None,
            })

            position += 1

    return flat, stats


# ============================================================================
# *** CORE FIX: Robust path resolution into uploads/ ***
# ============================================================================
def _resolve_pdf_path(path: str) -> Path:
    """
    Fixes the exact bug you hit:
    - If `path` is relative or just a filename → resolve into uploads/
    - If absolute → trust it
    """
    candidate = Path(path)

    # Absolute path → use it
    if candidate.is_absolute():
        return candidate

    # Filename or relative → force into uploads/
    resolved = UPLOAD_FOLDER / candidate

    return resolved


# ============================================================================
# extract_menu_from_pdf (MAIN)
# ============================================================================
def extract_menu_from_pdf(path: str) -> Tuple[StructuredMenuPayload, Dict[str, Any]]:
    if analyze_ocr_text is None:
        raise RuntimeError("ai_ocr_helper is not available; cannot extract menu")

    # ← FIX: robust file resolution
    p = _resolve_pdf_path(path)

    if not p.exists():
        raise FileNotFoundError(str(p))

    # Orientation normalize
    upright_path = _auto_rotate_pdf_if_needed(str(p))

    # Segmenter
    layout = segment_document(pdf_path=upright_path, pdf_bytes=None, dpi=400)

    # Cleanup temp
    if upright_path != str(p):
        try:
            Path(upright_path).unlink(missing_ok=True)
        except Exception:
            pass

    # AI helper
    raw_text = _layout_to_raw_text(layout)
    ai_doc = analyze_ocr_text(raw_text, layout=layout, taxonomy=None, restaurant_profile=None)

    items = ai_doc.get("items", []) or []
    sections = ai_doc.get("sections", []) or []

    hierarchy = build_grouped_hierarchy(items, blocks=layout.get("text_blocks"))

    categories_list = _group_items_into_categories(items)
    if not categories_list:
        categories_list = [{
            "name": "Uncategorized",
            "items": [{
                "name": "No items recognized",
                "description": "OCR returned no items.",
                "sizes": [],
            }],
        }]

    extracted_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    categories: StructuredMenuPayload = {
        "categories": categories_list,
        "extracted_at": extracted_at,
        "source": {
            "type": "upload",
            "file": p.name,                     # ← existing behavior preserved
            "ocr_engine": "ocr_helper+tesseract",
        },
        "meta": {
            "pipeline_version": PIPELINE_VERSION,
            "superimport_prep": True,
            "hierarchy_preview": hierarchy,
        },
    }

    super_items, super_stats = _build_superimport_items(categories)
    categories["meta"]["superimport"] = {"items": super_items, "stats": super_stats}

    preview_blocks = layout.get("preview_blocks") or []
    text_blocks = layout.get("text_blocks") or layout.get("blocks") or []

    debug_payload: Dict[str, Any] = {
        "version": PIPELINE_VERSION,
        "layout": layout,
        "preview_blocks": preview_blocks,
        "text_blocks": text_blocks,
        "blocks": text_blocks,
        "notes": [
            "phase-4 segmentation",
            "orientation normalizer applied",
            "ai-helper rev9",
            "category hierarchy v2",
            "structured output v2 + superimport",
        ],
        "ai_preview": {
            "items": items,
            "sections": sections,
            "hierarchy": hierarchy,
        },
        "superimport": {
            "items": super_items,
            "stats": super_stats,
        },
    }

    return categories, debug_payload
