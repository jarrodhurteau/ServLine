"""
Day 66: Sprint 8.4 -- Semantic Confidence Foundation

Tests the unified semantic confidence scoring in storage/semantic_confidence.py:
  1. Grammar signal (parse_confidence + Path B fallback)
  2. Name quality (length, garble, capitalization)
  3. Price presence signal
  4. Variant quality signal
  5. Flag penalty signal
  6. Weighted aggregation
  7. Path A vs Path B compatibility
  8. Edge cases

Run: python tests/test_day66_semantic_confidence.py
"""

import sys
import copy
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.semantic_confidence import (
    score_semantic_confidence,
    _score_grammar,
    _score_name_quality,
    _score_price_presence,
    _score_variant_quality,
    _score_flag_penalty,
    _is_name_garbled,
    _extract_name,
    _W_GRAMMAR,
    _W_NAME,
    _W_PRICE,
    _W_VARIANT,
    _W_FLAGS,
    _FLAG_PENALTY_WARN,
    _FLAG_PENALTY_INFO,
    _FLAG_PENALTY_AUTOFIX,
    _DEFAULT_GRAMMAR_SCORE,
    _DEFAULT_VARIANT_SCORE,
    _PRICE_PRESENT_SCORE,
    _PRICE_ABSENT_SCORE,
)


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

@dataclass
class TestReport:
    total: int = 0
    passed: int = 0
    failures: List[str] = field(default_factory=list)

    def ok(self, name: str) -> None:
        self.total += 1
        self.passed += 1
        print(f"  PASS  {name}")

    def fail(self, name: str, msg: str = "") -> None:
        self.total += 1
        self.failures.append(f"FAIL: {name} -- {msg}")
        print(f"  FAIL  {name}  ({msg})")

    def check(self, name: str, condition: bool, msg: str = "") -> None:
        if condition:
            self.ok(name)
        else:
            self.fail(name, msg)


def _make_item(
    text: str = "Test Item  10.99",
    grammar: Optional[Dict] = None,
    variants: Optional[List[Dict]] = None,
    category: Optional[str] = None,
    category_confidence: Optional[int] = None,
    meta: Optional[Dict] = None,
    price_flags: Optional[List[Dict]] = None,
    name: Optional[str] = None,
    confidence: Optional[float] = None,
    price_candidates: Optional[List[Dict]] = None,
    price_cents: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal item dict for testing.  Supports both Path A and B shapes."""
    item: Dict[str, Any] = {
        "merged_text": text,
        "bbox": [0, 0, 100, 20],
        "lines": [{"text": text, "bbox": [0, 0, 100, 20], "words": []}],
    }
    if grammar is not None:
        item["grammar"] = grammar
    if variants is not None:
        item["variants"] = variants
    if category is not None:
        item["category"] = category
    if category_confidence is not None:
        item["category_confidence"] = category_confidence
    if meta is not None:
        item["meta"] = meta
    if price_flags is not None:
        item["price_flags"] = price_flags
    if name is not None:
        item["name"] = name
    if confidence is not None:
        item["confidence"] = confidence
    if price_candidates is not None:
        item["price_candidates"] = price_candidates
    if price_cents is not None:
        item["price_cents"] = price_cents
    if description is not None:
        item["description"] = description
    return item


def _make_variant(
    label: str = "M",
    price_cents: int = 1099,
    kind: str = "size",
    confidence: float = 0.80,
) -> Dict[str, Any]:
    return {
        "label": label,
        "price_cents": price_cents,
        "kind": kind,
        "confidence": confidence,
    }


def _make_flag(severity: str = "warn", reason: str = "test_flag") -> Dict[str, Any]:
    return {"severity": severity, "reason": reason}


def _approx(a: float, b: float, tol: float = 0.001) -> bool:
    return abs(a - b) < tol


# ---------------------------------------------------------------------------
# Group 1: Grammar signal
# ---------------------------------------------------------------------------

def run_grammar_signal_tests(r: TestReport) -> None:
    print("\n--- Group 1: Grammar signal ---")

    # 1.1: High parse_confidence
    item = _make_item(grammar={"parsed_name": "Cheese Pizza", "parse_confidence": 0.95})
    r.check("1.1 high parse_confidence",
            _approx(_score_grammar(item), 0.95),
            f"got {_score_grammar(item)}")

    # 1.2: Low parse_confidence
    item = _make_item(grammar={"parsed_name": "X", "parse_confidence": 0.30})
    r.check("1.2 low parse_confidence",
            _approx(_score_grammar(item), 0.30),
            f"got {_score_grammar(item)}")

    # 1.3: No grammar, has item confidence (Path B fallback)
    item = _make_item(confidence=0.8)
    r.check("1.3 Path B fallback to item confidence",
            _approx(_score_grammar(item), 0.8),
            f"got {_score_grammar(item)}")

    # 1.4: No grammar, no confidence -> default 0.5
    item = _make_item()
    r.check("1.4 no signals -> default 0.5",
            _approx(_score_grammar(item), 0.5),
            f"got {_score_grammar(item)}")

    # 1.5: Grammar present but parse_confidence is None, item confidence exists
    item = _make_item(grammar={"parsed_name": "Test"}, confidence=0.75)
    r.check("1.5 grammar without parse_confidence falls back to item confidence",
            _approx(_score_grammar(item), 0.75),
            f"got {_score_grammar(item)}")

    # 1.6: Grammar with zero parse_confidence
    item = _make_item(grammar={"parsed_name": "X", "parse_confidence": 0.0})
    r.check("1.6 zero parse_confidence",
            _approx(_score_grammar(item), 0.0),
            f"got {_score_grammar(item)}")

    # 1.7: Grammar with confidence 1.0
    item = _make_item(grammar={"parsed_name": "Perfect", "parse_confidence": 1.0})
    r.check("1.7 perfect parse_confidence",
            _approx(_score_grammar(item), 1.0),
            f"got {_score_grammar(item)}")

    # 1.8: Empty grammar dict
    item = _make_item(grammar={})
    r.check("1.8 empty grammar dict -> default 0.5",
            _approx(_score_grammar(item), 0.5),
            f"got {_score_grammar(item)}")

    # 1.9: parse_confidence takes priority over item confidence
    item = _make_item(grammar={"parsed_name": "Test", "parse_confidence": 0.90},
                      confidence=0.60)
    r.check("1.9 parse_confidence wins over item confidence",
            _approx(_score_grammar(item), 0.90),
            f"got {_score_grammar(item)}")

    # 1.10: Item confidence = 0.0 (falsy but valid)
    item = _make_item(confidence=0.0)
    r.check("1.10 item confidence 0.0 used (not default)",
            _approx(_score_grammar(item), 0.0),
            f"got {_score_grammar(item)}")


# ---------------------------------------------------------------------------
# Group 2: Name quality signal
# ---------------------------------------------------------------------------

def run_name_quality_tests(r: TestReport) -> None:
    print("\n--- Group 2: Name quality ---")

    # 2.1: Short name (2 chars)
    item = _make_item(name="Ab")
    r.check("2.1 short name 2 chars -> 0.3",
            _approx(_score_name_quality(item), 0.3),
            f"got {_score_name_quality(item)}")

    # 2.2: Medium name (4 chars)
    item = _make_item(name="Taco")
    r.check("2.2 medium name 4 chars -> 0.6",
            _approx(_score_name_quality(item), 0.6),
            f"got {_score_name_quality(item)}")

    # 2.3: Long name (8+ chars)
    item = _make_item(name="Cheeseburger")
    r.check("2.3 long name 12 chars -> 1.0",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.4: Empty name -> 0.1
    item = _make_item(name="", text="")
    r.check("2.4 empty name -> 0.1",
            _approx(_score_name_quality(item), 0.1),
            f"got {_score_name_quality(item)}")

    # 2.5: Garbled name (triple repeat + high garble ratio)
    item = _make_item(name="ssseeecccc")
    r.check("2.5 garbled name -> 0.2",
            _approx(_score_name_quality(item), 0.2),
            f"got {_score_name_quality(item)}")

    # 2.6: All-caps real name
    item = _make_item(name="CHEESEBURGER")
    r.check("2.6 all-caps real name -> 0.9",
            _approx(_score_name_quality(item), 0.9),
            f"got {_score_name_quality(item)}")

    # 2.7: Mixed-case name -> 1.0
    item = _make_item(name="Cheese Pizza")
    r.check("2.7 mixed-case long name -> 1.0",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.8: Short name (3 chars exactly) -> medium tier 0.6
    item = _make_item(name="Sub")
    r.check("2.8 exactly 3 chars -> 0.6",
            _approx(_score_name_quality(item), 0.6),
            f"got {_score_name_quality(item)}")

    # 2.9: All-caps short name (2 chars) -> 0.3 (length dominates, no caps penalty for len<=2)
    item = _make_item(name="AB")
    r.check("2.9 all-caps 2-char -> 0.3 (length dominates)",
            _approx(_score_name_quality(item), 0.3),
            f"got {_score_name_quality(item)}")

    # 2.10: Name from grammar.parsed_name (Path A)
    item = _make_item(grammar={"parsed_name": "Buffalo Wings"})
    r.check("2.10 name from grammar.parsed_name",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.11: Name from 'name' field (Path B)
    item = _make_item(name="Margherita Pizza")
    r.check("2.11 name from name field",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.12: Name from merged_text fallback
    item = _make_item(text="Garden Salad  8.99")
    r.check("2.12 name from merged_text fallback",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.13: Exactly 6 chars -> 1.0 (long tier)
    item = _make_item(name="Nachos")
    r.check("2.13 exactly 6 chars -> 1.0",
            _approx(_score_name_quality(item), 1.0),
            f"got {_score_name_quality(item)}")

    # 2.14: All-caps medium name "TACO" -> min(0.6, 1.0, 0.9) = 0.6
    item = _make_item(name="TACO")
    r.check("2.14 all-caps medium name -> 0.6 (length wins)",
            _approx(_score_name_quality(item), 0.6),
            f"got {_score_name_quality(item)}")

    # 2.15: Real garble detection - not a false positive on real words
    r.check("2.15 CHEESEBURGER not garbled",
            not _is_name_garbled("CHEESEBURGER"),
            "false positive on real word")

    # 2.16: Real garble detection - catches actual garble
    r.check("2.16 ssseeecccc is garbled",
            _is_name_garbled("ssseeecccc"),
            "missed garble")

    # 2.17: Short garble string (3 alpha) -> not garbled (below threshold)
    r.check("2.17 short string 'sss' not garbled (too short)",
            not _is_name_garbled("sss"),
            "flagged too-short string as garble")


# ---------------------------------------------------------------------------
# Group 3: Price presence signal
# ---------------------------------------------------------------------------

def run_price_presence_tests(r: TestReport) -> None:
    print("\n--- Group 3: Price presence ---")

    # 3.1: Has variant with positive price_cents
    item = _make_item(variants=[_make_variant(price_cents=1099)])
    r.check("3.1 variant with price -> 1.0",
            _approx(_score_price_presence(item), 1.0),
            f"got {_score_price_presence(item)}")

    # 3.2: Has price_candidates with price_cents
    item = _make_item(price_candidates=[{"price_cents": 899}])
    r.check("3.2 price_candidates with price_cents -> 1.0",
            _approx(_score_price_presence(item), 1.0),
            f"got {_score_price_presence(item)}")

    # 3.3: Has price_candidates with value (float)
    item = _make_item(price_candidates=[{"value": 8.99}])
    r.check("3.3 price_candidates with value -> 1.0",
            _approx(_score_price_presence(item), 1.0),
            f"got {_score_price_presence(item)}")

    # 3.4: Has direct price_cents on item
    item = _make_item(price_cents=1299)
    r.check("3.4 direct price_cents -> 1.0",
            _approx(_score_price_presence(item), 1.0),
            f"got {_score_price_presence(item)}")

    # 3.5: No price at all
    item = _make_item()
    r.check("3.5 no price -> 0.3",
            _approx(_score_price_presence(item), 0.3),
            f"got {_score_price_presence(item)}")

    # 3.6: Variant with price_cents = 0
    item = _make_item(variants=[{"label": "S", "price_cents": 0}])
    r.check("3.6 variant price_cents=0 -> 0.3",
            _approx(_score_price_presence(item), 0.3),
            f"got {_score_price_presence(item)}")

    # 3.7: Empty variants list
    item = _make_item(variants=[])
    r.check("3.7 empty variants list -> 0.3",
            _approx(_score_price_presence(item), 0.3),
            f"got {_score_price_presence(item)}")

    # 3.8: Multiple price sources (first wins)
    item = _make_item(variants=[_make_variant(price_cents=1099)],
                      price_candidates=[{"price_cents": 899}])
    r.check("3.8 multiple sources -> 1.0",
            _approx(_score_price_presence(item), 1.0),
            f"got {_score_price_presence(item)}")

    # 3.9: price_candidates with zero value
    item = _make_item(price_candidates=[{"value": 0}])
    r.check("3.9 price_candidates value=0 -> 0.3",
            _approx(_score_price_presence(item), 0.3),
            f"got {_score_price_presence(item)}")

    # 3.10: None variants key
    item = _make_item()
    item["variants"] = None
    r.check("3.10 None variants -> 0.3",
            _approx(_score_price_presence(item), 0.3),
            f"got {_score_price_presence(item)}")


# ---------------------------------------------------------------------------
# Group 4: Variant quality signal
# ---------------------------------------------------------------------------

def run_variant_quality_tests(r: TestReport) -> None:
    print("\n--- Group 4: Variant quality ---")

    # 4.1: No variants -> 0.5 default
    item = _make_item()
    r.check("4.1 no variants -> 0.5",
            _approx(_score_variant_quality(item), 0.5),
            f"got {_score_variant_quality(item)}")

    # 4.2: Single variant confidence 0.9
    item = _make_item(variants=[_make_variant(confidence=0.9)])
    r.check("4.2 single variant 0.9 -> 0.9",
            _approx(_score_variant_quality(item), 0.9),
            f"got {_score_variant_quality(item)}")

    # 4.3: Multiple variants averaged
    item = _make_item(variants=[
        _make_variant(confidence=0.8),
        _make_variant(label="L", confidence=0.6),
    ])
    r.check("4.3 avg of 0.8 and 0.6 -> 0.7",
            _approx(_score_variant_quality(item), 0.7),
            f"got {_score_variant_quality(item)}")

    # 4.4: Variants missing confidence field -> defaults to 0.5
    item = _make_item(variants=[{"label": "S", "price_cents": 899}])
    r.check("4.4 missing confidence field -> 0.5",
            _approx(_score_variant_quality(item), 0.5),
            f"got {_score_variant_quality(item)}")

    # 4.5: Mix of high and low
    item = _make_item(variants=[
        _make_variant(confidence=1.0),
        _make_variant(label="L", confidence=0.0),
    ])
    r.check("4.5 mix 1.0 and 0.0 -> 0.5",
            _approx(_score_variant_quality(item), 0.5),
            f"got {_score_variant_quality(item)}")

    # 4.6: All confidence 1.0
    item = _make_item(variants=[
        _make_variant(confidence=1.0),
        _make_variant(label="L", confidence=1.0),
        _make_variant(label="XL", confidence=1.0),
    ])
    r.check("4.6 all 1.0 -> 1.0",
            _approx(_score_variant_quality(item), 1.0),
            f"got {_score_variant_quality(item)}")

    # 4.7: All confidence 0.0
    item = _make_item(variants=[
        _make_variant(confidence=0.0),
        _make_variant(label="L", confidence=0.0),
    ])
    r.check("4.7 all 0.0 -> 0.0",
            _approx(_score_variant_quality(item), 0.0),
            f"got {_score_variant_quality(item)}")

    # 4.8: Empty list
    item = _make_item(variants=[])
    r.check("4.8 empty list -> 0.5",
            _approx(_score_variant_quality(item), 0.5),
            f"got {_score_variant_quality(item)}")

    # 4.9: Three variants, average
    item = _make_item(variants=[
        _make_variant(confidence=0.9),
        _make_variant(label="L", confidence=0.6),
        _make_variant(label="XL", confidence=0.3),
    ])
    expected = (0.9 + 0.6 + 0.3) / 3
    r.check("4.9 three variants averaged",
            _approx(_score_variant_quality(item), expected),
            f"got {_score_variant_quality(item)}, expected {expected}")

    # 4.10: None variants
    item = _make_item()
    item["variants"] = None
    r.check("4.10 None variants -> 0.5",
            _approx(_score_variant_quality(item), 0.5),
            f"got {_score_variant_quality(item)}")


# ---------------------------------------------------------------------------
# Group 5: Flag penalty signal
# ---------------------------------------------------------------------------

def run_flag_penalty_tests(r: TestReport) -> None:
    print("\n--- Group 5: Flag penalty ---")

    # 5.1: No flags -> 1.0
    item = _make_item()
    r.check("5.1 no flags -> 1.0",
            _approx(_score_flag_penalty(item), 1.0),
            f"got {_score_flag_penalty(item)}")

    # 5.2: One warn flag
    item = _make_item(price_flags=[_make_flag("warn")])
    r.check("5.2 one warn -> 0.85",
            _approx(_score_flag_penalty(item), 0.85),
            f"got {_score_flag_penalty(item)}")

    # 5.3: One info flag
    item = _make_item(price_flags=[_make_flag("info")])
    r.check("5.3 one info -> 0.95",
            _approx(_score_flag_penalty(item), 0.95),
            f"got {_score_flag_penalty(item)}")

    # 5.4: One auto_fix flag
    item = _make_item(price_flags=[_make_flag("auto_fix")])
    r.check("5.4 one auto_fix -> 0.98",
            _approx(_score_flag_penalty(item), 0.98),
            f"got {_score_flag_penalty(item)}")

    # 5.5: Multiple warn flags
    item = _make_item(price_flags=[_make_flag("warn"), _make_flag("warn")])
    r.check("5.5 two warns -> 0.70",
            _approx(_score_flag_penalty(item), 0.70),
            f"got {_score_flag_penalty(item)}")

    # 5.6: Multiple info flags
    item = _make_item(price_flags=[_make_flag("info")] * 4)
    r.check("5.6 four infos -> 0.80",
            _approx(_score_flag_penalty(item), 0.80),
            f"got {_score_flag_penalty(item)}")

    # 5.7: Mixed severity
    item = _make_item(price_flags=[_make_flag("warn"), _make_flag("info"), _make_flag("auto_fix")])
    expected = 1.0 - 0.15 - 0.05 - 0.02
    r.check("5.7 mixed severity",
            _approx(_score_flag_penalty(item), expected),
            f"got {_score_flag_penalty(item)}, expected {expected}")

    # 5.8: Enough flags to hit 0.0 floor
    item = _make_item(price_flags=[_make_flag("warn")] * 7)
    r.check("5.8 seven warns -> floor at 0.0",
            _approx(_score_flag_penalty(item), 0.0),
            f"got {_score_flag_penalty(item)}")

    # 5.9: More than enough to exceed floor still 0.0
    item = _make_item(price_flags=[_make_flag("warn")] * 10)
    r.check("5.9 ten warns -> still 0.0",
            _approx(_score_flag_penalty(item), 0.0),
            f"got {_score_flag_penalty(item)}")

    # 5.10: Unknown severity treated as info
    item = _make_item(price_flags=[{"severity": "unknown_sev", "reason": "test"}])
    r.check("5.10 unknown severity -> info penalty 0.95",
            _approx(_score_flag_penalty(item), 0.95),
            f"got {_score_flag_penalty(item)}")

    # 5.11: Empty price_flags list
    item = _make_item(price_flags=[])
    r.check("5.11 empty list -> 1.0",
            _approx(_score_flag_penalty(item), 1.0),
            f"got {_score_flag_penalty(item)}")

    # 5.12: price_flags key missing entirely
    item = _make_item()
    assert "price_flags" not in item
    r.check("5.12 missing key -> 1.0",
            _approx(_score_flag_penalty(item), 1.0),
            f"got {_score_flag_penalty(item)}")


# ---------------------------------------------------------------------------
# Group 6: Weighted aggregation
# ---------------------------------------------------------------------------

def run_aggregation_tests(r: TestReport) -> None:
    print("\n--- Group 6: Weighted aggregation ---")

    # 6.1: Perfect item (all signals at 1.0)
    item = _make_item(
        name="Cheese Pizza Deluxe",
        grammar={"parsed_name": "Cheese Pizza Deluxe", "parse_confidence": 1.0},
        variants=[_make_variant(confidence=1.0, price_cents=1099)],
        price_candidates=[{"price_cents": 1099}],
    )
    score_semantic_confidence([item])
    r.check("6.1 perfect item -> 1.0",
            _approx(item["semantic_confidence"], 1.0),
            f"got {item['semantic_confidence']}")

    # 6.2: Worst case (minimum signals)
    item = _make_item(
        name="",
        text="",
        grammar={"parsed_name": "", "parse_confidence": 0.0},
        variants=[_make_variant(confidence=0.0, price_cents=0)],
        price_flags=[_make_flag("warn")] * 7,
    )
    score_semantic_confidence([item])
    # grammar=0*0.30 + name=0.1*0.20 + price=0.3*0.20 + variant=0*0.15 + flags=0*0.15
    expected = 0.0 * 0.30 + 0.1 * 0.20 + 0.3 * 0.20 + 0.0 * 0.15 + 0.0 * 0.15
    r.check("6.2 worst case",
            _approx(item["semantic_confidence"], expected),
            f"got {item['semantic_confidence']}, expected {round(expected, 4)}")

    # 6.3: Weights sum to 1.0
    weight_sum = _W_GRAMMAR + _W_NAME + _W_PRICE + _W_VARIANT + _W_FLAGS
    r.check("6.3 weights sum to 1.0",
            _approx(weight_sum, 1.0),
            f"sum = {weight_sum}")

    # 6.4: Grammar-dominant item
    item = _make_item(
        name="Margherita Pizza",
        grammar={"parsed_name": "Margherita Pizza", "parse_confidence": 1.0},
    )
    score_semantic_confidence([item])
    # grammar=1.0*0.30 + name=1.0*0.20 + price=0.3*0.20 + variant=0.5*0.15 + flags=1.0*0.15
    expected = 1.0*0.30 + 1.0*0.20 + 0.3*0.20 + 0.5*0.15 + 1.0*0.15
    r.check("6.4 grammar-dominant",
            _approx(item["semantic_confidence"], expected),
            f"got {item['semantic_confidence']}, expected {round(expected, 4)}")

    # 6.5: Flag-heavy penalty
    item = _make_item(
        name="Good Item Name",
        grammar={"parsed_name": "Good Item Name", "parse_confidence": 1.0},
        variants=[_make_variant(confidence=1.0, price_cents=1099)],
        price_flags=[_make_flag("warn")] * 7,
    )
    score_semantic_confidence([item])
    # grammar=1.0*0.30 + name=1.0*0.20 + price=1.0*0.20 + variant=1.0*0.15 + flags=0.0*0.15
    expected = 1.0*0.30 + 1.0*0.20 + 1.0*0.20 + 1.0*0.15 + 0.0*0.15
    r.check("6.5 flag-heavy penalty",
            _approx(item["semantic_confidence"], expected),
            f"got {item['semantic_confidence']}, expected {round(expected, 4)}")

    # 6.6: Details dict has all expected keys
    item = _make_item(name="Test Item Longname")
    score_semantic_confidence([item])
    details = item.get("semantic_confidence_details", {})
    expected_keys = {
        "grammar_score", "grammar_weight", "grammar_weighted",
        "name_quality_score", "name_quality_weight", "name_quality_weighted",
        "price_score", "price_weight", "price_weighted",
        "variant_score", "variant_weight", "variant_weighted",
        "flag_penalty_score", "flag_penalty_weight", "flag_penalty_weighted",
        "final",
    }
    r.check("6.6 details has all expected keys",
            expected_keys == set(details.keys()),
            f"missing: {expected_keys - set(details.keys())}, extra: {set(details.keys()) - expected_keys}")

    # 6.7: details.final matches semantic_confidence
    r.check("6.7 details.final matches semantic_confidence",
            item["semantic_confidence"] == details["final"],
            f"sc={item['semantic_confidence']}, final={details['final']}")

    # 6.8: Rounding to 4 decimal places
    item = _make_item(
        name="Burger",  # 6 chars -> name=1.0
        grammar={"parsed_name": "Burger", "parse_confidence": 0.777777},
    )
    score_semantic_confidence([item])
    details = item["semantic_confidence_details"]
    r.check("6.8 grammar_score rounded to 4 decimals",
            details["grammar_score"] == round(0.777777, 4),
            f"got {details['grammar_score']}")

    # 6.9: Score clamped to [0.0, 1.0]
    # Even with best signals, score shouldn't exceed 1.0
    item = _make_item(
        name="Perfect Name Long Enough",
        grammar={"parsed_name": "Perfect Name Long Enough", "parse_confidence": 1.0},
        variants=[_make_variant(confidence=1.0, price_cents=1099)],
    )
    score_semantic_confidence([item])
    r.check("6.9 clamped to max 1.0",
            item["semantic_confidence"] <= 1.0,
            f"got {item['semantic_confidence']}")

    # 6.10: Multiple items scored independently
    item_a = _make_item(name="Good Pizza Name",
                        grammar={"parsed_name": "Good Pizza Name", "parse_confidence": 0.95},
                        variants=[_make_variant(confidence=0.9, price_cents=1099)])
    item_b = _make_item(name="X",
                        grammar={"parsed_name": "X", "parse_confidence": 0.2})
    score_semantic_confidence([item_a, item_b])
    r.check("6.10 items scored independently",
            item_a["semantic_confidence"] > item_b["semantic_confidence"],
            f"a={item_a['semantic_confidence']}, b={item_b['semantic_confidence']}")

    # 6.11: Realistic menu item with variants
    item = _make_item(
        name="Pepperoni Pizza",
        grammar={"parsed_name": "Pepperoni Pizza", "parse_confidence": 0.88},
        variants=[
            _make_variant(label="S", price_cents=999, confidence=0.85),
            _make_variant(label="M", price_cents=1299, confidence=0.85),
            _make_variant(label="L", price_cents=1599, confidence=0.85),
        ],
        category="Pizza",
    )
    score_semantic_confidence([item])
    r.check("6.11 realistic pizza item scores high",
            item["semantic_confidence"] > 0.85,
            f"got {item['semantic_confidence']}")

    # 6.12: Realistic item with some flags
    item = _make_item(
        name="Mystery Special",
        grammar={"parsed_name": "Mystery Special", "parse_confidence": 0.60},
        variants=[_make_variant(confidence=0.5, price_cents=799)],
        price_flags=[_make_flag("info"), _make_flag("info")],
    )
    score_semantic_confidence([item])
    r.check("6.12 item with flags scores lower",
            0.4 < item["semantic_confidence"] < 0.85,
            f"got {item['semantic_confidence']}")


# ---------------------------------------------------------------------------
# Group 7: Path A vs Path B compatibility
# ---------------------------------------------------------------------------

def run_path_compatibility_tests(r: TestReport) -> None:
    print("\n--- Group 7: Path A vs Path B compatibility ---")

    # 7.1: Full Path A item
    item_a = _make_item(
        text="Cheese Pizza  10.99",
        grammar={"parsed_name": "Cheese Pizza", "parse_confidence": 0.92,
                 "confidence_tier": "high", "line_type": "menu_item"},
        variants=[_make_variant(price_cents=1099, confidence=0.88)],
        category="Pizza",
        category_confidence=85,
        meta={"has_size_variants": True},
    )
    score_semantic_confidence([item_a])
    r.check("7.1 Path A item scored",
            "semantic_confidence" in item_a and "semantic_confidence_details" in item_a,
            "missing fields")

    # 7.2: Full Path B item
    item_b: Dict[str, Any] = {
        "name": "Cheese Pizza",
        "description": "Mozzarella, tomato sauce",
        "category": "Pizza",
        "confidence": 0.85,
        "price_candidates": [{"type": "inline", "value": 10.99, "price_cents": 1099}],
        "variants": [_make_variant(price_cents=1099, confidence=0.88)],
    }
    score_semantic_confidence([item_b])
    r.check("7.2 Path B item scored",
            "semantic_confidence" in item_b and "semantic_confidence_details" in item_b,
            "missing fields")

    # 7.3: Mixed list
    item_a2 = _make_item(
        grammar={"parsed_name": "Wings", "parse_confidence": 0.80},
        variants=[_make_variant(price_cents=899, confidence=0.7)],
    )
    item_b2: Dict[str, Any] = {
        "name": "Soda",
        "category": "Beverages",
        "confidence": 0.90,
        "variants": [],
        "price_candidates": [{"value": 2.50}],
    }
    score_semantic_confidence([item_a2, item_b2])
    r.check("7.3 mixed list both scored",
            "semantic_confidence" in item_a2 and "semantic_confidence" in item_b2,
            "missing fields on one or both")

    # 7.4: Path B item with grammar added (hybrid)
    item_hybrid: Dict[str, Any] = {
        "name": "Calzone",
        "confidence": 0.60,
        "grammar": {"parsed_name": "Calzone", "parse_confidence": 0.90},
        "variants": [_make_variant(price_cents=1199, confidence=0.75)],
    }
    score_semantic_confidence([item_hybrid])
    details = item_hybrid["semantic_confidence_details"]
    r.check("7.4 hybrid: parse_confidence wins",
            _approx(details["grammar_score"], 0.90),
            f"grammar_score={details['grammar_score']}, expected 0.90")

    # 7.5: Path A item missing grammar
    item_no_grammar = _make_item(
        text="Garlic Bread  5.99",
        variants=[_make_variant(price_cents=599, confidence=0.8)],
    )
    score_semantic_confidence([item_no_grammar])
    details = item_no_grammar["semantic_confidence_details"]
    r.check("7.5 Path A without grammar uses default",
            _approx(details["grammar_score"], _DEFAULT_GRAMMAR_SCORE),
            f"got {details['grammar_score']}")

    # 7.6: Both parse_confidence and item confidence
    item = _make_item(
        grammar={"parsed_name": "Test Item", "parse_confidence": 0.95},
        confidence=0.60,
    )
    score_semantic_confidence([item])
    details = item["semantic_confidence_details"]
    r.check("7.6 parse_confidence takes priority",
            _approx(details["grammar_score"], 0.95),
            f"got {details['grammar_score']}")

    # 7.7: No mutation of upstream fields
    original_grammar = {"parsed_name": "Test", "parse_confidence": 0.80}
    original_variants = [_make_variant(confidence=0.75)]
    original_flags = [_make_flag("warn")]
    item = _make_item(
        grammar=copy.deepcopy(original_grammar),
        variants=copy.deepcopy(original_variants),
        price_flags=copy.deepcopy(original_flags),
    )
    score_semantic_confidence([item])
    r.check("7.7a grammar not mutated",
            item["grammar"] == original_grammar,
            "grammar was mutated")
    r.check("7.7b variants not mutated",
            item["variants"] == original_variants,
            "variants were mutated")
    r.check("7.7c price_flags not mutated",
            item["price_flags"] == original_flags,
            "price_flags were mutated")

    # 7.8: Path A realistic output
    item = _make_item(
        text="Buffalo Chicken Sub  Small 8.99  Large 12.99",
        grammar={"parsed_name": "Buffalo Chicken Sub", "parse_confidence": 0.85,
                 "confidence_tier": "high", "line_type": "menu_item",
                 "size_mentions": ["Small", "Large"], "modifiers": []},
        variants=[
            _make_variant(label="Small", price_cents=899, confidence=0.82),
            _make_variant(label="Large", price_cents=1299, confidence=0.82),
        ],
        category="Subs",
        category_confidence=72,
    )
    score_semantic_confidence([item])
    r.check("7.8 realistic Path A scores well",
            item["semantic_confidence"] > 0.80,
            f"got {item['semantic_confidence']}")


# ---------------------------------------------------------------------------
# Group 8: Edge cases
# ---------------------------------------------------------------------------

def run_edge_case_tests(r: TestReport) -> None:
    print("\n--- Group 8: Edge cases ---")

    # 8.1: Empty items list
    items: List[Dict] = []
    score_semantic_confidence(items)
    r.check("8.1 empty list no crash", True)

    # 8.2: Single item
    item = _make_item(name="Test Burger")
    score_semantic_confidence([item])
    r.check("8.2 single item scored",
            "semantic_confidence" in item,
            "missing semantic_confidence")

    # 8.3: Item with no fields at all (empty dict)
    item = {}
    score_semantic_confidence([item])
    r.check("8.3 empty dict scored without crash",
            "semantic_confidence" in item,
            "missing semantic_confidence")
    r.check("8.3b empty dict has valid score",
            0.0 <= item["semantic_confidence"] <= 1.0,
            f"got {item.get('semantic_confidence')}")

    # 8.4: Item with None variants
    item = _make_item(name="Test Item Long")
    item["variants"] = None
    score_semantic_confidence([item])
    r.check("8.4 None variants handled",
            "semantic_confidence" in item,
            "crashed or missing")

    # 8.5: Item with None grammar
    item = _make_item(name="Test Item Long")
    item["grammar"] = None
    score_semantic_confidence([item])
    r.check("8.5 None grammar handled",
            "semantic_confidence" in item,
            "crashed or missing")

    # 8.6: Function returns None
    item = _make_item(name="Test Item")
    result = score_semantic_confidence([item])
    r.check("8.6 returns None (mutates in place)",
            result is None,
            f"returned {result}")

    # 8.7: Idempotent (calling twice doesn't change score)
    item = _make_item(
        name="Pepperoni Pizza",
        grammar={"parsed_name": "Pepperoni Pizza", "parse_confidence": 0.88},
        variants=[_make_variant(confidence=0.85, price_cents=1099)],
    )
    score_semantic_confidence([item])
    first_score = item["semantic_confidence"]
    score_semantic_confidence([item])
    second_score = item["semantic_confidence"]
    r.check("8.7 idempotent",
            first_score == second_score,
            f"first={first_score}, second={second_score}")

    # 8.8: Large list (100 items)
    items = [_make_item(name=f"Item Number {i:03d}") for i in range(100)]
    score_semantic_confidence(items)
    all_scored = all("semantic_confidence" in it for it in items)
    r.check("8.8 100 items all scored",
            all_scored,
            "some items missing semantic_confidence")

    # 8.9: All signals at defaults
    item = _make_item()
    score_semantic_confidence([item])
    # grammar=0.5, name=from merged_text, price=0.3, variant=0.5, flags=1.0
    details = item["semantic_confidence_details"]
    r.check("8.9 default grammar score",
            _approx(details["grammar_score"], _DEFAULT_GRAMMAR_SCORE),
            f"got {details['grammar_score']}")
    r.check("8.9b default variant score",
            _approx(details["variant_score"], _DEFAULT_VARIANT_SCORE),
            f"got {details['variant_score']}")
    r.check("8.9c default flag score",
            _approx(details["flag_penalty_score"], 1.0),
            f"got {details['flag_penalty_score']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    report = TestReport()

    run_grammar_signal_tests(report)
    run_name_quality_tests(report)
    run_price_presence_tests(report)
    run_variant_quality_tests(report)
    run_flag_penalty_tests(report)
    run_aggregation_tests(report)
    run_path_compatibility_tests(report)
    run_edge_case_tests(report)

    print(f"\n{'=' * 60}")
    print(f"Day 66 Results: {report.passed}/{report.total} passed")

    if report.failures:
        print(f"\n{len(report.failures)} FAILURES:")
        for f in report.failures:
            print(f"  {f}")
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
