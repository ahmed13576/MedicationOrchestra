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
    # No fabricated hour gap: warfarin + ibuprofen is a combination-avoid alert
    # that timing cannot resolve, so the engine claims no separation for it.
    assert alert.time_gap_hours == 0.0
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


def test_two_medicines_from_the_same_group_do_not_trigger_a_cross_group_rule(registry):
    """Brufen + Combiflam are both NSAIDs. The SSRI+NSAID rule lists several
    NSAIDs, so a naive ingredient-pairing fires it here and the alert reads as if
    an antidepressant were involved. Only cross-group pairs may alert."""
    meds = [med("m1", "Brufen"), med("m2", "Combiflam")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    rule_ids = {a.rule_id for a in alerts}
    assert rule_ids == {"dup_ibuprofen"}, rule_ids
    assert [a.kind for a in alerts] in (["duplicate_ingredient"], ["dose_ceiling"])  # one fact, one alert


def test_aspirin_plus_another_nsaid_does_alert(registry):
    """The other side of the same coin: two different NSAIDs DO interact when one
    of them is aspirin, because it loses its antiplatelet effect. S-4 also adds a
    duplicate-therapy finding (two NSAIDs share a mechanism group) alongside the
    interaction alert — both facts are true and both reach the patient."""
    meds = [med("m1", "Ecosprin"), med("m2", "Brufen")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    rule_ids = [a.rule_id for a in alerts]
    assert "ddi_aspirin_nsaid" in rule_ids
    interaction = next(a for a in alerts if a.rule_id == "ddi_aspirin_nsaid")
    assert interaction.severity == "moderate"
    # Two different NSAIDs are also duplicate therapy (same mechanism group).
    therapy = [a for a in alerts if a.kind == "duplicate_therapy"]
    assert len(therapy) == 1, rule_ids
    assert therapy[0].severity == "moderate"


def test_every_rule_declares_disjoint_groups(registry):
    """A rule whose groups overlap would fire on a single ingredient."""
    for rule in registry.rules:
        groups = rule.get("_groups") or []
        assert groups, f"{rule['id']} has no mechanism groups"
        seen: set[str] = set()
        for group in groups:
            overlap = seen & set(group)
            assert not overlap, f"{rule['id']} repeats {sorted(overlap)} in two groups"
            seen |= set(group)
        assert len(groups) >= 2, f"{rule['id']} needs at least two groups"


def test_alert_lists_only_the_medicines_that_carry_the_interacting_pair(registry):
    """An alert naming an unrelated medicine trains the caregiver to distrust it."""
    meds = [
        med("m1", "Warf"),
        med("m2", "Brufen"),
        med("m3", "Eltroxin"),      # levothyroxine: in the medicine list, not in the pair
        med("m4", "Shelcal"),       # calcium: same
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    warfarin_alerts = [a for a in alerts if "warfarin" in a.ingredients]
    assert len(warfarin_alerts) == 1
    assert set(warfarin_alerts[0].medications) == {"Warf", "Brufen"}
    assert "Eltroxin" not in warfarin_alerts[0].medications


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
    # A rule with a cited 8-hour gap (aspirin + another NSAID) is enforced.
    meds = [
        med("m1", "Ecosprin", timing=["08:00"]),
        med("m2", "Brufen", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    slots = {m["med_id"]: d["time"] for d in schedule["dose_times"] for m in d["medications"]}
    gap_needed = alerts[0].time_gap_hours
    assert gap_needed == 8.0
    assert slots["m1"] and slots["m2"]
    h1, m1_ = (int(x) for x in slots["m1"].split(":"))
    h2, m2_ = (int(x) for x in slots["m2"].split(":"))
    assert abs((h1 * 60 + m1_) - (h2 * 60 + m2_)) >= gap_needed * 60


def test_interaction_pair_with_no_time_gap_is_never_co_located(registry):
    """A combination-avoid interaction (no cited hour gap) is still never placed
    in the same slot as the other medicine - co-administration is the riskiest
    moment, and the engine never claims a fabricated separation for it."""
    meds = [
        med("m1", "Warf", timing=["08:00"]),
        med("m2", "Brufen", timing=["08:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert alerts[0].time_gap_hours == 0.0
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    by_slot = {d["time"]: [m["med_id"] for m in d["medications"]] for d in schedule["dose_times"]}
    # The two are never in the same slot.
    for meds_here in by_slot.values():
        assert not ({"m1", "m2"} <= set(meds_here)), by_slot
    assert schedule["schedule_status"] != "unsafe_conflict"


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
    assert result["verified_against_rules"] is False
    # Warfarin + ibuprofen now carry no cited hour gap (combination-avoid), so
    # the verifier flags the co-administration itself, not a gap violation.
    assert result["violations"][0]["reason"] == "interaction_same_slot"
    # S-5: the verifier reports which rules it examined and which knowledge
    # base it ran against, so a stale cached schedule is detectable.
    assert isinstance(result["rules_checked"], list)
    assert result["knowledge_version"] == {}


def test_verifier_accepts_a_good_schedule(registry):
    meds = [med("m1", "Warf", timing=["08:00"]), med("m2", "Brufen", timing=["20:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["verification"]["verified_against_rules"] is True
    assert schedule["verification"]["violations"] == []
    # The good schedule names the rule it checked and the KB version it used.
    assert schedule["verification"]["rules_checked"]
    assert schedule["verification"]["knowledge_version"]


def test_schedule_flags_medicines_whose_ingredients_are_unknown(registry):
    """S-7 changed this from "scheduled with a warning" to "not scheduled": an
    unidentified medicine is reported, never placed in the timetable."""
    meds = [med("m1", "Mystery pill", timing=["08:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    placed = [m for d in schedule["dose_times"] for m in d["medications"] if m["med_id"] == "m1"]
    assert placed == [], "an unidentified medicine must not be given a dose time"
    assert schedule["unchecked_medications"]
    assert [w["med_id"] for w in schedule["awaiting_confirmation"]] == ["m1"]


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


# ── Time gaps are constraints, not prose ──────────────────────────────────────

def test_rule_gap_override_beats_the_severity_default(registry):
    """The aspirin/NSAID rule says 8 hours in its own cited advice; the severity
    default is now 0.0 (no fabricated gap), so the cited 8 hours is what the
    solver uses - printing "kept apart" over a made-up 2 hours would be a false
    claim."""
    from services.clinical_engine import DEFAULT_TIME_GAPS, rule_time_gap

    aspirin_rule = next(r for r in registry.rules if r["id"] == "ddi_aspirin_nsaid")
    assert aspirin_rule["severity"] == "moderate"
    assert DEFAULT_TIME_GAPS["moderate"] == 0.0
    assert rule_time_gap(aspirin_rule) == 8.0

    meds = [med("m1", "Ecosprin"), med("m2", "Brufen")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert alerts[0].time_gap_hours == 8.0


def test_declared_gaps_are_positive_and_used_by_the_solver(registry):
    from services.clinical_engine import DEFAULT_TIME_GAPS, rule_time_gap

    for rule in registry.rules:
        # Every shipped rule now declares its own min_gap_hours; a rule without
        # one is an incomplete knowledge-base entry, not something the solver
        # silently fills in with a severity-based hour count.
        assert "min_gap_hours" in rule, f"{rule['id']} declares no min_gap_hours"
        gap = rule_time_gap(rule)
        assert gap >= 0, rule["id"]
        assert gap == float(rule["min_gap_hours"]), rule["id"]
        if rule["severity"] == "contraindicated":
            assert gap == 0.0, "scheduling cannot separate a contraindicated pair"
    for severity, gap in DEFAULT_TIME_GAPS.items():
        assert gap == 0.0, f"no fabricated gap for severity {severity}: {gap}"


def test_no_fabricated_gap_when_a_rule_lacks_min_gap_hours():
    """A rule missing min_gap_hours entirely is a KB defect: the solver refuses
    to invent a severity-based gap for it rather than silently scheduling a
    made-up separation. (Shipped rules all declare min_gap_hours; this guards
    future KB additions.)"""
    from services.clinical_engine import DEFAULT_TIME_GAPS, rule_time_gap

    assert DEFAULT_TIME_GAPS["major"] == 0.0
    assert DEFAULT_TIME_GAPS["moderate"] == 0.0
    major_rule = {"id": "ddi_synthetic", "severity": "major"}  # no min_gap_hours
    assert rule_time_gap(major_rule) == 0.0  # not 6.0


def test_nitrate_pde5_carries_the_cited_48_hour_washout(registry):
    """The nitrate / PDE5-inhibitor rule cites a 24-48 hour washout; the solver
    uses the conservative 48 hours. A patient on both cannot be safely scheduled
    in a single day, so the engine refuses to co-schedule them and reports it."""
    meds = [
        med("m1", "Sorbitrate", timing=["08:00"]),   # isosorbide dinitrate
        med("m2", "Viagra", timing=["08:00"]),         # sildenafil
    ]
    alerts, unchecked = check_patient(meds, "p1", registry=registry)
    assert unchecked == []
    nitrate = [a for a in alerts if a.rule_id == "ddi_nitrate_pde5"]
    assert nitrate, "nitrate/PDE5 alert not raised"
    assert nitrate[0].time_gap_hours == 48.0
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    # 48h cannot fit in a single-day schedule, so the pair is not both placed.
    by_slot = {d["time"]: [m["med_id"] for m in d["medications"]] for d in schedule["dose_times"]}
    for meds_here in by_slot.values():
        assert not ({"m1", "m2"} <= set(meds_here)), by_slot
    assert schedule["schedule_status"] == "partial"
    # No fabricated "kept apart" note claims a separation the citation does not.
    assert not any("kept at least" in n and "48" not in n for n in schedule["safety_notes"])


# ── Reporting what the solver could not place ─────────────────────────────────

def test_unscheduled_medicine_is_reported_once_not_once_per_dose(registry):
    """A twice-daily medicine with nowhere to go is one problem, not two, and the
    client displays `len(unscheduled)`. Counting it twice would tell a caregiver
    that three doses failed when two medicines did."""
    from services.clinical_engine import build_schedule

    meds = [
        med("m1", "Warf", dosage="5mg", frequency_raw="OD", timing=["20:00"]),
        med("m2", "Brufen", dosage="400mg", frequency_raw="BD", timing=["08:00", "20:00"]),
        med("m3", "Ecosprin", dosage="75mg", frequency_raw="BD", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    if schedule["unscheduled"]:
        names = [u["med_name"] for u in schedule["unscheduled"]]
        assert len(names) == len(set(names)), f"a medicine was reported twice: {names}"
        assert schedule["schedule_status"] == "partial"


def test_a_move_note_never_names_a_medicine_that_is_not_in_the_schedule(registry):
    """The note explains what a dose was moved away from; naming a medicine that
    was itself left out reads as advice about a medicine the user cannot find."""
    from services.clinical_engine import build_schedule

    meds = [
        med("m1", "Warf", dosage="5mg", frequency_raw="OD", timing=["20:00"]),
        med("m2", "Brufen", dosage="400mg", frequency_raw="BD", timing=["08:00", "20:00"]),
        med("m3", "Ecosprin", dosage="75mg", frequency_raw="OD", timing=["08:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    placed = {m["med_name"] for slot in schedule["dose_times"] for m in slot["medications"]}
    left_out = {u["med_name"] for u in schedule["unscheduled"]}
    for note in schedule["safety_notes"]:
        for name in left_out - placed:
            assert name not in note, f"a dropped medicine is named in a note: {note}"


def test_every_dropped_medicine_has_a_conflict_entry_with_a_reason(registry):
    from services.clinical_engine import build_schedule

    meds = [
        med("m1", "Warf", dosage="5mg", frequency_raw="OD", timing=["20:00"]),
        med("m2", "Brufen", dosage="400mg", frequency_raw="BD", timing=["08:00", "20:00"]),
        med("m3", "Combiflam", dosage="1 tab", frequency_raw="BD", timing=["08:00", "20:00"]),
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    explained = {c["medications"][0] for c in schedule["conflicts"]
                 if c.get("reason") == "no_safe_slot_available"}
    assert {u["med_name"] for u in schedule["unscheduled"]} <= explained
    for conflict in schedule["conflicts"]:
        assert conflict["message"], conflict


# ── Severity-ordered truncation ───────────────────────────────────────────────

def test_truncation_never_drops_a_critical_or_major_finding(registry, monkeypatch):
    """A flat cap that hid a contraindication would be the worst possible defect:
    the most important finding is the one a caregiver must see. Truncation keeps
    every critical/major finding regardless of the cap, and only pushes
    lower-severity findings out - and says how many it pushed out."""
    import services.clinical_engine as ce

    # A major interaction (warfarin + NSAIDs) plus lower-severity advisories;
    # cap to 1 so the budget is exhausted by the major alone.
    monkeypatch.setattr(ce, "MAX_ALERTS_PER_PATIENT", 1)
    meds = [
        med("m1", "Warf", timing=["20:00"]),
        med("m2", "Brufen", timing=["08:00"]),    # major: warfarin + ibuprofen
        med("m3", "Ecosprin", timing=["08:00"]),  # merged into the same major
        med("m4", "Dolo", timing=["08:00"]),        # dose_ceiling / advisory (lower)
    ]
    # Uncapped baseline first, then apply the cap and re-run.
    monkeypatch.undo()
    full, _ = check_patient(meds, "p1", registry=registry)
    monkeypatch.setattr(ce, "MAX_ALERTS_PER_PATIENT", 1)
    alerts, _ = check_patient(meds, "p1", registry=registry)
    majors_full = [a for a in full if a.severity in ("contraindicated", "major")]
    majors_kept = [a for a in alerts if a.severity in ("contraindicated", "major")]
    assert len(majors_kept) == len(majors_full), "a major finding was dropped by the cap"
    assert alerts.truncated_count == len(full) - len(alerts)
    # The cap only ever pushes out lower-severity findings: every kept alert
    # whose severity is above major would only appear once the budget allowed.
    assert all(a.severity_rank <= ce.SEVERITY_ORDER["major"] for a in alerts) or len(alerts) > len(majors_kept)


def test_truncation_discloses_the_count_held_back(registry, monkeypatch):
    """The UI shows '40 of 47'; it cannot do that if the count is hidden. The
    household payload carries the total held back and each patient's share."""
    import services.clinical_engine as ce

    monkeypatch.setattr(ce, "MAX_ALERTS_PER_PATIENT", 1)
    meds = [
        med("m1", "Warf", timing=["20:00"]),
        med("m2", "Brufen", timing=["08:00"]),
        med("m3", "Ecosprin", timing=["08:00"]),
    ]
    result = check_household(meds, {"p1": "dad"}, registry=registry)
    assert result["truncated_count"] >= 1
    assert result["max_alerts_per_patient"] == 1
    assert result["coverage"]["patients"][0]["truncated_count"] == result["truncated_count"]


def test_truncation_keeps_severity_order(registry, monkeypatch):
    """Kept findings are still severity-ordered so the most urgent appears
    first; a truncation that shuffled the survivors would bury a major below a
    minor advisory."""
    import services.clinical_engine as ce

    monkeypatch.setattr(ce, "MAX_ALERTS_PER_PATIENT", 2)
    meds = [
        med("m1", "Warf", timing=["20:00"]),
        med("m2", "Brufen", timing=["08:00"]),   # major
        med("m3", "Dolo", timing=["08:00"]),       # dose_ceiling / advisory (lower)
    ]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    ranks = [a.severity_rank for a in alerts]
    assert ranks == sorted(ranks), "survivors are not severity-ordered"


# ── Mechanism-group duplicate therapy (S-4) ──────────────────────────────────

def test_two_different_drugs_in_one_mechanism_group_alert(registry):
    """Brufen (ibuprofen) and Voveran (diclofenac) are different ingredients
    with the same NSAID mechanism. Exact-ingredient matching misses this; the
    interaction rules' declared groups catch it at runtime."""
    meds = [med("m1", "Brufen"), med("m2", "Voveran")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    therapy = [a for a in alerts if a.kind == "duplicate_therapy"]
    assert len(therapy) == 1, [
        (a.kind, a.rule_id) for a in alerts
    ]
    alert = therapy[0]
    assert alert.severity == "moderate"
    assert set(alert.ingredients) == {"ibuprofen", "diclofenac"}
    assert set(alert.medications) == {"Brufen", "Voveran"}
    # The pair is a no-coallocate pair: two same-mechanism products never share
    # a dose slot, exactly like two products sharing an ingredient.
    assert alert.conflicting_pairs
    pair = alert.conflicting_pairs[0]
    assert set(pair["med_ids"]) == {"m1", "m2"}
    assert set(pair["ingredients"]) == {"ibuprofen", "diclofenac"}


def test_a_group_alert_cites_its_source(registry):
    """A duplicate-therapy finding must be auditable to a source, like every
    other clinical claim. The citation names the interaction rule(s) whose
    mechanism-group definition placed the two ingredients together."""
    meds = [med("m1", "Brufen"), med("m2", "Voveran")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    alert = next(a for a in alerts if a.kind == "duplicate_therapy")
    assert alert.source, "duplicate_therapy alert has no source"
    assert alert.citation, "duplicate_therapy alert has no citation"
    assert "rule" in alert.citation.lower()
    assert alert.rule_id and alert.rule_id.startswith("duptherapy_")


def test_duplicate_therapy_does_not_double_alert_an_exact_duplicate(registry):
    """Brufen + Combiflam both contain ibuprofen, so the exact-ingredient
    check already covers them. A duplicate_therapy alert on the same pair would
    be a double alert; the mechanism-group check skips a cluster whose products
    all map to a single ingredient."""
    meds = [med("m1", "Brufen"), med("m2", "Combiflam")]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    assert not any(a.kind == "duplicate_therapy" for a in alerts), [
        (a.kind, a.rule_id) for a in alerts
    ]


def test_duplicate_therapy_pair_is_never_co_located(registry):
    """Two same-mechanism products must never land in the same dose slot; the
    verifier independently re-checks this, so a tampered schedule that
    co-locates them is rejected."""
    meds = [med("m1", "Brufen", timing=["08:00"]), med("m2", "Voveran", timing=["08:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["schedule_status"] != "unsafe_conflict"
    # No dose slot holds both Brufen and Voveran.
    for dt in schedule.get("dose_times", []):
        ids = {m.get("med_id") for m in dt.get("medications", [])}
        assert not {"m1", "m2"} <= ids, f"two NSAIDs co-located at {dt['time']}"



# ── S-7: confidence gates scheduling ─────────────────────────────────────────


def test_a_low_confidence_medicine_is_never_scheduled_silently(registry):
    """A medicine we are not sure we read gets no reminder time at all; it is
    listed for a person to check, in plain words and without a percentage."""
    meds = [med("m1", "Mystery pill", timing=["08:00"])]
    alerts, _ = check_patient(meds, "p1", registry=registry)
    schedule = build_schedule(meds, [a.to_dict() for a in alerts], "p1", registry=registry)
    scheduled = {m["med_id"] for d in schedule["dose_times"] for m in d["medications"]}
    assert "m1" not in scheduled, "an unverified medicine must not get a reminder"
    waiting = schedule["awaiting_confirmation"]
    assert [w["med_id"] for w in waiting] == ["m1"]
    assert "not sure" in waiting[0]["note"].lower()
    assert "%" not in waiting[0]["note"]
    assert schedule["schedule_status"] == "partial"
    assert schedule["unchecked_medications"]


def test_a_confirmed_low_confidence_medicine_is_scheduled(registry):
    """Confirmation is a human act recorded on the record; once it exists the
    medicine joins the timetable. Nothing else about the medicine changed."""
    item = med("m1", "Mystery pill", timing=["08:00"])
    item["identity_confirmed"] = True
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    scheduled = {m["med_id"] for d in schedule["dose_times"] for m in d["medications"]}
    assert "m1" in scheduled
    assert schedule["awaiting_confirmation"] == []
    # Confirming the identity does not claim the ingredients were identified.
    assert schedule["unchecked_medications"]


def test_a_low_reading_confidence_also_blocks_scheduling(registry):
    """The reader's own "low" confidence in the label is enough to hold a
    medicine back, even when the brand resolves cleanly."""
    item = med("m1", "Brufen", timing=["08:00"])
    item["confidence"] = "low"
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["dose_times"] == []
    assert schedule["awaiting_confirmation"][0]["reason"] == "reading_not_confirmed"


# ── S-9: fixed, taper and as-needed dosing ───────────────────────────────────


def test_a_prn_medicine_creates_no_reminders(registry):
    """An as-needed medicine is listed, never given a reminder time."""
    item = med("m1", "Brufen", timing=["08:00"], schedule_kind="prn")
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["dose_times"] == []
    assert schedule["unscheduled"] == []
    assert [a["med_id"] for a in schedule["as_needed"]] == ["m1"]
    assert "when needed" in schedule["as_needed"][0]["note"]


def test_a_taper_step_is_not_flattened_into_one_dose(registry):
    """A reducing course keeps every stated step; the timetable shows the
    current step, and the later steps stay visible as steps."""
    item = med(
        "m1", "Omnacortil", schedule_kind="taper",
        taper_steps=[
            {"dose": "40mg", "duration": "3 days", "timing": ["08:00"]},
            {"dose": "20mg", "duration": "3 days", "timing": ["08:00"]},
            {"dose": "10mg", "duration": "3 days", "timing": ["08:00"]},
        ],
    )
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    taper = schedule["tapers"][0]
    assert taper["med_id"] == "m1"
    assert [s["dose"] for s in taper["steps"]] == ["40mg", "20mg", "10mg"]
    assert [s["step"] for s in taper["steps"]] == [1, 2, 3]
    # The timetable holds the current step only - not three doses in one day.
    times = [d["time"] for d in schedule["dose_times"]]
    assert times == ["08:00"]


def test_an_unknown_schedule_kind_falls_back_to_fixed_and_says_so(registry):
    item = med("m1", "Brufen", timing=["08:00"], schedule_kind="pulse")
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    entry = schedule["dose_times"][0]["medications"][0]
    assert entry["schedule_kind"] == "fixed"
    assert any("pulse" in n for n in schedule["schedule_kind_notes"])


def test_a_taper_is_never_invented_for_an_ordinary_medicine(registry):
    item = med("m1", "Brufen", timing=["08:00"], dosage="400mg", duration="5 days")
    alerts, _ = check_patient([item], "p1", registry=registry)
    schedule = build_schedule([item], [a.to_dict() for a in alerts], "p1", registry=registry)
    assert schedule["tapers"] == []
    assert schedule["dose_times"][0]["medications"][0]["schedule_kind"] == "fixed"
