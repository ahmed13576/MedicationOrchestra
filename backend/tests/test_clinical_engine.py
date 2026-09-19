"""The deterministic safety core.

Every test here corresponds to a promise the product makes to a patient:
  * real interactions are found,
  * duplicate active ingredients are found,
  * two people's medicines are never checked against each other,
  * nothing is reported as "safe" when it could not be checked.
"""

from __future__ import annotations

import pytest
from fakes import med

from services.clinical_engine import (
    build_schedule,
    check_household,
    check_patient,
    verify_schedule,
)

# ── Pairwise interaction detection ────────────────────────────────────────────


def test_major_interaction_detected_across_brand_names(registry):
    meds = [med("m1", "Warf"), med("m2", "Brufen")]
    alerts, unchecked = check_patient(meds, "p1", registry=registry)
    assert unchecked == []
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.severity == "major"
    assert set(alert.ingredients) == {"warfarin", "ibuprofen"}
    assert alert.time_gap_hours == 6.0
    # Every assertion must be traceable to a source for a pharmacist to audit.
    assert alert.source and alert.citation and alert.rule_id


def test_contraindicated_pair_reported_as_contraindicated(registry):
    meds = [med("m1", "Imuran"), med("m2", "Zyloric")]  # azathioprine + allopurinol
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert [a.severity for a in alerts] == ["contraindicated"]


def test_combination_product_participates_in_pairwise_checks(registry):
    """Ecosprin AV is aspirin + atorvastatin; it must interact as aspirin would."""
    meds = [med("m1", "Ecosprin AV"), med("m2", "Brufen")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    ingredients = {tuple(sorted(a.ingredients)) for a in alerts}
    assert ("aspirin", "ibuprofen") in ingredients


def test_second_product_carrying_the_same_ingredient_is_not_swallowed(registry):
    """Two NSAIDs plus warfarin: both NSAID products must appear in the alert."""
    meds = [med("m1", "Warf"), med("m2", "Brufen"), med("m3", "Combiflam")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    warfarin_alerts = [a for a in alerts if "warfarin" in a.ingredients]
    assert len(warfarin_alerts) == 1, "one clinical fact, one alert"
    assert set(warfarin_alerts[0].medications) == {"Warf", "Brufen", "Combiflam"}


def test_no_alert_when_no_rule_applies(registry):
    meds = [med("m1", "Shelcal"), med("m2", "Folvite")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert alerts == []


# ── Duplicate active ingredient / dose ledger ─────────────────────────────────


def test_duplicate_paracetamol_across_two_products_is_flagged(registry):
    """Dolo 650 + Combiflam = 1300 mg paracetamol per dose-time. Previously silent."""
    meds = [
        med("m1", "Dolo", dosage="650mg", timing=["08:00", "13:00", "20:00"]),
        med("m2", "Combiflam", dosage="1 tab", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    duplicates = [a for a in alerts if a.ingredients == ["paracetamol"]]
    assert duplicates, "duplicate paracetamol must be reported"
    dup = duplicates[0]
    assert dup.kind in ("duplicate_ingredient", "dose_ceiling")
    assert dup.severity == "major"
    assert "975" in dup.detail, "the dose arithmetic must be shown, not just the fact"
    assert "Dolo" in dup.detail and "Combiflam" in dup.detail
    assert set(dup.medications) == {"Dolo", "Combiflam"}


def test_same_ingredient_alert_does_not_fire_for_a_single_product(registry):
    meds = [med("m1", "Dolo")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert [a for a in alerts if a.kind.startswith("dup") or a.kind == "dose_ceiling"] == []


def test_ultracet_and_dolo_share_paracetamol(registry):
    meds = [med("m1", "Ultracet"), med("m2", "Dolo")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert any(a.ingredients == ["paracetamol"] for a in alerts)


# ── Unresolved coverage ───────────────────────────────────────────────────────


def test_unresolved_medication_is_reported_not_rendered_safe(registry):
    meds = [med("m1", "Illegible tablet"), med("m2", "Warf")]
    _alerts, unchecked = check_patient(meds, "p1", registry=registry)
    assert len(unchecked) == 1
    assert unchecked[0].reason == "ingredients_not_identified"
    assert unchecked[0].unresolved_parts == ["Illegible tablet"]


def test_coverage_block_reports_partial_coverage(registry):
    result = check_household([
        med("m1", "Warf", "p1"),
        med("m2", "Mystery pill", "p1"),
    ], registry=registry)
    coverage = result["coverage"]
    assert coverage["medications_total"] == 2
    assert coverage["medications_fully_checked"] == 1
    assert coverage["is_complete"] is False
    assert coverage["coverage_percent"] == 50.0


def test_coverage_is_complete_for_fully_resolved_household(registry):
    result = check_household([med("m1", "Warf", "p1")], registry=registry)
    assert result["coverage"]["is_complete"] is True


# ── Patient scoping ───────────────────────────────────────────────────────────


def test_medications_of_different_patients_are_never_paired(registry):
    """Dad's warfarin and mum's ibuprofen are not an interaction between anyone."""
    meds = [med("m1", "Warf", "dad"), med("m2", "Brufen", "mom")]
    result = check_household(meds, {"dad": "Father", "mom": "Mother"}, registry=registry)
    assert result["alerts"] == []
    assert result["coverage"]["coverage_percent"] == 100.0


def test_the_same_pair_does_alert_within_one_patient(registry):
    meds = [med("m1", "Warf", "dad"), med("m2", "Brufen", "dad")]
    result = check_household(meds, registry=registry)
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["profile_id"] == "dad"


def test_alerts_are_reported_per_patient(registry):
    meds = [
        med("m1", "Warf", "dad"), med("m2", "Brufen", "dad"),
        med("m3", "Dolo", "mom"), med("m4", "Combiflam", "mom"),
    ]
    result = check_household(meds, registry=registry)
    by_profile = {a["profile_id"] for a in result["alerts"]}
    assert by_profile == {"dad", "mom"}
    patients = {p["profile_id"]: p for p in result["coverage"]["patients"]}
    assert patients["dad"]["critical_count"] == 1
    assert patients["mom"]["alert_count"] == 1


# ── Alert identity ────────────────────────────────────────────────────────────


def test_alert_id_is_stable_across_runs(registry):
    """Stable ids are what make acknowledgement, SOS and audit trails possible."""
    meds = [med("m1", "Warf", "p1"), med("m2", "Brufen", "p1")]
    first, _ = check_patient(meds, "p1", registry=registry)
    second, _ = check_patient([med("m9", "Warf", "p1"), med("m8", "Brufen", "p1")],
                              "p1", registry=registry)
    assert first[0].interaction_id == second[0].interaction_id


def test_alert_id_differs_between_patients(registry):
    a, _ = check_patient([med("m1", "Warf", "dad"), med("m2", "Brufen", "dad")],
                         "dad", registry=registry)
    b, _ = check_patient([med("m1", "Warf", "mom"), med("m2", "Brufen", "mom")],
                         "mom", registry=registry)
    assert a[0].interaction_id != b[0].interaction_id


# ── Advisories ────────────────────────────────────────────────────────────────


def test_single_ingredient_advisory_is_surfaced(registry):
    meds = [med("m1", "Glycomet")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert any(a.kind == "advisory" and "Metformin" in a.title for a in alerts)


def test_multi_ingredient_advisory_requires_all_ingredients(registry):
    """The B12/PPI advisory needs a PPI; it must not fire for unrelated drugs."""
    alerts, _ = check_patient([med("m1", "Dolo")], "p1", registry=registry)
    assert all("stomach acid" not in a.title for a in alerts)


# ── Deterministic scheduling ──────────────────────────────────────────────────


def test_schedule_never_puts_duplicate_ingredients_in_one_slot(registry):
    meds = [
        med("m1", "Dolo", timing=["08:00", "13:00", "20:00"]),
        med("m2", "Combiflam", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    for slot in schedule["dose_times"]:
        ids = [m["med_id"] for m in slot["medications"]]
        assert not {"m1", "m2"} <= set(ids), f"duplicate ingredient co-located at {slot['time']}"


def test_schedule_respects_the_required_gap(registry):
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Brufen", timing=["20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    slots = {m["med_id"]: d["time"] for d in schedule["dose_times"] for m in d["medications"]}
    gap_needed = alerts[0].time_gap_hours
    assert slots["m1"] and slots["m2"]
    h1, m1_ = (int(x) for x in slots["m1"].split(":"))
    h2, m2_ = (int(x) for x in slots["m2"].split(":"))
    assert abs((h1 * 60 + m1_) - (h2 * 60 + m2_)) >= gap_needed * 60


def test_schedule_moves_a_dose_and_says_why(registry):
    meds = [
        med("m1", "Dolo", timing=["08:00", "13:00", "20:00"]),
        med("m2", "Combiflam", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["schedule_status"] == "verified"
    assert any("moved from" in note for note in schedule["safety_notes"])


def test_schedule_places_every_prescribed_dose_or_reports_it(registry):
    meds = [med("m1", "Dolo", timing=["08:00", "13:00", "20:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    placed = sum(1 for d in schedule["dose_times"] for m in d["medications"] if m["med_id"] == "m1")
    assert placed == 3
    assert schedule["unscheduled"] == []


def test_impossible_schedule_is_reported_as_partial_not_hidden(registry):
    """Warfarin + three-times-daily ibuprofen cannot be separated within the day."""
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Brufen", timing=["08:00", "13:00", "20:00"]),
        med("m3", "Combiflam", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["schedule_status"] == "partial"
    assert schedule["unscheduled"], "unplaced doses must be reported"
    assert schedule["conflicts"], "the user must be told to ask a pharmacist"
    assert any("pharmacist" in c["message"] for c in schedule["conflicts"])


def test_schedule_notes_never_contradict_the_schedule(registry):
    """The previous system persisted model-authored notes that could be false."""
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Brufen", timing=["08:00", "13:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    for note in schedule["safety_notes"]:
        if "hours apart" in note:
            for slot in schedule["dose_times"]:
                ids = {m["med_id"] for m in slot["medications"]}
                if {"m1", "m2"} <= ids:
                    pytest.fail("note claims a gap while both drugs share a slot")


def test_verifier_catches_a_tampered_schedule(registry):
    """The verifier is the gate before persistence, so it must reject a bad payload."""
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Brufen", timing=["20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    alert_dicts = [a.to_dict() for a in alerts]
    tampered = {
        "dose_times": [{
            "time": "08:00",
            "label": "Morning",
            "medications": [
                {"med_id": "m1", "med_name": "Warf", "ingredients": ["warfarin"]},
                {"med_id": "m2", "med_name": "Brufen", "ingredients": ["ibuprofen"]},
            ],
        }],
    }
    result = verify_schedule(tampered, alert_dicts)
    assert result["verified"] is False
    assert result["violations"][0]["reason"] == "required_gap_violated"


def test_verifier_accepts_a_good_schedule(registry):
    meds = [med("m1", "Warf", timing=["08:00"]), med("m2", "Brufen", timing=["20:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["verification"]["verified"] is True
    assert schedule["verification"]["violations"] == []


def test_schedule_flags_medicines_whose_ingredients_are_unknown(registry):
    meds = [med("m1", "Mystery pill", timing=["08:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    entry = next(
        m for d in schedule["dose_times"] for m in d["medications"] if m["med_id"] == "m1"
    )
    assert entry["interaction_warning"], "an unidentified medicine must not look verified"
    assert schedule["unchecked_medications"]


def test_as_needed_medicines_are_not_scheduled(registry):
    meds = [med("m1", "Emeset", frequency_raw="SOS")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["dose_times"] == []
    assert schedule["unscheduled"] == []


def test_schedule_is_deterministic(registry):
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Dolo", timing=["08:00", "13:00", "20:00"]),
        med("m3", "Combiflam", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    first = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    second = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert first["dose_times"] == second["dose_times"]
    assert first["safety_notes"] == second["safety_notes"]


def test_schedule_reports_the_knowledge_base_it_used(registry):
    meds = [med("m1", "Dolo", timing=["08:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["knowledge_base"]["interactions"]
    assert schedule["review_status"]
    assert schedule["generated_by"] == "deterministic_solver"
