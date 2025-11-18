# storage/ocr_facade.py
"""
OCR façade — Phase 3 (+ AI helper rev7)
Bridges the segmenter to higher-level app code.

Public API:
- extract_menu_from_pdf(path) -> (categories_dict, debug_payload)
- health() -> engine + versions
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
import os
import shutil
import sys
from datetime import datetime

import pytesseract

# --- Ensure project root + portal/ are importable (so storage + portal both work) ---
ROOT = Path(__file__).resolve().parents[1]  # repo root: .../servline
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(ROOT / "portal") not in sys.path:
    sys.path.append(str(ROOT / "portal"))

# Phase 3 segmenter (lives under portal/storage/*)
from portal.storage.ocr_pipeline import segment_document  # type: ignore

# Orientation + image helpers (lives under portal/storage/*)
from portal.storage.ocr_utils import normalize_orientation  # type: ignore

# AI parsing helper (lives alongside this file)
try:
    from .ai_ocr_helper import analyze_ocr_text  # type: ignore
except Exception as e:
    analyze_ocr_text = None  # type: ignore
    print(f"[OCR] Warning: ai_ocr_helper import failed in ocr_facade: {e!r}")


PIPELINE_VERSION = "phase-4-segmenter(block_roles+multiline)+ai-helper-rev9"


def _tesseract_cmd() -> str:
    """Locate the tesseract executable on disk."""
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
    """Return OCR engine + environment health info."""
    try:
        ver = str(pytesseract.get_tesseract_version())
    except Exception:
        ver = None
    poppler = os.getenv("POPPLER_PATH") or ""
    return {
        "engine": "servline-ocr",
        "pipeline_version": PIPELINE_VERSION,
        "tesseract": {
            "cmd": _tesseract_cmd(),
            "version": ver,
            "found_on_disk": bool(_tesseract_cmd()),
        },
        "poppler": {
            "path_env": poppler,
            "present": bool(poppler and Path(poppler).exists()),
        },
    }


def _auto_rotate_pdf_if_needed(pdf_path: str) -> str:
    """
    Normalize page orientation deterministically using storage.ocr_utils.normalize_orientation.
    If any page is adjusted, write a temporary upright PDF and return its path;
    otherwise return the original path.
    """
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
            im2, deg = normalize_orientation(im)  # <- EXIF + OSD fallback
            applied_degs.append(int(deg or 0))
            any_changed = any_changed or (int(deg or 0) != 0)
            if im2.mode != "RGB":
                im2 = im2.convert("RGB")
            rotated_pages.append(im2)

        if not any_changed:
            return pdf_path

        # Save temp upright PDF alongside the original
        tmp_out = str(Path(pdf_path).with_name(Path(pdf_path).stem + "_upright_tmp.pdf"))
        rotated_pages[0].save(tmp_out, save_all=True, append_images=rotated_pages[1:])
        print(f"[Orientation] Applied per-page normalization: {applied_degs} → {os.path.basename(tmp_out)}")
        return tmp_out

    except Exception as e:
        print(f"[Orientation] Skipped (error: {e})")
        return pdf_path


def _layout_to_raw_text(layout: Dict[str, Any]) -> str:
    """
    Flatten segmented layout into a rough reading-order plaintext.
    Each line is a segmented line.text; blocks/lines are already y-then-x ordered.
    """
    lines: List[str] = []
    for b in layout.get("blocks", []):
        for ln in b.get("lines", []):
            t = (ln.get("text") or "").strip()
            if t:
                lines.append(t)
        # block break to help header/section detection
        lines.append("")
    return "\n".join(lines).strip()


def _group_items_into_categories(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert AI-helper items to the portal's categories schema:
    [
      {
        "name": "Pizza",
        "items": [
          {
            "name": ...,
            "description": ...,
            "sizes": [
              {"label":"L","price":12.99},
              ...
            ]
          }
        ]
      },
      ...
    ]
    """
    cats: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        cat = it.get("category") or "Uncategorized"
        name = (it.get("name") or "").strip() or "Untitled"
        desc = it.get("description")
        variants = it.get("variants") or []

               # Build sizes from variants if present; otherwise seed from first price candidate (as "Base")
        sizes: List[Dict[str, Any]] = []
        if variants:
            for v in variants:
                lbl = str(v.get("label") or "Var").strip()

                # Support both old-style {"price": 12.99} and new-style {"price_cents": 1299}
                raw_price = v.get("price", None)
                if raw_price is None and v.get("price_cents") is not None:
                    try:
                        raw_price = float(v.get("price_cents")) / 100.0
                    except Exception:
                        raw_price = 0.0

                try:
                    pr = float(raw_price or 0.0)
                except Exception:
                    pr = 0.0

                if pr > 0:
                    sizes.append({"label": lbl, "price": round(pr, 2)})
        else:
            pcs = it.get("price_candidates") or []
            if pcs:
                try:
                    base = float(pcs[0].get("value") or 0.0)
                except Exception:
                    base = 0.0
                if base > 0:
                    sizes.append({"label": "Base", "price": round(base, 2)})

        cats.setdefault(cat, []).append(
            {
                "name": name,
                "description": desc or None,
                "sizes": sizes,  # may be []
            }
        )

    # materialize in stable order (Pizza, Specialty Pizzas, etc. first-ish)
    preferred = [
        "Pizza",
        "Specialty Pizzas",
        "Burgers & Sandwiches",
        "Wings",
        "Salads",
        "Sides & Apps",
        "Beverages",
        "Uncategorized",
    ]
    ordered_names = [c for c in preferred if c in cats] + [c for c in cats.keys() if c not in preferred]

    return [{"name": cname, "items": cats[cname]} for cname in ordered_names]


def extract_menu_from_pdf(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Segment PDF/image into structured layout, then run AI helper to produce draft items.
    Returns:
        categories_dict: {
            "categories": [ { "name": ..., "items": [ {"name":...., "description":...., "sizes":[...]}, ... ] },
            "extracted_at": "...Z",
            "source": { "type": "upload", "file": "<basename>", "ocr_engine": "ocr_helper+tesseract" }
        }
        debug_payload: {
            "version": PIPELINE_VERSION,
            "layout": <segmenter output>,
            "notes": [...],
            "ai_preview": { "items": [...], "sections": [...] }
        }
    """
    # Safety: ensure analyze_ocr_text imported correctly
    if analyze_ocr_text is None:
        raise RuntimeError("ai_ocr_helper is not available; cannot extract menu")

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    # Ensure upright orientation before segmentation (per-page normalize)
    upright_path = _auto_rotate_pdf_if_needed(str(p))

    # Phase 3 segmenter: high-clarity + segmentation + categories + multi-price/variants
    layout = segment_document(pdf_path=upright_path, pdf_bytes=None, dpi=400)

    # Clean up temporary upright file if created
    if upright_path != str(p):
        try:
            Path(upright_path).unlink(missing_ok=True)
        except Exception:
            pass

    # ---- AI helper pass: lines → items ----
    raw_text = _layout_to_raw_text(layout)
    ai_doc = analyze_ocr_text(
        raw_text,
        layout=layout,
        taxonomy=None,
        restaurant_profile=None,
    )
    items = ai_doc.get("items", [])
    sections = ai_doc.get("sections", [])

    # Build categories payload expected by portal
    categories_list = _group_items_into_categories(items)
    categories: Dict[str, Any] = {
        "categories": categories_list
        if categories_list
        else [
            {
                "name": "Uncategorized",
                "items": [
                    {
                        "name": "No items recognized",
                        "description": "OCR returned no items.",
                        "sizes": [],
                    }
                ],
            }
        ],
        "extracted_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "source": {
            "type": "upload",
            "file": p.name,
            "ocr_engine": "ocr_helper+tesseract",
        },
    }

    # Rich debug / preview blob for UI
    debug_payload: Dict[str, Any] = {
        "version": PIPELINE_VERSION,
        "layout": layout,
        "notes": [
            "phase-4 segmentation: blocks + text_blocks + categories + multi-price variants + block roles + multiline reconstruction",
            "per-page orientation normalizer applied when needed",
            "ai-helper (rev9) applied: dot leaders, next-line prices, size pairs, wide-gap splits, price bounds, multi-item splitter",
        ],
        "ai_preview": {
            "items": items,
            "sections": sections,
        },
    }


    return categories, debug_payload
