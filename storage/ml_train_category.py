# servline/storage/ml_train_category.py
"""
Category Trainer — Day 23 (Phase 3 pt.2)

Purpose
-------
Train a lightweight text classifier to label menu text blocks with canonical categories
(e.g., Pizzas, Burgers, Wings, Salads, etc.). The model complements the rule-based
heuristics and is fused at runtime in `ocr_pipeline.infer_categories_on_text_blocks`.

Outputs
-------
- Model bundle (vectorizer + classifier): storage/ml_models/category_clf.pkl
- Training report (metrics + config):    storage/ml_models/training_report.json
- Training dataset (CSV, optional):      storage/ml_training/menu_blocks.csv

Usage (Windows PowerShell examples)
-----------------------------------
# 1) If you already have a labeled CSV (text,label):
python -m storage.ml_train_category --input storage/ml_training/menu_blocks.csv --train

# 2) Build a weakly-labeled dataset by scanning PDFs and applying heuristics,
#    then train on that dataset:
python -m storage.ml_train_category --scan-dir fixtures/sample_menus --export-csv storage/ml_training/menu_blocks.csv --train

# 3) Quick default (tries CSV at storage/ml_training/menu_blocks.csv; if missing, scans fixtures):
python -m storage.ml_train_category --auto

Notes
-----
- Requires scikit-learn and joblib. (Install: pip install "scikit-learn>=1.3,<2" joblib)
- Uses TF-IDF (word bigrams) + LogisticRegression. Falls back to MultinomialNB if LR fails.
- Weak labeling uses the same heuristics defined here (kept in sync with ocr_pipeline).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Optional heavy imports guarded where used
import csv
import re
import random

# ----------------------------
# Canonical categories & heuristics (keep aligned with ocr_pipeline)
# ----------------------------

CANONICAL_CATEGORIES: Tuple[str, ...] = (
    "Pizzas",
    "Burgers",
    "Wings",
    "Subs",
    "Sandwiches",
    "Appetizers",
    "Sides",
    "Salads",
    "Pasta",
    "Calzones",
    "Desserts",
    "Beverages",
    "Kids",
    "Breakfast",
    "Specials",
    "Seafood",
)

_HEUR_PATTERNS: Dict[str, List[re.Pattern]] = {
    "Pizzas": [
        re.compile(r"\bpizza(s)?\b", re.I),
        re.compile(r"\bpies?\b", re.I),
        re.compile(r"\b(sicilian|neapolitan)\b", re.I),
    ],
    "Burgers": [
        re.compile(r"\bburger(s)?\b", re.I),
        re.compile(r"\bcheeseburger(s)?\b", re.I),
        re.compile(r"\bpatty melt\b", re.I),
    ],
    "Wings": [
        re.compile(r"\bwing(s)?\b", re.I),
        re.compile(r"\bboneless\b", re.I),
        re.compile(r"\b(buffalo|garlic[\-\s]?parm|inferno)\b", re.I),
    ],
    "Subs": [
        re.compile(r"\bsub(s)?\b", re.I),
        re.compile(r"\bhoagie(s)?\b", re.I),
        re.compile(r"\bgrinder(s)?\b", re.I),
    ],
    "Sandwiches": [
        re.compile(r"\bsandwich(es)?\b", re.I),
        re.compile(r"\bclub\b", re.I),
        re.compile(r"\bblt\b", re.I),
    ],
    "Appetizers": [
        re.compile(r"\bapp(etizer)?s?\b", re.I),
        re.compile(r"\bmozz(arella)? stick(s)?\b", re.I),
        re.compile(r"\bfries\b", re.I),
    ],
    "Sides": [
        re.compile(r"\bside(s)?\b", re.I),
        re.compile(r"\bcoleslaw\b", re.I),
        re.compile(r"\bmashed potatoes\b", re.I),
    ],
    "Salads": [
        re.compile(r"\bsalad(s)?\b", re.I),
        re.compile(r"\bcaesar\b", re.I),
        re.compile(r"\bgarden\b", re.I),
    ],
    "Pasta": [
        re.compile(r"\bpasta\b", re.I),
        re.compile(r"\bspaghetti|penne|fettuccine\b", re.I),
    ],
    "Calzones": [
        re.compile(r"\bcalzone(s)?\b", re.I),
        re.compile(r"\bstromboli\b", re.I),
    ],
    "Desserts": [
        re.compile(r"\bdessert(s)?\b", re.I),
        re.compile(r"\bcheesecake|tiramisu|cannoli\b", re.I),
    ],
    "Beverages": [
        re.compile(r"\b(beverage|drink)s?\b", re.I),
        re.compile(r"\bsoda|pop|cola|pepsi|coke|sprite|iced tea\b", re.I),
    ],
    "Kids": [
        re.compile(r"\bkids?\b", re.I),
        re.compile(r"\bchild(ren)?\b", re.I),
    ],
    "Breakfast": [
        re.compile(r"\bbreakfast\b", re.I),
        re.compile(r"\bpancake(s)?|omelet(te)?\b", re.I),
    ],
    "Specials": [
        re.compile(r"\bspecial(s)?\b", re.I),
        re.compile(r"\bdaily\b", re.I),
        re.compile(r"\bcombo(s)?\b", re.I),
    ],
    "Seafood": [
        re.compile(r"\bsea ?food\b", re.I),
        re.compile(r"\bshrimp|scallop(s)?|tilapia|salmon|cod\b", re.I),
    ],
}

_HEADING_HINT = re.compile(r"^[A-Z][A-Z\s&/0-9\-]{2,}$")

def rule_label(text: str) -> Optional[str]:
    if not text:
        return None
    t = text.strip()
    scores: Dict[str, float] = {}
    for label, pats in _HEUR_PATTERNS.items():
        s = 0.0
        for p in pats:
            if p.search(t):
                s += 1.0
        if s > 0:
            scores[label] = s
    if not scores:
        return None
    mx = max(scores.values())
    best = [k for k, v in scores.items() if v == mx]
    if len(best) == 1:
        return best[0]
    # tie: prefer heading-ish and canonical order
    heading_like = bool(_HEADING_HINT.match(t)) or (len(t) <= 28 and t.isupper())
    if heading_like:
        # If multiple matches, just fall through to canonical tie-break
        pass
    for c in CANONICAL_CATEGORIES:
        if c in best:
            return c
    return best[0]


# ----------------------------
# IO helpers
# ----------------------------

def _ensure_dirs():
    Path("storage/ml_models").mkdir(parents=True, exist_ok=True)
    Path("storage/ml_training").mkdir(parents=True, exist_ok=True)

def _read_csv(path: Path) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            text = (r.get("text") or "").strip()
            label = (r.get("label") or "").strip()
            if text and label:
                rows.append((text, label))
    return rows

def _write_csv(path: Path, rows: List[Tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["text", "label"])
        for t, y in rows:
            w.writerow([t, y])

def _shuffle(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    r = rows[:]
    random.Random(17).shuffle(r)
    return r


# ----------------------------
# Dataset building (weak labels)
# ----------------------------

def build_dataset_from_pdfs(scan_dir: Path, limit: Optional[int] = None) -> List[Tuple[str, str]]:
    """
    Run the segmentation pipeline over PDFs/images, weak-label headings with rules,
    and return (text,label) pairs. This is *not* perfect but gives the model a start.
    """
    try:
        from . import ocr_pipeline  # local import to avoid heavy deps at module import
    except Exception as e:
        print(f"[ERROR] Could not import storage.ocr_pipeline: {e}", file=sys.stderr)
        return []

    pairs: List[Tuple[str, str]] = []
    count = 0

    exts = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    for p in sorted(scan_dir.rglob("*")):
        if limit and count >= limit:
            break
        if p.suffix.lower() not in exts:
            continue
        try:
            seg = ocr_pipeline.segment_document(pdf_path=str(p))
            tblocks = seg.get("text_blocks", []) or []
            for tb in tblocks:
                text = (tb.get("merged_text") or "").strip()
                if not text:
                    continue
                # Only label block-like headings or obvious category blocks
                is_heading = (tb.get("block_type") or "").lower() in {"heading", "section", "title"} \
                             or bool(_HEADING_HINT.match(text))
                if not is_heading and len(text) > 48:
                    continue
                lbl = rule_label(text)
                if lbl:
                    pairs.append((text, lbl))
            count += 1
            print(f"[SCAN] {p.name}: +{len(pairs)} total examples")
        except Exception as e:
            print(f"[WARN] Skipping {p}: {e}")
            continue
    return pairs


# ----------------------------
# Training
# ----------------------------

@dataclass
class TrainConfig:
    max_features: int = 30000
    ngram_min: int = 1
    ngram_max: int = 2
    test_size: float = 0.2
    random_state: int = 17

def train_and_save(rows: List[Tuple[str, str]], cfg: TrainConfig) -> Dict[str, object]:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import MultinomialNB
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import classification_report, accuracy_score
    from joblib import dump

    texts = [t for t, _ in rows]
    labels = [y for _, y in rows]

    X_train, X_test, y_train, y_test = train_test_split(
        texts, labels, test_size=cfg.test_size, random_state=cfg.random_state, stratify=labels
    )

    vec = TfidfVectorizer(
        lowercase=True,
        analyzer="word",
        ngram_range=(cfg.ngram_min, cfg.ngram_max),
        max_features=cfg.max_features,
        token_pattern=r"(?u)\b[\w&'/-]{2,}\b",
    )
    Xtr = vec.fit_transform(X_train)
    Xte = vec.transform(X_test)

    clf = None
    used = "LogisticRegression"
    try:
        clf = LogisticRegression(
            max_iter=300,
            solver="lbfgs",
            multi_class="auto",
            n_jobs=None,
        )
        clf.fit(Xtr, y_train)
    except Exception as e:
        print(f"[WARN] LogisticRegression failed ({e}); falling back to MultinomialNB.")
        used = "MultinomialNB"
        clf = MultinomialNB()
        clf.fit(Xtr, y_train)

    y_pred = clf.predict(Xte)
    acc = float(accuracy_score(y_test, y_pred))
    report = classification_report(y_test, y_pred, zero_division=0, output_dict=True)

    # Save model bundle
    bundle = {
        "vectorizer": vec,
        "clf": clf,
        "labels": sorted(list(set(labels))),
        "algo": used,
        "created_at": int(time.time()),
        "config": cfg.__dict__,
    }
    out_path = Path("storage/ml_models/category_clf.pkl")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # joblib dump
    dump(bundle, out_path)

    # Save metrics
    report_path = Path("storage/ml_models/training_report.json")
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "algo": used,
                "accuracy": acc,
                "report": report,
                "labels": bundle["labels"],
                "created_at": bundle["created_at"],
                "config": cfg.__dict__,
            },
            f,
            indent=2,
        )

    print(f"[OK] Saved model → {out_path}")
    print(f"[OK] Saved report → {report_path}")
    print(f"[METRICS] accuracy={acc:.3f}")
    return {"accuracy": acc, "algo": used, "labels": bundle["labels"]}


# ----------------------------
# CLI
# ----------------------------

def main(argv: Optional[List[str]] = None) -> int:
    _ensure_dirs()

    ap = argparse.ArgumentParser(description="Train menu category classifier.")
    ap.add_argument("--input", type=str, help="CSV with columns: text,label")
    ap.add_argument("--scan-dir", type=str, help="Directory of PDFs/Images to weak-label and export CSV")
    ap.add_argument("--export-csv", type=str, help="Where to write the built CSV (default: storage/ml_training/menu_blocks.csv)")
    ap.add_argument("--limit", type=int, default=None, help="Limit number of files scanned")
    ap.add_argument("--train", action="store_true", help="Train after loading/building dataset")
    ap.add_argument("--auto", action="store_true", help="Try input CSV; else scan fixtures/sample_menus; then train")
    args = ap.parse_args(argv)

    csv_path = Path(args.export_csv or "storage/ml_training/menu_blocks.csv")

    rows: List[Tuple[str, str]] = []

    if args.auto:
        if args.input and Path(args.input).exists():
            rows = _read_csv(Path(args.input))
            print(f"[AUTO] Loaded {len(rows)} rows from {args.input}")
        elif csv_path.exists():
            rows = _read_csv(csv_path)
            print(f"[AUTO] Loaded {len(rows)} rows from {csv_path}")
        else:
            scan_dir = Path(args.scan_dir or "fixtures/sample_menus")
            print(f"[AUTO] No CSV found; scanning {scan_dir} to build weak labels…")
            rows = build_dataset_from_pdfs(scan_dir, limit=args.limit)
            if rows:
                _write_csv(csv_path, rows)
                print(f"[AUTO] Wrote dataset CSV → {csv_path} ({len(rows)} rows)")
        if not rows:
            print("[AUTO] No data available; nothing to train.", file=sys.stderr)
            return 1
        args.train = True

    else:
        if args.input:
            p = Path(args.input)
            if not p.exists():
                print(f"[ERR] Input CSV not found: {p}", file=sys.stderr)
                return 1
            rows = _read_csv(p)
            print(f"[LOAD] {len(rows)} rows from {p}")

        if args.scan_dir:
            scan_dir = Path(args.scan_dir)
            if not scan_dir.exists():
                print(f"[ERR] Scan dir not found: {scan_dir}", file=sys.stderr)
                return 1
            built = build_dataset_from_pdfs(scan_dir, limit=args.limit)
            print(f"[BUILD] Built {len(built)} weak-labeled rows from {scan_dir}")
            rows = rows + built
            if args.export_csv:
                _write_csv(csv_path, rows)
                print(f"[SAVE] Wrote dataset CSV → {csv_path} ({len(rows)} rows)")

        if not rows and not args.train:
            print("[INFO] No rows loaded yet. Provide --input or --scan-dir, or use --auto.", file=sys.stderr)
            return 1

    # Train if requested
    if args.train:
        if not rows:
            if csv_path.exists():
                rows = _read_csv(csv_path)
                print(f"[TRAIN] Loaded {len(rows)} rows from {csv_path}")
            else:
                print("[ERR] No data to train on. Provide --input or run --scan-dir/--auto first.", file=sys.stderr)
                return 1

        # Deduplicate and shuffle a bit
        rows = list({(t.strip(), y): None for t, y in rows if t.strip() and y}.keys())
        rows = _shuffle(rows)

        # Filter to canonical labels only
        rows = [(t, y) for t, y in rows if y in CANONICAL_CATEGORIES]
        if len(rows) < 20:
            print(f"[ERR] Insufficient examples after filtering ({len(rows)}).", file=sys.stderr)
            return 1

        cfg = TrainConfig()
        result = train_and_save(rows, cfg)
        print(f"[DONE] Trained {result['algo']} with {len(rows)} total rows. Accuracy≈{result['accuracy']:.3f}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
