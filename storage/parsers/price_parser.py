"""
Price Parser — Day 20 (Phase A)
Extracts and normalizes price expressions from text lines.
"""

import re

PRICE_RE = re.compile(r"\$?\d{1,3}(?:\.\d{2})?")
DELTA_RE = re.compile(r"\+\$?\d+(?:\.\d{2})?")
RANGE_RE = re.compile(r"(\d{1,3}\.\d{2})\s*[-–]\s*(\d{1,3}\.\d{2})")
DEAL_RE = re.compile(r"\b\d+\s*for\s*\$?\d+(?:\.\d{2})?\b", re.I)
MARKET_RE = re.compile(r"\\b(MP|Market)\\b", re.I)

def parse_prices(text):
    """Return a list of price dicts with normalized structure."""
    prices = []
    for match in PRICE_RE.findall(text):
        try:
            prices.append({"value": float(match.replace('$', '')), "type": "base"})
        except ValueError:
            pass
    for match in DELTA_RE.findall(text):
        val = float(re.sub(r"[^0-9.]", "", match))
        prices.append({"value": val, "type": "delta"})
    for m in RANGE_RE.findall(text):
        prices.append({"value": None, "range": [float(m[0]), float(m[1])], "type": "range"})
    for m in DEAL_RE.findall(text):
        prices.append({"value": None, "text": m, "type": "deal"})
    if MARKET_RE.search(text):
        prices.append({"value": None, "text": "MP", "type": "market"})
    return prices
