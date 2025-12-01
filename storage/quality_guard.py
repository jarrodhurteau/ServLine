"""
Quality Guard — Phase 5 Stabilization Layer (Day 36)

PURPOSE:
Read-only analysis of OCR + AI cleanup output.
This module never modifies text. It only *measures and flags* quality and risk.

GOAL:
Provide an objective health report for every draft so we can:
- See failure patterns
- Identify junk-heavy items
- Measure cleanup effectiveness
- Drive future improvements using data instead of guesswork

RULES:
✅ No mutation
✅ No cleanup logic
✅ No rewriting
✅ Logging and metrics only
"""

from __future__ import annotations
from typing import Dict, Any, List
import re
import statistics


# ----------------------------
# Heuristics (Read-Only)
# ----------------------------

_JUNK_RX = re.compile(r"[^\w\s$.,&()/+'-]")
_MULTI_JUNK_RX = re.compile(r"[^\w\s]{2,}")
_ALLCAPS_RX = re.compile(r"^[^a-z]*$")

# ----------------------------
# Core metrics
# ----------------------------

def _tokenize(text: str) -> List[str]:
    return [t for t in re.split(r"\s+", text or "") if t.strip()]


def _junk_ratio(text: str) -> float:
    if not text:
        return 0.0
    junk = len(_JUNK_RX.findall(text))
    return junk / max(len(text), 1)


def _uppercase_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)


def _is_junk_heavy(text: str) -> bool:
    return _junk_ratio(text) > 0.25 or _MULTI_JUNK_RX.search(text) is not None


def _looks_all_caps(text: str) -> bool:
    return bool(_ALLCAPS_RX.match(text or ""))


# ----------------------------
# Item-level scoring
# ----------------------------

def score_item(item: Dict[str, Any]) -> Dict[str, Any]:
    name = item.get("name", "") or ""
    desc = item.get("description", "") or ""

    tokens = _tokenize(name + " " + desc)

    return {
        "id": item.get("id"),
        "token_count": len(tokens),
        "junk_ratio": round(_junk_ratio(name + " " + desc), 3),
        "uppercase_ratio": round(_uppercase_ratio(name + " " + desc), 3),
        "junk_heavy": _is_junk_heavy(name + " " + desc),
        "all_caps": _looks_all_caps(name),
        "name_len": len(_tokenize(name)),
        "desc_len": len(_tokenize(desc)),
        "has_description": bool(desc.strip()),
    }


# ----------------------------
# Draft-level summary
# ----------------------------

def summarize_items(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {"ok": True, "message": "No items"}

    scores = [score_item(it) for it in items]

    junk_items = [s for s in scores if s["junk_heavy"]]
    empty_desc = [s for s in scores if not s["has_description"]]
    all_caps   = [s for s in scores if s["all_caps"]]

    name_lengths = [s["name_len"] for s in scores if s["name_len"] > 0]
    junk_ratios  = [s["junk_ratio"] for s in scores]
    uc_ratios    = [s["uppercase_ratio"] for s in scores]

    def safe_avg(vals):
        return round(statistics.mean(vals), 3) if vals else 0.0

    return {
        "items_scanned": len(scores),
        "junk_heavy_count": len(junk_items),
        "all_caps_count": len(all_caps),
        "empty_description_count": len(empty_desc),
        "avg_name_len": safe_avg(name_lengths),
        "avg_junk_ratio": safe_avg(junk_ratios),
        "avg_uppercase_ratio": safe_avg(uc_ratios),
        "junk_items": junk_items[:10],   # preview only
        "flags": {
            "high_junk": len(junk_items) >= max(3, len(scores) * 0.2),
            "many_empty_desc": len(empty_desc) > len(scores) * 0.3,
            "ocr_casing_issue": len(all_caps) > len(scores) * 0.4,
        }
    }


# ----------------------------
# CLI / Debug Helper
# ----------------------------

def print_report(items: List[Dict[str, Any]]) -> None:
    report = summarize_items(items)

    print("\n[QUALITY REPORT]")
    for k, v in report.items():
        if k == "junk_items":
            print(f"- {k}: {len(v)} flagged (showing first 10)")
        elif isinstance(v, dict):
            continue
        else:
            print(f"- {k}: {v}")

    if "flags" in report:
        print("\n[FLAGS]")
        for k, v in report["flags"].items():
            print(f"- {k}: {'YES' if v else 'no'}")

