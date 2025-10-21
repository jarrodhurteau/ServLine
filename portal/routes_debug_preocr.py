# portal/routes_debug_preocr.py
from flask import Blueprint, send_file, request, abort, Response
from pathlib import Path
from portal.ocr_worker import ocr_image, _prep_cv
from PIL import Image
import io
import os

debug_preocr = Blueprint("debug_preocr", __name__)

@debug_preocr.route("/debug/pre_ocr_image")
def pre_ocr_image():
    # /debug/pre_ocr_image?path=C:\full\path\to\menu.jpg
    p = request.args.get("path")
    if not p or not os.path.exists(p):
        return abort(404, "Image not found")
    pil = Image.open(p)
    bw = _prep_cv(pil, source_path=Path(p))
    buf = io.BytesIO()
    Image.fromarray(bw).save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

@debug_preocr.route("/debug/ocr_text")
def ocr_text():
    # /debug/ocr_text?path=C:\full\path\to\menu.jpg
    p = request.args.get("path")
    if not p or not os.path.exists(p):
        return abort(404, "Image not found")
    txt = ocr_image(Path(p))
    return Response(txt, mimetype="text/plain; charset=utf-8")
