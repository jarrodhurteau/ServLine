# storage/ocr_facade.py
"""
OCR façade — Phase 3 + Phase 4 (Structured Output v2 + Superimport Prep)
Bridges the segmenter to higher-level app code.

Public API:
- extract_menu_from_pdf(path) -> (categories_dict, debug_payload)
- health() -> engine + versions

`categories_dict` is a structured menu payload (StructuredMenuPayload-like):
{
  "categories": [
    {
      "name": "Pizza",
      "items": [
        {"name": "...", "description": "...", "sizes": [{"label": "Lg", "price": 12.99}, ...]},
        ...
      ],
    },
    ...
  ],
  "extracted_at": "...Z",
  "source": {...},
  "meta": {
    "pipeline_version": "...",
    "superimport_prep": true,
    "hierarchy_preview": {...}
  }
}
"""

from __future__ import annotations
from pathlib import Path
from typing import (
    Dict,
    Any,
    Tuple,
    List,
    Optional,
)
from typing import TypedDict
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

# Phase 3/4 segmenter (lives under portal/storage/*)
from portal.storage.ocr_pipeline import segment_document  # type: ignore

# Orientation + image helpers (lives under portal/storage/*)
from portal.storage.ocr_utils import normalize_orientation  # type: ignore

# AI parsing helper (lives alongside this file)
try:
    from .ai_ocr_helper import analyze_ocr_text  # type: ignore
except Exception as e:  # pragma: no cover - soft failure; surfaced in extract_menu_from_pdf
    analyze_ocr_text = None  # type: ignore
    print(f"[OCR] Warning: ai_ocr_helper import failed in ocr_facade: {e!r}")

# Category Hierarchy v2 (Phase 4 pt.7–8)
from .category_hierarchy import build_grouped_hierarchy

# Structured menu payload types (Phase 4 pt.11–12)
try:  # pragma: no cover - typing/shape only
    from .ocr_types import StructuredMenuPayload  # type: ignore
except Exception:
    class StructuredMenuPayload(TypedDict):
        """
        Minimal structured menu payload used by ocr_facade.

        Designed to stay compatible with storage.ocr_types.StructuredMenuPayload:
        {
          "categories": [...],
          "extracted_at": "...Z",
          "source": {...},
          "meta": {...},
        }
        """
        categories: List[Dict[str, Any]]
        extracted_at: str
        source: Dict[str, Any]
        meta: Dict[str, Any]


PIPELINE_VERSION = "phase-4-structured_v2+superimport_prep+ai-helper-rev9"


def _tesseract_cmd() -> str:
    """Locate the tesseract executable on disk."""
    # Prefer whatever pytesseract already thinks it should use
    cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "") or ""
    if cmd:
        return cmd

    # PATH
    which = shutil.which("tesseract") or shutil.which("tesseract.exe") or ""
    if which:
        return which

    # Common Windows install paths
    for p in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ):
        if Path(p).exists():
            return p

    return ""


def health() -> Dict[str, Any]:
    """
    Return OCR engine + environment health info.

    IMPORTANT: we now drive both `cmd` and `version` off the same resolved
    executable, so /ocr/health.tesseract and ocr_lib_health.tesseract stay
    in sync instead of disagreeing.
    """
    # Single source of truth for the executable
    cmd = _tesseract_cmd()
    version: Optional[str] = None

    if cmd:
        try:
            # Ensure pytesseract uses the same executable we report
            pytesseract.pytesseract.tesseract_cmd = cmd
        except Exception:
            # Even if this fails, we still surface cmd + found_on_disk
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

    Phase 4 note:
    - Prefer canonical_category if present (post-normalization), else fallback
      to item.category, then "Uncategorized".
    """
    cats: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        cat = (
            it.get("canonical_category")
            or it.get("category")
            or "Uncategorized"
        )

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
            # Legacy fallback: first price_candidate.value (if AI helper exposes it)
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


def _build_superimport_items(structured: StructuredMenuPayload) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Given a StructuredMenuPayload-like dict, produce:

    - items: a flat list of "draft-like" items:
      {
        "name": str,
        "description": Optional[str],
        "category": str,
        "price_cents": int,
        "position": int,
        "variants": Optional[List[{"label": str, "price_cents": int}]],
      }

    - stats: simple counts for meta/debug (categories, items, variants, zero_price_items).
    """
    flat: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {
        "categories": 0,
        "items": 0,
        "variants": 0,
        "zero_price_items": 0,
    }

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
            variants: List[Dict[str, Any]] = []
            base_price_cents = 0

            for sz in sizes:
                label = str(sz.get("label") or "Base").strip() or "Base"
                raw_price = sz.get("price", 0.0)
                try:
                    price = float(raw_price or 0.0)
                except Exception:
                    price = 0.0
                cents = int(round(price * 100))
                variants.append({"label": label, "price_cents": cents})
                if cents > 0 and (base_price_cents == 0 or cents < base_price_cents):
                    base_price_cents = cents

            if base_price_cents == 0:
                stats["zero_price_items"] += 1

            stats["variants"] += len(variants)

            flat.append(
                {
                    "name": name,
                    "description": desc,
                    "category": cat_name,
                    "price_cents": base_price_cents,
                    "position": position,
                    "variants": variants or None,
                }
            )
            position += 1

    return flat, stats


def extract_menu_from_pdf(path: str) -> Tuple[StructuredMenuPayload, Dict[str, Any]]:
    """
    Segment PDF/image into structured layout, then run AI helper to produce draft items.
    Returns:
        categories_dict (StructuredMenuPayload-like): {
            "categories": [ { "name": ..., "items": [ {"name":...., "description":...., "sizes":[...]}, ... ] },
            "extracted_at": "...Z",
            "source": { "type": "upload", "file": "<basename>", "ocr_engine": "ocr_helper+tesseract" },
            "meta": {
                "pipeline_version": PIPELINE_VERSION,
                "superimport_prep": True,
                "hierarchy_preview": <grouped hierarchy tree or None>,
            },
        }
        debug_payload: {
            "version": PIPELINE_VERSION,
            "layout": <segmenter output>,
            "preview_blocks": [...],
            "text_blocks": [...],
            "blocks": [...],
            "notes": [...],
            "ai_preview": { "items": [...], "sections": [...], "hierarchy": {...} }
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

    # Phase 3/4 segmenter: high-clarity + segmentation + categories + multi-price/variants
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
    items = ai_doc.get("items", []) or []
    sections = ai_doc.get("sections", []) or []

    # Phase 4 pt.7–8: Category Hierarchy v2 (grouping)
    hierarchy = build_grouped_hierarchy(
        items,
        blocks=layout.get("text_blocks"),  # safe if missing / None
    )

    # Build categories payload expected by portal
    categories_list = _group_items_into_categories(items)

    extracted_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    if categories_list:
        payload_categories = categories_list
    else:
        payload_categories = [
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
        ]

    categories: StructuredMenuPayload = {
        "categories": payload_categories,
        "extracted_at": extracted_at,
        "source": {
            "type": "upload",
            "file": p.name,
            "ocr_engine": "ocr_helper+tesseract",
        },
        # Phase 4 pt.11–12: structured output meta + superimport prep hooks
        "meta": {
            "pipeline_version": PIPELINE_VERSION,
            "superimport_prep": True,
            # Lightweight, normalized section tree for downstream phases
            "hierarchy_preview": hierarchy,
        },
    }

    # Phase 4 pt.12: build superimport bundle (flat draft-like items + stats)
    super_items, super_stats = _build_superimport_items(categories)
    categories["meta"]["superimport"] = {
        "items": super_items,
        "stats": super_stats,
    }

    # ---- NEW: expose preview/text blocks at top-level for overlays ----
    preview_blocks = layout.get("preview_blocks") or []
    text_blocks = layout.get("text_blocks") or layout.get("blocks") or []

    # Rich debug / preview blob for UI
    debug_payload: Dict[str, Any] = {
        "version": PIPELINE_VERSION,
        "layout": layout,
        # These three are what /drafts/<id>/blocks and /debug/blocks expect:
        "preview_blocks": preview_blocks,
        "text_blocks": text_blocks,
        "blocks": text_blocks,
        "notes": [
            "phase-4 segmentation: blocks + text_blocks + categories + multi-price variants + block roles + multiline reconstruction",
            "per-page orientation normalizer applied when needed",
            "ai-helper (rev9) applied: dot leaders, next-line prices, size pairs, wide-gap splits, price bounds, multi-item splitter",
            "category hierarchy v2: canonical categories + grouped subcategories",
            "structured output v2 + superimport prep: hierarchy_preview + stable categories payload",
            "superimport bundle attached: flat items + stats for downstream importers",
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
