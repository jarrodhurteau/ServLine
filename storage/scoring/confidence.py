"""
Confidence Scoring — Day 20 (Phase A)
Combines heuristic indicators into confidence scores.
"""

def score_confidence(draft):
    """
    Very basic scoring blend: adjust confidence
    based on presence of price, variant, and category.
    """
    for item in draft.get("items", []):
        score = 0.4
        if item.get("price_candidates"):
            score += 0.2
        if item.get("variants"):
            score += 0.1
        if item.get("category"):
            score += 0.2
        item["confidence"] = min(round(score, 2), 1.0)
    return draft
