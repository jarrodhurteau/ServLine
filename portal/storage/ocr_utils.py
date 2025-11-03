"""
ServLine OCR Utils — helpers for PDF→image, preprocessing, and env checks.
Phase 1 keeps dependencies light: Pillow, pdf2image, pytesseract.
"""

from __future__ import annotations
import os
import re
import glob
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, ImageFilter, ImageEnhance
from pdf2image import convert_from_path, convert_from_bytes
import pytesseract


# -----------------------------
# Poppler (for pdf2image)
# -----------------------------

def get_poppler_path() -> Optional[str]:
    """
    Returns a directory path to Poppler's 'bin' folder if found (Windows),
    or None to let pdf2image use system default search (Linux/macOS).
    Priority:
      1) POPPLER_PATH env var (should point to the folder that contains pdfinfo.exe)
      2) Common Windows install locations (best-effort glob)
    """
    env = os.environ.get("POPPLER_PATH")
    if env and os.path.isdir(env):
        return env

    if os.name == "nt":
        candidates = []
        # Common Chocolatey/Scoop/Manual install patterns
        candidates += glob.glob(r"C:\Program Files\poppler*\bin")
        candidates += glob.glob(r"C:\Program Files (x86)\poppler*\bin")
        candidates += glob.glob(r"C:\poppler*\bin")
        candidates += glob.glob(r"C:\tools\poppler*\bin")
        # Some packages place binaries in ...\Library\bin
        candidates += glob.glob(r"C:\Program Files\poppler*\Library\bin")
        candidates += glob.glob(r"C:\Program Files (x86)\poppler*\Library\bin")

        for path in candidates:
            if os.path.isfile(os.path.join(path, "pdfinfo.exe")):
                return path

    return None


def check_poppler() -> dict:
    """
    Lightweight check to report whether Poppler is likely available.
    """
    path = get_poppler_path()
    ok = True
    if os.name == "nt":
        ok = bool(path and os.path.isfile(os.path.join(path, "pdfinfo.exe")))
    # On non-Windows, assume system packages provide poppler utils in PATH
    return {"found_on_disk": bool(ok), "path": path}


# -----------------------------
# Tesseract
# -----------------------------

def configure_tesseract_from_env() -> None:
    """
    Optionally point pytesseract at a custom tesseract.exe.
    Honor TESSERACT_CMD env var if provided (Windows-friendly).
    """
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd and os.path.isfile(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd


def check_tesseract() -> dict:
    """
    Returns presence and version info for Tesseract.
    """
    try:
        configure_tesseract_from_env()
        ver = pytesseract.get_tesseract_version()
        return {"found_on_disk": True, "version": str(ver)}
    except Exception as e:
        return {"found_on_disk": False, "version": None, "error": str(e)}


# -----------------------------
# PDF → PIL.Image sequence
# -----------------------------

def pdf_to_images_from_path(pdf_path: str, dpi: int = 300) -> List[Image.Image]:
    """
    Render a PDF file into a list of PIL Images at provided DPI.
    On Windows, passes poppler_path when available.
    """
    poppler_path = get_poppler_path()
    if poppler_path and os.name == "nt":
        return convert_from_path(pdf_path, dpi=dpi, poppler_path=poppler_path)
    return convert_from_path(pdf_path, dpi=dpi)


def pdf_to_images_from_bytes(pdf_bytes: bytes, dpi: int = 300) -> List[Image.Image]:
    """
    Render a PDF (bytes) into a list of PIL Images at provided DPI.
    On Windows, passes poppler_path when available.
    """
    poppler_path = get_poppler_path()
    if poppler_path and os.name == "nt":
        return convert_from_bytes(pdf_bytes, dpi=dpi, poppler_path=poppler_path)
    return convert_from_bytes(pdf_bytes, dpi=dpi)


# -----------------------------
# Image preprocessing (light)
# -----------------------------

def normalize_image(
    im: Image.Image,
    to_grayscale: bool = True,
    contrast_boost: float = 1.15,
    sharpen_radius: float = 1.0,
    unsharp_percent: int = 120,
    unsharp_threshold: int = 3,
) -> Image.Image:
    """
    Light preprocessing for OCR:
      - optional grayscale
      - mild contrast boost
      - unsharp mask to crisp glyph edges
    Keeps things conservative to avoid harming small text.
    """
    out = im
    if to_grayscale and out.mode != "L":
        out = ImageOps.grayscale(out)

    if contrast_boost and contrast_boost != 1.0:
        out = ImageEnhance.Contrast(out).enhance(contrast_boost)

    # PIL's UnsharpMask: radius (float), percent (int), threshold (int)
    if sharpen_radius > 0:
        out = out.filter(ImageFilter.UnsharpMask(radius=sharpen_radius,
                                                 percent=unsharp_percent,
                                                 threshold=unsharp_threshold))
    return out


# -----------------------------
# Heuristics helpers
# -----------------------------

def median(values: List[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    n = len(vals)
    mid = n // 2
    if n % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
