"""
DDI Corpus Processor — Medication Orchestra Phase 3

Transforms the raw DrugBank DDI CSV (191,543 rows) into:
  1. backend/data/ddi_processed.json  — enriched, deduplicated interaction records
  2. backend/data/ddi_index.jsonl     — JSONL for Vertex AI Vector Search batch indexing

Run from project root:
  python backend/data/prepare_corpus.py

Input: db_drug_interactions.csv  (columns: Drug 1, Drug 2, Interaction Description)
"""

import csv
import json
import os
import re
import sys
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CSV_PATH     = PROJECT_ROOT / "db_drug_interactions.csv"
OUT_DIR      = Path(__file__).resolve().parent          # backend/data/
PROCESSED_PATH = OUT_DIR / "ddi_processed.json"
JSONL_PATH     = OUT_DIR / "ddi_index.jsonl"

MAX_RECORDS = 50_000   # cap to keep index manageable
CHUNK_SIZE  = 10_000   # rows processed per iteration

# ── Severity classifier ────────────────────────────────────────────────────────

# Patterns derived from real DrugBank phrase frequency audit across 191K rows.
# Each category is ordered: most specific patterns first.
# 'major' captures the clinically dangerous interactions that require action.
# 'moderate' captures interactions that need monitoring.
# 'minor' is the default for everything else.

# MAJOR: Life-threatening or clinically significant interactions
MAJOR_PHRASES = {
    # Cardiac
    r"\bmay increase the QTc\b",             # 6140 rows — QT prolongation = fatal arrhythmia
    r"\bmay increase the arrhythmogenic\b",  # 350 rows
    r"\bmay increase the atrioventricular\b", # 716 rows — AV block
    r"\bmay increase the bradycardic\b",     # 1277 rows
    r"\bmay increase the tachycardic\b",     # 372 rows
    r"\bmay increase the cardiotoxic\b",     # 202 rows
    # Bleeding / coagulation
    r"\bmay increase the anticoagulant\b",   # 3155 rows
    r"\bmay increase the antiplatelet\b",    # 336 rows
    r"\bmay decrease the anticoagulant\b",   # 238 rows — reduced clot protection
    r"\bmay increase the thrombogenic\b",    # 108 rows
    # Neurological / CNS
    r"\bmay increase the central\b",         # 5453 rows — CNS depression
    r"\bmay increase the serotonergic\b",    # 803 rows — serotonin syndrome
    r"\bmay increase the neuroexcitatory\b", # 936 rows
    r"\bmay increase the neuromuscular\b",   # 409 rows
    r"\bmay increase the sedative\b",        # 1011 rows (major when combined)
    r"\bmay increase the respiratory\b",     # 280 rows — respiratory depression
    # Metabolic / Electrolyte
    r"\bmay increase the hyperkalemic\b",    # 278 rows — dangerous potassium levels
    r"\bmay increase the hypoglycemic\b",    # 2109 rows — dangerous blood sugar drop
    r"\bmay increase the hypokalemic\b",     # 1204 rows
    r"\bmay increase the hypocalcemic\b",    # 180 rows
    r"\bmay increase the hyponatremic\b",    # 118 rows
    # Organ toxicity
    r"\bmay increase the nephrotoxic\b",     # 662 rows — kidney damage
    r"\bmay increase the hepatotoxic\b",     # kidney/liver damage
    r"\bmay increase the immunosuppressive\b", # 312 rows
    # General danger keywords
    r"\blife.?threatening\b",
    r"\bfatal\b",
    r"\bcontraindicated\b",
    r"\bserious\b",
    r"\bsevere\b",
    r"\bdangerous\b",
    r"\bmay increase the risk\b",
    r"\bthe risk or severity of adverse effects\b",  # catch-all DrugBank phrase
}

# MODERATE: Interactions requiring monitoring but not immediately dangerous
MODERATE_PHRASES = {
    r"\bmay increase the hypotensive\b",         # 8411 rows — BP drop (monitoring needed)
    r"\bmay increase the hypertensive\b",        # 673 rows
    r"\bmay increase the orthostatic\b",         # 616 rows — orthostatic hypotension
    r"\bmay increase the antihypertensive\b",    # 629 rows
    r"\bmay decrease the antihypertensive\b",    # 3089 rows
    r"\bmay decrease the excretion\b",           # 1824 rows — slower clearance
    r"\bmay decrease the sedative\b",            # 558 rows
    r"\bmay decrease the stimulatory\b",         # 498 rows
    r"\bmay decrease the bronchodilatory\b",     # 361 rows
    r"\bmay decrease the diuretic\b",            # 324 rows
    r"\bmay decrease the vasoconstricting\b",    # 309 rows
    r"\bmay increase the fluid\b",               # 422 rows — fluid retention
    r"\bmay increase the anticholinergic\b",     # 323 rows
    r"\bmay decrease the cardiotoxic\b",         # 1043 rows (decrease = slightly safer)
    r"\bmay decrease\b",                         # general decrease = reduced efficacy
    r"\bmay reduce\b",
    r"\bmay impair\b",
    r"\bmay alter\b",
    r"\bmoderate\b",
}

SEVERITY_PATTERNS = [
    ("contraindicated", [r"\bcontraindicated\b"]),
    ("major", list(MAJOR_PHRASES)),
    ("moderate", list(MODERATE_PHRASES)),
]


def classify_severity(description: str) -> str:
    desc_lower = description.lower()
    for severity, patterns in SEVERITY_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, desc_lower):
                return severity
    return "minor"


def process_corpus() -> None:
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    seen_pairs: set = set()
    all_records: list = []
    severity_counts: dict = {"contraindicated": 0, "major": 0, "moderate": 0, "minor": 0}
    rows_read = 0
    rows_skipped = 0

    print(f"Reading DDI corpus from: {CSV_PATH}")

    with open(CSV_PATH, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)

        for i, row in enumerate(reader):
            rows_read += 1

            drug_a = row.get("Drug 1", "").strip().lower()
            drug_b = row.get("Drug 2", "").strip().lower()
            description = row.get("Interaction Description", "").strip()

            # Skip empty or incomplete rows
            if not drug_a or not drug_b or not description:
                rows_skipped += 1
                continue

            # Deduplicate (both directions treated as same pair)
            pair_key = frozenset([drug_a, drug_b])
            if pair_key in seen_pairs:
                rows_skipped += 1
                continue
            seen_pairs.add(pair_key)

            severity = classify_severity(description)
            severity_counts[severity] += 1

            record = {
                "id": f"ddi_{len(all_records):06d}",
                "drug_a": drug_a,
                "drug_b": drug_b,
                "severity": severity,
                "description": description,
                "embedding_text": f"{drug_a} {drug_b} {description}"
            }
            all_records.append(record)

            if i > 0 and i % CHUNK_SIZE == 0:
                print(f"  Processed {i:,} rows, {len(all_records):,} unique pairs so far...")

    print(f"\nDone reading. Total rows: {rows_read:,}, skipped: {rows_skipped:,}")
    print(f"Unique pairs before cap: {len(all_records):,}")
    print(f"Severity distribution: {severity_counts}")

    # Sort by severity (most critical first) then cap
    severity_order = {"contraindicated": 0, "major": 1, "moderate": 2, "minor": 3}
    all_records.sort(key=lambda r: severity_order.get(r["severity"], 9))
    records = all_records[:MAX_RECORDS]
    print(f"Records after {MAX_RECORDS:,} cap: {len(records):,}")

    # Reassign sequential IDs after sort+cap
    for idx, rec in enumerate(records):
        rec["id"] = f"ddi_{idx:06d}"

    # Write ddi_processed.json
    with open(PROCESSED_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {len(records):,} records -> {PROCESSED_PATH}")

    # Write ddi_index.jsonl (id + embedding_text only)
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        for rec in records:
            line = json.dumps({"id": rec["id"], "embedding_text": rec["embedding_text"]},
                              ensure_ascii=False)
            f.write(line + "\n")
    print(f"Wrote {len(records):,} JSONL lines -> {JSONL_PATH}")


if __name__ == "__main__":
    process_corpus()
