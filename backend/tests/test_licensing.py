"""The licensing boundary is a promise, so it is pinned like any other.

Open-core only works if the line is unambiguous. A file that is claimed open in
LICENSING.md but carries no notice, or a proprietary file that quietly grew an
Apache header, both break the promise in a way nobody notices for months.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Apache-2.0. These decide what counts as safe, so they are readable and
#: challengeable by anyone.
OPEN_FILES = (
    "backend/services/clinical_engine.py",
    "scripts/safety_invariant_audit.py",
    "backend/tests/test_clinical_engine.py",
)

#: Proprietary. The product around the engine.
CLOSED_FILES = (
    "backend/main.py",
    "backend/services/explanation_service.py",
    "deploy.sh",
)


def test_the_repository_states_its_licence():
    licence = (ROOT / "LICENSE").read_text()
    assert "Apache" in licence and "ALL RIGHTS RESERVED" in licence
    assert (ROOT / "LICENSES" / "Apache-2.0.txt").read_text().startswith(
        "\n                                 Apache License")
    assert (ROOT / "LICENSING.md").is_file()


def test_every_open_file_carries_the_apache_notice():
    for name in OPEN_FILES:
        head = (ROOT / name).read_text()[:400]
        assert "SPDX-License-Identifier: Apache-2.0" in head, name


def test_no_proprietary_file_claims_to_be_open():
    for name in CLOSED_FILES:
        head = (ROOT / name).read_text()[:400]
        assert "Apache-2.0" not in head, name


def test_the_licence_and_the_licensing_note_list_the_same_open_files():
    licence = (ROOT / "LICENSE").read_text()
    guide = (ROOT / "LICENSING.md").read_text()
    for name in ("clinical_engine.py", "backend/knowledge", "safety_invariant_audit.py"):
        assert name in licence, name
        assert name in guide, name


def test_the_knowledge_base_says_it_is_not_clinician_reviewed():
    """Open does not mean approved. Anyone reusing the rules must see this."""
    def flat(path: Path) -> str:
        return " ".join(path.read_text().split())

    for path in (ROOT / "backend" / "knowledge" / "LICENSE", ROOT / "LICENSING.md"):
        assert "reviewed by a clinician" in flat(path), path.name
