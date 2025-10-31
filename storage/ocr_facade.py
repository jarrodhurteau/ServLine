# storage/ocr_facade.py
"""
Stable OCR entrypoint used by the rest of ServLine.

Contract (what portal/app.py expects):
- `extract_menu_from_pdf(path)` MUST return either:
    (A) dict[str, list[ {name, description?, price?, confidence?, raw?} ]]
    (B) tuple[ dict[str, list[...]], debug_payload: dict ]
  where keys are normalized category names.

Notes:
- We select the NEW pipeline by default (`servline.ocr.pipeline_new`).
- You can force legacy with SERVLINE_OCR_ENGINE=old (if _old module exists).
- `health()` returns a small diagnostic block used by /ocr/health.
"""

from __future__ import annotations
import os
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------- Engine selection ----------
ENGINE = os.getenv("SERVLINE_OCR_ENGINE", "new").strip().lower()  # "new" | "old"

# NEW pipeline (preferred)
try:
    from servline.ocr import pipeline_new as _new  # type: ignore
except Exception as e:
    _new = None
    _NEW_IMPORT_ERROR = e
else:
    _NEW_IMPORT_ERROR = None

# OLD helpers (optional/temporary)
_old = None
_OLD_IMPORT_ERROR = None
if ENGINE == "old":
    try:
        # Keep this file out of the main import path in normal use.
        # If you truly need rollback, create storage/_old_ocr_helpers.py
        from storage._legacy import _old_ocr_helpers as _old  # type: ignore
    except Exception as e:
        _OLD_IMPORT_ERROR = e

# ---------- Shared config for Tesseract (mirrors new defaults) ----------
_TESSERACT_CONFIG = "--oem 3 --psm 6 -c preserve_interword_spaces=1"


# ---------- Public API (adapter to the contract) ----------
def extract_menu_from_pdf(pdf_path: str, *, restaurant_id: Optional[int] = None):
    """
    Returns either:
      • categories_dict
      • (categories_dict, debug_payload)
    """
    chosen = _choose_engine()
    t0 = time.time()
    try:
        if chosen == "new":
            if _new is None:
                raise RuntimeError(_format_new_import_error())
            raw = _new.extract_menu_from_pdf(pdf_path, restaurant_id=restaurant_id)  # type: ignore[attr-defined]
        else:
            if _old is None:
                raise RuntimeError(_format_old_import_error())
            # Legacy signature may differ; best-effort call
            raw = _old.extract_menu_from_pdf(pdf_path)  # type: ignore[attr-defined]

        cats, debug = _adapt_to_categories(raw, engine=chosen, duration=time.time() - t0)
        _log_ocr_usage(chosen, time.time() - t0, ok=True)
        return (cats, debug) if debug else cats
    except Exception as e:
        _log_ocr_usage(chosen, time.time() - t0, ok=False, err=e)
        raise


def extract_menu_from_images(images: List[Any], *, restaurant_id: Optional[int] = None):
    """
    Same contract as extract_menu_from_pdf(), but takes pre-rasterized pages.
    """
    chosen = _choose_engine()
    t0 = time.time()
    try:
        if chosen == "new":
            if _new is None:
                raise RuntimeError(_format_new_import_error())
            raw = _new.extract_menu_from_images(images, restaurant_id=restaurant_id)  # type: ignore[attr-defined]
        else:
            if _old is None:
                raise RuntimeError(_format_old_import_error())
            raw = _old.extract_menu_from_images(images)  # type: ignore[attr-defined]

        cats, debug = _adapt_to_categories(raw, engine=chosen, duration=time.time() - t0)
        _log_ocr_usage(chosen, time.time() - t0, ok=True)
        return (cats, debug) if debug else cats
    except Exception as e:
        _log_ocr_usage(chosen, time.time() - t0, ok=False, err=e)
        raise


def health() -> Dict[str, Any]:
    """
    Diagnostics used by /ocr/health.
    """
    tesseract_version = None
    tess_error = None
    try:
        import pytesseract  # type: ignore
        tesseract_version = str(getattr(pytesseract, "get_tesseract_version")())
    except Exception as e:
        tess_error = repr(e)

    new_ok = _new is not None and _NEW_IMPORT_ERROR is None
    old_ok = _old is not None and _OLD_IMPORT_ERROR is None if ENGINE == "old" else None

    return {
        "engine": _choose_engine(),
        "new_import_ok": new_ok,
        "old_import_ok": old_ok,
        "tesseract": {"version": tesseract_version, "error": tess_error, "config": _TESSERACT_CONFIG},
    }


# ---------- Adapters / Helpers ----------
def _choose_engine() -> str:
    return "old" if ENGINE == "old" else "new"


def _format_new_import_error() -> str:
    return (
        "Failed to import servline.ocr.pipeline_new for the NEW OCR engine.\n"
        f"Import error: {repr(_NEW_IMPORT_ERROR)}\n"
        "Make sure the new OCR package exists and is on PYTHONPATH.\n"
        "Expected path: servline/ocr/pipeline_new.py"
    )


def _format_old_import_error() -> str:
    return (
        "Old OCR engine was requested (SERVLINE_OCR_ENGINE=old) but could not be imported.\n"
        f"Import error: {repr(_OLD_IMPORT_ERROR)}\n"
        "Ensure storage/_old_ocr_helpers.py exists if you truly need rollback."
    )


def _adapt_to_categories(raw: Any, *, engine: str, duration: float) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, Any]]:
    """
    Normalize various possible shapes from the underlying pipeline into:
      categories: dict[str, list[item]]
    and build a compact debug payload for tooltips / inspector.
    Recognized shapes for `raw`:
      - Already a dict[str, list[items]]
      - {'categories': [ {'name': str, 'items': [ ... ]}, ... ]}
      - {'items': [ {'name':..., 'category':...}, ... ], 'artifacts'?: {...}, 'debug'?: {...}}
      - {'rows': [...]}  (fallback heuristic)
    """
    categories: Dict[str, List[Dict[str, Any]]] = {}
    debug: Dict[str, Any] = {}

    def _push(cat: str, item: Dict[str, Any]):
        ckey = (cat or "Uncategorized").strip() or "Uncategorized"
        categories.setdefault(ckey, []).append(_slim_item(item))

    # 1) Already a mapping of categories -> items
    if isinstance(raw, dict) and raw and all(isinstance(v, list) for v in raw.values()) and any(isinstance(k, str) for k in raw.keys()):
        categories = {str(k): [_slim_item(it) for it in (v or [])] for k, v in raw.items()}
    # 2) List-of-categories shape
    elif isinstance(raw, dict) and isinstance(raw.get("categories"), list):
        for cat in (raw.get("categories") or []):
            cname = (cat.get("name") or "Uncategorized")
            for it in (cat.get("items") or []):
                _push(cname, it)
    # 3) Flat items with category field
    elif isinstance(raw, dict) and isinstance(raw.get("items"), list):
        for it in (raw.get("items") or []):
            _push((it.get("category") or "Uncategorized"), it)
    # 4) Rows fallback (best-effort)
    elif isinstance(raw, dict) and isinstance(raw.get("rows"), list):
        for r in (raw.get("rows") or []):
            _push((r.get("category") or "Uncategorized"), r)
    else:
        # Last resort: single bucket
        items = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            # try to find something list-like
            for k, v in raw.items():
                if isinstance(v, list):
                    items = v
                    break
        categories["Uncategorized"] = [_slim_item(x) for x in (items or [])]

    # Build debug payload from common fields if present
    if isinstance(raw, dict):
        if "debug" in raw and isinstance(raw["debug"], dict):
            debug.update(raw["debug"])
        if "artifacts" in raw and isinstance(raw["artifacts"], dict):
            debug.setdefault("artifacts", {}).update(raw["artifacts"])
        # keep a tiny sample for inspection
        if isinstance(raw.get("items"), list):
            debug.setdefault("items_sample", raw["items"][:40])

    # Always include meta
    debug.setdefault("meta", {})["engine_used"] = engine
    debug["meta"]["tesseract_config"] = _TESSERACT_CONFIG
    debug["meta"]["duration_seconds"] = round(duration, 3)

    # Strip empty categories
    categories = {k: v for k, v in categories.items() if v}

    if not categories:
        categories = {"Uncategorized": []}

    return categories, debug


def _slim_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reduce any verbose upstream item to the fields our draft bridge understands.
    """
    name = (it.get("name") or "").strip()
    desc = (it.get("description") or "").strip()
    price = None

    # Accept several price conventions
    if "price" in it:
        price = it.get("price")
    elif "price_cents" in it:
        try:
            price = float(it.get("price_cents", 0)) / 100.0
        except Exception:
            price = None
    elif "price_candidates" in it and isinstance(it["price_candidates"], list) and it["price_candidates"]:
        try:
            price = float((it["price_candidates"][0] or {}).get("value", 0))
        except Exception:
            price = None

    conf = it.get("confidence")
    if isinstance(conf, float) and conf <= 1.0:
        conf = int(round(conf * 100))
    elif isinstance(conf, (int, float)):
        try:
            conf = int(round(float(conf)))
        except Exception:
            conf = None
    else:
        conf = None

    return {
        "name": name or "Untitled",
        "description": desc or "",
        "price": price,                 # draft bridge converts to cents / sizes
        "confidence": conf,
        "raw": {k: it[k] for k in it.keys() if k not in {"name", "description", "price", "price_cents", "price_candidates", "confidence"}},
    }


def _attach_meta(result: Dict[str, Any], engine: str, duration_s: float) -> Dict[str, Any]:
    result = dict(result or {})
    artifacts = dict(result.get("artifacts") or {})
    artifacts.update({
        "engine_used": engine,
        "tesseract_config": _TESSERACT_CONFIG,
        "duration_seconds": round(duration_s, 3),
    })
    result["artifacts"] = artifacts
    return result


def _log_ocr_usage(engine: str, duration: float, ok: bool, err: Optional[BaseException] = None) -> None:
    status = "OK" if ok else f"ERROR: {repr(err)}"
    print(f"[OCR] engine={engine} duration={duration:.3f}s status={status}")

