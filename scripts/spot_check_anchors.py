"""Anchor-fidelity spot-check for the two-phase pricing pipeline.

After a pricing run completes, this script answers:
  1. Two-pool metric — total_data_points vs total cited sources.
     A healthy gap means platforms ARE widening the data set.
  2. Anchor fidelity — for each cited source restaurant, is it in
     the Phase 1 anchor list? Cites from outside the anchor list
     mean Gemini fell back to open-web discovery instead of using
     the targeted-search anchors. That's the silent failure mode
     Gemini flagged in the v4 critique.
  3. Per-item breakdown of off-anchor cites so we can investigate
     specific cases.

Usage:
    python scripts/spot_check_anchors.py [draft_id]

Defaults to draft 310 (the test draft we've been iterating on).
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def normalize_name(name: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace.
    Anchor list and cited names use slightly different formats
    ("Joe's Pizza" vs "Joe's Pizza Restaurant"), so we compare on
    a canonical form."""
    if not name:
        return ""
    n = name.lower().strip()
    n = re.sub(r"[^\w\s]", " ", n)  # punctuation -> space
    n = re.sub(r"\s+", " ", n)      # collapse whitespace
    # Strip generic suffix words that often differ between Places +
    # the restaurant's own branding
    for suffix in (" restaurant", " pizzeria", " pizza", " grill",
                   " kitchen", " house", " co", " llc", " inc"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


def name_match(cited: str, anchor_names: set, anchor_normalized: set) -> bool:
    """Treat as match if exact (case-insensitive) OR normalized form
    is in the anchor set OR there's a substring match in either
    direction (handles 'Joe's' vs 'Joe's Pizza Co' divergence)."""
    if not cited:
        return False
    if cited.lower().strip() in anchor_names:
        return True
    cn = normalize_name(cited)
    if not cn:
        return False
    if cn in anchor_normalized:
        return True
    # Substring tolerance for divergent suffixes/prefixes
    for an in anchor_normalized:
        if not an:
            continue
        if cn in an or an in cn:
            # Avoid trivial substring matches like "the" matching "athena"
            if min(len(cn), len(an)) >= 4:
                return True
    return False


def main():
    draft_id = int(sys.argv[1]) if len(sys.argv) > 1 else 310

    db_path = REPO / "storage" / "servline.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Restaurant id for this draft
    drow = conn.execute(
        "SELECT restaurant_id FROM drafts WHERE id = ?", (draft_id,),
    ).fetchone()
    if not drow:
        print(f"draft {draft_id} not found")
        return
    rid = drow["restaurant_id"]

    # Anchor list — the names we passed into the prompt
    anchors = conn.execute(
        "SELECT place_name, place_address FROM price_comparison_results "
        "WHERE restaurant_id = ?", (rid,),
    ).fetchall()
    anchor_names = {(a["place_name"] or "").lower().strip() for a in anchors}
    anchor_normalized = {normalize_name(a["place_name"]) for a in anchors}
    anchor_normalized.discard("")
    print(f"\nDraft {draft_id} (restaurant_id={rid})")
    print(f"Anchor list size: {len(anchors)}")
    if anchors:
        print(f"Sample anchors: {[a['place_name'] for a in anchors[:5]]}")

    # Pricing results
    rows = conn.execute(
        "SELECT item_id, item_name, price_sources "
        "FROM price_intelligence_results WHERE draft_id = ? "
        "AND price_sources IS NOT NULL", (draft_id,),
    ).fetchall()

    total_items = 0
    items_with_cites = 0
    items_with_data_points = 0
    total_dp = 0
    total_cites = 0
    on_anchor = 0
    off_anchor = 0
    off_anchor_examples = []  # (item_name, cited_restaurant)

    for row in rows:
        total_items += 1
        ps = row["price_sources"]
        if isinstance(ps, str):
            try:
                ps = json.loads(ps)
            except Exception:
                continue
        if not isinstance(ps, list):
            continue

        item_dp = 0
        item_cites = 0
        for src in ps:
            if not isinstance(src, dict):
                continue
            # Base entry
            item_dp += int(src.get("total_data_points") or 0)
            for s in (src.get("sources") or []):
                if not isinstance(s, dict):
                    continue
                rest = s.get("restaurant") or ""
                item_cites += 1
                if name_match(rest, anchor_names, anchor_normalized):
                    on_anchor += 1
                else:
                    off_anchor += 1
                    off_anchor_examples.append((row["item_name"], rest))
            # Per-size
            for sz in (src.get("sizes") or {}).values():
                if not isinstance(sz, dict):
                    continue
                item_dp += int(sz.get("total_data_points") or 0)
                for s in (sz.get("sources") or []):
                    if not isinstance(s, dict):
                        continue
                    rest = s.get("restaurant") or ""
                    item_cites += 1
                    if name_match(rest, anchor_names, anchor_normalized):
                        on_anchor += 1
                    else:
                        off_anchor += 1
                        off_anchor_examples.append((row["item_name"], rest))

        total_dp += item_dp
        total_cites += item_cites
        if item_cites > 0:
            items_with_cites += 1
        if item_dp > 0:
            items_with_data_points += 1

    print(f"\n{'='*60}")
    print(f"TWO-POOL METRIC")
    print(f"{'='*60}")
    print(f"Items with any data points (range pool):  {items_with_data_points}/{total_items}")
    print(f"Items with cited sources (direct sites):  {items_with_cites}/{total_items}")
    print(f"Total data points (range pool):           {total_dp}")
    print(f"Total cited sources (direct sites):       {total_cites}")
    gap = total_dp - total_cites
    print(f"Gap (platforms widening data):            {gap}")
    if total_dp == 0:
        print("WARNING: no total_data_points reported. Either Gemini")
        print("isn't returning the field, or the parser didn't pick it up.")
    elif gap == 0:
        print("WARNING: gap is zero. Platforms aren't contributing —")
        print("the two-pool architecture isn't earning its complexity.")
    elif gap > 0 and total_cites > 0:
        ratio = total_dp / total_cites
        print(f"Multiplier:                               {ratio:.1f}x")
        print(f"(every cited source has {ratio - 1:.1f}x more uncited platform data backing it)")

    print(f"\n{'='*60}")
    print(f"ANCHOR FIDELITY")
    print(f"{'='*60}")
    print(f"Cited sources IN anchor list:    {on_anchor}")
    print(f"Cited sources NOT in anchor list: {off_anchor}")
    if total_cites > 0:
        pct_on = 100 * on_anchor / total_cites
        print(f"On-anchor rate:                   {pct_on:.1f}%")
        if pct_on < 50:
            print("WARNING: less than half of cited sources are from anchored")
            print("restaurants. Phase 2 is falling back to open-web discovery —")
            print("the targeted-search step isn't doing its job.")
        elif pct_on < 80:
            print("MODERATE: most cites are on-anchor but a meaningful chunk are")
            print("from open-web discovery. Could mean some anchors had no")
            print("usable menus and Gemini correctly broadened — review the")
            print("off-anchor list below to judge.")
        else:
            print("HEALTHY: the targeted-search step is working as designed.")

    if off_anchor_examples:
        print(f"\nOff-anchor cites (first 20):")
        seen = set()
        shown = 0
        for item_name, rest in off_anchor_examples:
            key = (item_name, rest)
            if key in seen:
                continue
            seen.add(key)
            print(f"  - {item_name}: {rest}")
            shown += 1
            if shown >= 20:
                break
        if len(seen) > shown:
            print(f"  ...and {len(seen) - shown} more unique pairs")


if __name__ == "__main__":
    main()
