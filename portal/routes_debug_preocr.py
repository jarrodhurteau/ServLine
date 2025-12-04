# portal/routes_debug_preocr.py
from flask import Blueprint, send_file, request, abort, Response
from pathlib import Path
from portal.ocr_worker import ocr_image, _prep_cv
from storage.ocr_pipeline import segment_document  # <-- NEW: use Phase-3 pipeline
from PIL import Image
import io
import os
import json

debug_preocr = Blueprint("debug_preocr", __name__)


@debug_preocr.route("/debug/pre_ocr_image")
def pre_ocr_image():
    """
    Debug endpoint to view the preprocessed (pre-OCR) image.

    Usage:
      /debug/pre_ocr_image?path=C:/full/path/to/menu.jpg

    NOTE: This endpoint expects a *filesystem path* via the 'path' query param.
    It does NOT accept job_id. For imports, look up the upload path in the
    database or from the imports table and pass that as ?path=...
    """
    p = request.args.get("path")

    # If someone passes job_id by mistake, give a clear message.
    if not p:
        job_id = request.args.get("job_id")
        if job_id:
            abort(
                400,
                "This debug endpoint expects ?path=FULL_FILE_PATH, not ?job_id. "
                "Look up the upload path in the database or from the imports table "
                "and pass that as ?path=...",
            )
        abort(400, "Missing 'path' query parameter (?path=FULL_FILE_PATH).")

    if not os.path.exists(p):
        return abort(404, "Image not found")

    pil = Image.open(p)
    bw = _prep_cv(pil, source_path=Path(p))
    buf = io.BytesIO()
    Image.fromarray(bw).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@debug_preocr.route("/debug/ocr_text")
def ocr_text():
    """
    Debug endpoint to run OCR on a single file.

    Usage (image):
      /debug/ocr_text?path=C:/full/path/to/menu.png

    Usage (PDF):
      /debug/ocr_text?path=C:/full/path/to/menu.pdf

    - For images: returns raw OCR text (text/plain).
    - For PDFs: uses the Phase-3 segment_document() pipeline and returns JSON.
    """
    p = request.args.get("path")

    # If someone passes job_id by mistake, give a clear message.
    if not p:
        job_id = request.args.get("job_id")
        if job_id:
            abort(
                400,
                "This debug endpoint expects ?path=FULL_FILE_PATH, not ?job_id. "
                "Look up the upload path in the database or from the imports table "
                "and pass that as ?path=...",
            )
        abort(400, "Missing 'path' query parameter (?path=FULL_FILE_PATH).")

    if not os.path.exists(p):
        return abort(404, "Image not found")

    lower = p.lower()

    # 🔹 If it's a PDF, run the new Phase-3 pipeline and dump JSON.
    if lower.endswith(".pdf"):
        try:
            segmented = segment_document(pdf_path=p)
        except Exception as exc:
            # Surface the error clearly instead of a generic 500 page.
            return Response(
                f"segment_document() failed for {p}:\n{exc}",
                mimetype="text/plain; charset=utf-8",
                status=500,
            )
        return Response(
            json.dumps(segmented, indent=2),
            mimetype="application/json",
        )

    # 🔹 Otherwise treat it as an image and use the legacy ocr_image() helper.
    try:
        txt = ocr_image(Path(p))
    except Exception as exc:
        return Response(
            f"ocr_image() failed for {p}:\n{exc}",
            mimetype="text/plain; charset=utf-8",
            status=500,
        )

    return Response(txt, mimetype="text/plain; charset=utf-8")
