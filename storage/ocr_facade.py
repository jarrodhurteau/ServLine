# storage/ocr_facade.py
"""
OCR façade — Phase 1
Bridges the new segmenter to the app without changing higher-level contracts.
- extract_menu_from_pdf(path) -> (categories_dict, debug_payload)
- health() -> engine + versions
"""

from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Tuple
import os
import shutil
import sys
import pytesseract

# --- Make sure project root and portal/ are importable (helps runtime + Pylance) ---
ROOT = Path(__file__).resolve().parents[1]  # repo root
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(ROOT / "portal") not in sys.path:
    sys.path.append(str(ROOT / "portal"))

# Phase 1 segmenter (lives under portal/storage/*)
from portal.storage.ocr_pipeline import segment_document  # type: ignore
from pytesseract import image_to_osd

PIPELINE_VERSION = "phase-1-segmenter+autorotate"


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
    Detect sideways pages in a PDF and auto-rotate them upright before OCR.
    Returns the (possibly temporary) upright PDF path.
    """
    try:
        from pdf2image import convert_from_path
        poppler_path = os.getenv("POPPLER_PATH") or None
        pages = convert_from_path(pdf_path, dpi=150, poppler_path=poppler_path)
        if not pages:
            return pdf_path

        # Inspect first page with Tesseract OSD
        osd = image_to_osd(pages[0])
        if not any(k in osd for k in ("Rotate: 90", "Rotate: 180", "Rotate: 270")):
            return pdf_path

        print(f"[Auto-Rotate] Detected rotation in {os.path.basename(pdf_path)} → correcting…")

        rotated_pages = []
        for im in pages:
            try:
                if "Rotate: 90" in osd:
                    im = im.rotate(-90, expand=True)
                elif "Rotate: 270" in osd:
                    im = im.rotate(90, expand=True)
                elif "Rotate: 180" in osd:
                    im = im.rotate(180, expand=True)
            except Exception:
                pass
            # Ensure PDF-safe mode
            if im.mode != "RGB":
                im = im.convert("RGB")
            rotated_pages.append(im)

        tmp_out = str(Path(pdf_path).with_name(Path(pdf_path).stem + "_upright_tmp.pdf"))
        rotated_pages[0].save(tmp_out, save_all=True, append_images=rotated_pages[1:])
        return tmp_out
    except Exception as e:
        print(f"[Auto-Rotate] Skipped (no rotation detected or error: {e})")
        return pdf_path


def extract_menu_from_pdf(path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Phase 1: segment PDF/image into structured layout.
    Returns empty categories (no draft items yet) plus full layout for debug.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    # Ensure upright orientation before segmentation
    upright_path = _auto_rotate_pdf_if_needed(str(p))

    layout = segment_document(pdf_path=upright_path, pdf_bytes=None, dpi=300)

    # Clean up temporary upright file
    if upright_path != str(p):
        try:
            Path(upright_path).unlink(missing_ok=True)  # py3.8+: remove try if older
        except Exception:
            pass

    categories: Dict[str, Any] = {}  # still Phase 1
    debug_payload = {
        "version": PIPELINE_VERSION,
        "layout": layout,
        "notes": ["phase-1 segmentation only; auto-rotation active; no item parsing yet"],
    }
    return categories, debug_payload
