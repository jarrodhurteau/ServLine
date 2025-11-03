from __future__ import annotations
from flask import Blueprint, jsonify
from .storage import ocr_utils

bp = Blueprint("ocr_health", __name__)

@bp.route("/ocr/health", methods=["GET"])
def ocr_health():
    return jsonify({
        "tesseract": ocr_utils.check_tesseract(),
        "poppler": ocr_utils.check_poppler(),
    })
