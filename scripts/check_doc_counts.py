#!/usr/bin/env python3
"""Check the counts quoted in the docs against the knowledge base itself.

Docs age badly: a README saying "195 ingredients" stays convincing long after
the file says something else, and a wrong count in a clinical product is a
credibility problem, not a typo. This script is the cheap guard - it reads the
knowledge base, finds every quoted count in the docs, and fails when they
disagree. Run it in CI and before any release.

Usage:  python3 scripts/check_doc_counts.py [--fix]
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "backend" / "knowledge"

#: label -> (how to count it, regex naming that count in prose)
PATTERNS = {
    "ingredients": r"(\d[\d,]*)\s+(?:unique\s+)?ingredients?\b",
    "interaction rules": r"(\d[\d,]*)\s+interaction\s+rules?\b",
    "brand presentations": r"(\d[\d,]*)\s+brand\s+presentations?\b",
    "advisories": r"(\d[\d,]*)\s+advisor(?:y|ies)\b",
}

DOC_GLOBS = ("*.md", "docs/*.md", "backend/*.md")

#: A comparison ("more than 50 ingredients") is a threshold, not a claim about
#: the size of the knowledge base, so it is left alone.
COMPARISON = re.compile(
    r"(?:>|≥|~|at least|over|more than|fewer than|under|about|around)\s*$",
    re.IGNORECASE,
)

#: A qualified subset ("4 ingredients carry explicit ceilings") is also not a
#: total. Only unqualified counts are checked.
SUBSET = re.compile(
    r"^\s*(?:carry|carries|have|has|with|of|in|are|were|lack)\b",
    re.IGNORECASE,
)


def actual_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    ingredients = json.loads((KB / "ingredients.json").read_text())
    counts["ingredients"] = len(ingredients.get("ingredients", ingredients))

    interactions = json.loads((KB / "interactions.json").read_text())
    rules = interactions.get("rules", interactions.get("interactions", []))
    counts["interaction rules"] = len(rules)
    counts["advisories"] = len(interactions.get("advisories", []))

    brands = KB / "brand_mapping.csv"
    with brands.open(newline="") as handle:
        counts["brand presentations"] = sum(1 for _ in csv.DictReader(handle))
    return counts


def doc_files() -> list[Path]:
    seen: list[Path] = []
    for pattern in DOC_GLOBS:
        for path in sorted(ROOT.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.append(path)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true",
                        help="rewrite the docs with the real counts")
    args = parser.parse_args()

    counts = actual_counts()
    print("Knowledge base:", ", ".join(f"{v} {k}" for k, v in counts.items()))

    problems: list[str] = []
    for path in doc_files():
        text = original = path.read_text()
        for label, pattern in PATTERNS.items():
            real = counts[label]
            for match in list(re.finditer(pattern, text, flags=re.IGNORECASE)):
                quoted = int(match.group(1).replace(",", ""))
                if quoted == real:
                    continue
                if COMPARISON.search(text[max(0, match.start() - 24):match.start()]):
                    continue
                if SUBSET.match(text[match.end():match.end() + 24]):
                    continue
                where = f"{path.relative_to(ROOT)}: says {quoted} {label}, actual {real}"
                problems.append(where)
                if args.fix:
                    text = (text[:match.start(1)] + str(real)
                            + text[match.end(1):])
        if args.fix and text != original:
            path.write_text(text)

    if not problems:
        print("All quoted counts match the knowledge base.")
        return 0
    if args.fix:
        print(f"Rewrote {len(problems)} count(s):")
    else:
        print(f"{len(problems)} quoted count(s) disagree with the knowledge base:")
    for problem in problems:
        print("  -", problem)
    return 0 if args.fix else 1


if __name__ == "__main__":
    sys.exit(main())
