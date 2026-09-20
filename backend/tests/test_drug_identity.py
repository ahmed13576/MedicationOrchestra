"""Drug identity must be exact.

Regression cover for the substring-matching defect: the previous implementation
resolved identity with `q in c or c in q`, which reported cortisone ~
hydrocortisone and ampicillin ~ pivampicillin as the *same drug*, while failing
to match anything worded differently from the corpus.
"""

from __future__ import annotations

import pytest

from services.medication_registry import normalise_name

# These pairs are genuinely different drugs. The old matcher treated each pair as
# a match; the registry must keep them apart.
AMBIGUOUS_PAIRS = [
    ("cortisone", "hydrocortisone"),
    ("ampicillin", "pivampicillin"),
    ("codeine", "dihydrocodeine"),
    ("morphine", "dihydromorphine"),
    ("nortriptyline", "amitriptyline"),
    ("paracetamol", "ibuprofen"),
]


@pytest.mark.parametrize("a,b", AMBIGUOUS_PAIRS)
def test_distinct_drugs_are_not_conflated(registry, a, b):
    id_a = registry.resolve_ingredient(a)
    id_b = registry.resolve_ingredient(b)
    assert id_a is not None, f"{a} should resolve"
    assert id_b is not None, f"{b} should resolve"
    assert id_a != id_b


def test_substring_is_never_used_for_identity(registry):
    """A name that is a substring of a known drug must NOT resolve to it."""
    assert registry.resolve_ingredient("cortis") is None
    assert registry.resolve_ingredient("hydro") is None
    assert registry.resolve_ingredient("warf") is None


def test_exact_synonyms_resolve(registry):
    assert registry.resolve_ingredient("Acetylsalicylic Acid") == "aspirin"
    assert registry.resolve_ingredient("acetaminophen") == "paracetamol"
    assert registry.resolve_ingredient("frusemide") == "furosemide"
    assert registry.resolve_ingredient("albuterol") == "salbutamol"


def test_units_and_pack_quantities_are_stripped(registry):
    assert registry.resolve_ingredient("Paracetamol 650mg") == "paracetamol"
    assert registry.resolve_ingredient("Warfarin 5 mg") == "warfarin"
    assert registry.resolve_ingredient("Ibuprofen 400mg Tablet") == "ibuprofen"


def test_normalise_name_handles_indian_label_shapes():
    assert normalise_name("Dolo-650 Tablet") == "dolo"
    assert normalise_name("PAN 40MG TAB") == "pan"
    assert normalise_name("Ecosprin AV 75/10") == "ecosprin av"
    assert normalise_name("Combiflam + Paracetamol") == "combiflam and paracetamol"
    assert normalise_name(None) == ""


def test_brand_resolves_to_ingredient_sets_not_strings(registry):
    """A combination product is an ingredient SET with strengths."""
    dolo = registry.resolve_medication("Dolo", "Paracetamol")
    assert [i.ingredient_id for i in dolo.ingredients] == ["paracetamol"]
    assert dolo.ingredients[0].mg == 650
    assert dolo.resolved_by == "brand_table"
    assert dolo.is_fully_resolved

    combiflam = registry.resolve_medication("Combiflam", "")
    ids = {i.ingredient_id for i in combiflam.ingredients}
    assert ids == {"ibuprofen", "paracetamol"}
    strengths = {i.ingredient_id: i.mg for i in combiflam.ingredients}
    assert strengths["ibuprofen"] == 400
    assert strengths["paracetamol"] == 325


def test_free_text_generic_with_different_wording_resolves(registry):
    """"Aspirin (Acetylsalicylic Acid) 75mg" used to match nothing at all."""
    res = registry.resolve_medication("Ecosprin", "Aspirin (Acetylsalicylic Acid) 75mg")
    assert res.is_fully_resolved
    assert "aspirin" in res.ingredient_ids


def test_unknown_drug_is_reported_not_guessed(registry):
    res = registry.resolve_medication("Zzqq 500", "unknownium")
    assert res.ingredients == []
    assert res.unresolved, "an unresolved drug must be reported, never silently ignored"
    assert res.confidence == "low"


def test_partially_known_combination_is_flagged(registry):
    """One known salt plus one unknown must not be reported as fully checked."""
    res = registry.resolve_medication("Mystery Syrup", "Paracetamol + Unobtainium")
    assert "paracetamol" in res.ingredient_ids
    assert res.unresolved
    assert res.is_partially_resolved and not res.is_fully_resolved


def test_knowledge_base_refuses_to_start_when_empty(tmp_path):
    """An empty knowledge base must be fatal, not a soft 'no interactions'."""
    from services.medication_registry import (
        KnowledgeBaseError,
        MedicationRegistry,
    )

    empty = tmp_path / "ingredients.json"
    empty.write_text('{"version": "0", "ingredients": {}}')
    with pytest.raises(KnowledgeBaseError):
        MedicationRegistry(ingredients_file=empty)


def test_knowledge_base_refuses_to_start_when_missing(tmp_path):
    from services.medication_registry import KnowledgeBaseError, MedicationRegistry

    with pytest.raises(KnowledgeBaseError):
        MedicationRegistry(ingredients_file=tmp_path / "does-not-exist.json")


def test_synonym_collision_is_fatal(tmp_path, registry):
    """Two ingredients claiming one synonym would silently mis-identify a drug."""
    import json

    from services.medication_registry import KnowledgeBaseError, MedicationRegistry

    doc = {
        "version": "test",
        "ingredients": {
            "drug_a": {"name": "Drug A", "synonyms": ["shared name"]},
            "drug_b": {"name": "Drug B", "synonyms": ["shared name"]},
        },
    }
    path = tmp_path / "ingredients.json"
    path.write_text(json.dumps(doc))
    with pytest.raises(KnowledgeBaseError):
        MedicationRegistry(ingredients_file=path)
