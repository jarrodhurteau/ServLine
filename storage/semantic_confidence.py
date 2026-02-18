# storage/semantic_confidence.py
"""
Semantic Confidence Scoring -- Sprint 8.4 Day 66

Computes a unified per-item semantic_confidence score (0.0-1.0) by
aggregating five independent signal sources:

  1. Grammar/parse confidence (or fallback item confidence for Path B)
  2. Name quality (length, garble detection, capitalization)
  3. Price presence (has at least one price)
  4. Variant quality (average variant confidence)
  5. Flag penalty (severity-weighted deductions from price_flags)

Polymorphic: works with both Path A (text_block dicts from ocr_pipeline)
and Path B (flat item dicts from ai_ocr_helper).

Entry function: score_semantic_confidence(items)
Mutates items in place by adding semantic_confidence and
semantic_confidence_details.

Pipeline placement: Step 9.2, after check_cross_item_consistency (Step 9.1).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Signal weights (must sum to 1.0)
_W_GRAMMAR = 0.30
_W_NAME = 0.20
_W_PRICE = 0.20
_W_VARIANT = 0.15
_W_FLAGS = 0.15

# Flag penalty values per severity
_FLAG_PENALTY_WARN = 0.15
_FLAG_PENALTY_INFO = 0.05
_FLAG_PENALTY_AUTOFIX = 0.02

# Name quality thresholds
_NAME_SHORT_THRESHOLD = 3   # names < 3 chars get 0.3
_NAME_MEDIUM_THRESHOLD = 6  # names 3-5 chars get 0.6, 6+ get 1.0

# Price presence scores
_PRICE_PRESENT_SCORE = 1.0
_PRICE_ABSENT_SCORE = 0.3

# Neutral defaults when signal is unavailable
_DEFAULT_VARIANT_SCORE = 0.5
_DEFAULT_GRAMMAR_SCORE = 0.5

# Garble detection constants (mirrors menu_grammar._is_garble_run pattern)
_GARBLE_CHARS = set("secrnotvw")
_TRIPLE_REPEAT_RE = re.compile(r"(.)\1{2,}", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_name(item: Dict[str, Any]) -> str:
    """Extract item name from either Path A or Path B item dict."""
    grammar = item.get("grammar") or {}
    parsed = grammar.get("parsed_name")
    if parsed and parsed.strip():
        return parsed.strip()
    name = item.get("name")
    if name and name.strip():
        return name.strip()
    merged = item.get("merged_text") or ""
    # Strip price tokens for cleaner name
    merged = re.sub(r"\$?\d+\.\d{2}", "", merged)
    return merged.strip()


def _score_grammar(item: Dict[str, Any]) -> float:
    """Read grammar parse_confidence or fall back to item confidence."""
    grammar = item.get("grammar") or {}
    pc = grammar.get("parse_confidence")
    if pc is not None:
        return float(pc)
    # Path B fallback: item-level confidence
    conf = item.get("confidence")
    if conf is not None:
        return float(conf)
    return _DEFAULT_GRAMMAR_SCORE


def _is_name_garbled(name: str) -> bool:
    """Check if a name looks like OCR garble (not a real menu item name)."""
    alpha = [c for c in name if c.isalpha()]
    if len(alpha) < 4:
        return False
    garble_ratio = sum(1 for c in alpha if c.lower() in _GARBLE_CHARS) / len(alpha)
    unique_ratio = len(set(c.lower() for c in alpha)) / len(alpha)
    has_triple = bool(_TRIPLE_REPEAT_RE.search(name))
    signals = sum([
        has_triple,
        garble_ratio >= 0.60,
        unique_ratio <= 0.40,
    ])
    return signals >= 2


def _score_name_quality(item: Dict[str, Any]) -> float:
    """Score name quality based on length, garble, and capitalization."""
    name = _extract_name(item)
    if not name:
        return 0.1  # No name at all

    # Signal 1: Length
    if len(name) < _NAME_SHORT_THRESHOLD:
        length_score = 0.3
    elif len(name) < _NAME_MEDIUM_THRESHOLD:
        length_score = 0.6
    else:
        length_score = 1.0

    # Signal 2: Garble check
    if _is_name_garbled(name):
        garble_score = 0.2
    else:
        garble_score = 1.0

    # Signal 3: All-caps penalty (small ding -- OCR often produces all-caps)
    if name == name.upper() and len(name) > 2:
        caps_score = 0.9
    else:
        caps_score = 1.0

    # Combine as minimum of signals (weakest link)
    return min(length_score, garble_score, caps_score)


def _score_price_presence(item: Dict[str, Any]) -> float:
    """Score 1.0 if item has at least one positive price, 0.3 otherwise."""
    # Check variants for prices
    variants = item.get("variants") or []
    for v in variants:
        pc = v.get("price_cents", 0)
        if isinstance(pc, (int, float)) and pc > 0:
            return _PRICE_PRESENT_SCORE

    # Check price_candidates
    candidates = item.get("price_candidates") or []
    for cand in candidates:
        cents = cand.get("price_cents", 0)
        if isinstance(cents, (int, float)) and cents > 0:
            return _PRICE_PRESENT_SCORE
        val = cand.get("value", 0)
        if isinstance(val, (int, float)) and val > 0:
            return _PRICE_PRESENT_SCORE

    # Check direct price_cents on item
    direct = item.get("price_cents", 0)
    if isinstance(direct, (int, float)) and direct > 0:
        return _PRICE_PRESENT_SCORE

    return _PRICE_ABSENT_SCORE


def _score_variant_quality(item: Dict[str, Any]) -> float:
    """Average variant confidence; 0.5 default if no variants."""
    variants = item.get("variants") or []
    if not variants:
        return _DEFAULT_VARIANT_SCORE
    confs = [float(v.get("confidence", 0.5)) for v in variants]
    return sum(confs) / len(confs)


def _score_flag_penalty(item: Dict[str, Any]) -> float:
    """1.0 minus severity-weighted penalties from price_flags, capped at 0.0."""
    flags = item.get("price_flags") or []
    if not flags:
        return 1.0
    total_penalty = 0.0
    for flag in flags:
        severity = flag.get("severity", "info")
        if severity == "warn":
            total_penalty += _FLAG_PENALTY_WARN
        elif severity == "info":
            total_penalty += _FLAG_PENALTY_INFO
        elif severity == "auto_fix":
            total_penalty += _FLAG_PENALTY_AUTOFIX
        else:
            # Unknown severity — treat as info
            total_penalty += _FLAG_PENALTY_INFO
    return max(0.0, 1.0 - total_penalty)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score_semantic_confidence(items: list) -> None:
    """Compute unified semantic_confidence for each item.

    Reads 5 signal sources (all read-only on upstream data),
    computes weighted aggregation, writes two fields per item:
      - semantic_confidence: float 0.0-1.0
      - semantic_confidence_details: dict with signal audit trail

    Pipeline placement: Step 9.2, after check_cross_item_consistency (9.1).
    Mutates items in place.
    """
    for item in items:
        grammar_raw = _score_grammar(item)
        name_raw = _score_name_quality(item)
        price_raw = _score_price_presence(item)
        variant_raw = _score_variant_quality(item)
        flag_raw = _score_flag_penalty(item)

        weighted_grammar = grammar_raw * _W_GRAMMAR
        weighted_name = name_raw * _W_NAME
        weighted_price = price_raw * _W_PRICE
        weighted_variant = variant_raw * _W_VARIANT
        weighted_flags = flag_raw * _W_FLAGS

        raw_score = (weighted_grammar + weighted_name + weighted_price
                     + weighted_variant + weighted_flags)
        final = max(0.0, min(1.0, round(raw_score, 4)))

        item["semantic_confidence"] = final
        item["semantic_confidence_details"] = {
            "grammar_score": round(grammar_raw, 4),
            "grammar_weight": _W_GRAMMAR,
            "grammar_weighted": round(weighted_grammar, 4),
            "name_quality_score": round(name_raw, 4),
            "name_quality_weight": _W_NAME,
            "name_quality_weighted": round(weighted_name, 4),
            "price_score": round(price_raw, 4),
            "price_weight": _W_PRICE,
            "price_weighted": round(weighted_price, 4),
            "variant_score": round(variant_raw, 4),
            "variant_weight": _W_VARIANT,
            "variant_weighted": round(weighted_variant, 4),
            "flag_penalty_score": round(flag_raw, 4),
            "flag_penalty_weight": _W_FLAGS,
            "flag_penalty_weighted": round(weighted_flags, 4),
            "final": final,
        }
