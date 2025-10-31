from __future__ import annotations
from typing import Any, Dict, List, Optional

# Public API expected by storage.ocr_facade -------------------------------

def extract_menu_from_pdf(pdf_path: str, *, restaurant_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Placeholder implementation. The rest of the app calls this.
    Return shape should include an 'artifacts' dict for debug meta.
    """
    return {
        "items": [],
        "artifacts": {
            "note": "pipeline_new.extract_menu_from_pdf (stub)",
            "input_pdf": str(pdf_path),
            "engine": "new",
            "tesseract_config": get_tesseract_config(),
        },
    }

def extract_menu_from_images(images: List[Any], *, restaurant_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Placeholder implementation for pre-rasterized pages (PIL/numpy frames).
    """
    num_frames = 0 if images is None else len(images)
    return {
        "items": [],
        "artifacts": {
            "note": "pipeline_new.extract_menu_from_images (stub)",
            "frames": num_frames,
            "engine": "new",
            "tesseract_config": get_tesseract_config(),
        },
    }

# Helpers -----------------------------------------------------------------

def get_tesseract_config() -> str:
    # Keep in sync with storage.ocr_facade._TESSERACT_CONFIG
    return "--oem 3 --psm 6 -c preserve_interword_spaces=1"
