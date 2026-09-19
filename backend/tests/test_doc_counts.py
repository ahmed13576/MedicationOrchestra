"""The counts quoted in the docs must match the knowledge base."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_every_count_quoted_in_the_docs_is_true():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_doc_counts.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_checker_catches_a_wrong_count(tmp_path):
    """A guard that cannot fail is not a guard."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "check_doc_counts", ROOT / "scripts" / "check_doc_counts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    counts = module.actual_counts()
    assert counts["ingredients"] > 0 and counts["interaction rules"] > 0

    wrong = f"The base holds {counts['ingredients'] + 7} ingredients."
    assert module.PATTERNS["ingredients"]
    import re
    match = re.search(module.PATTERNS["ingredients"], wrong, flags=re.IGNORECASE)
    assert match and int(match.group(1)) != counts["ingredients"]


def test_a_threshold_is_not_read_as_a_total():
    import importlib.util
    import re

    spec = importlib.util.spec_from_file_location(
        "check_doc_counts", ROOT / "scripts" / "check_doc_counts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = "the knowledge base is real (>50 ingredients)"
    match = re.search(module.PATTERNS["ingredients"], text, flags=re.IGNORECASE)
    assert module.COMPARISON.search(text[:match.start()])


def test_the_reminder_sender_is_gone():
    """It had no callers and no scheduler; shipping it implied a feature that
    never fired."""
    import services.notification_service as notifications

    assert not hasattr(notifications, "send_dose_reminder")
