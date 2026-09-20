"""API contract tests.

Each test here corresponds to a finding in docs/ADVERSARIAL_REVIEW.md. Before
the fix these behaviours were either wrong or absent; the tests exist so they
cannot regress silently.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Health & fail-loud behaviour
# ---------------------------------------------------------------------------

def test_health_reports_knowledge_base_that_is_actually_loaded(api):
    body = api.get("/health").json()
    assert body["status"] == "ok"
    kb = body["knowledge_base"]
    # F-01: the shipped image must contain a real corpus, not zero records.
    assert kb["ingredients"] > 50
    assert kb["interaction_rules"] > 10
    assert "not clinician-reviewed" in body["review_status"]


def test_readyz_checks_dependencies(api):
    body = api.get("/readyz").json()
    assert body["ready"] is True
    assert body["checks"]["knowledge_base"] is True


def test_empty_household_never_claims_safety(api):
    """F-02/F-13: no medications must not look like 'all clear'."""
    body = api.get("/api/v1/interactions").json()
    assert body["count"] == 0
    coverage = body["coverage"]
    assert coverage["coverage_percent"] == 0.0
    assert coverage["is_complete"] is False
    assert "No medications found" in body["message"]


def test_health_declares_that_no_model_makes_clinical_decisions(api):
    body = api.get("/health").json()
    assert body["decision_engine"] == "deterministic"
    assert body["model_calls_in_decision_path"] == 0


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def test_requests_without_a_token_are_rejected(api, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    response = api.get("/api/v1/profiles", headers={"Authorization": ""})
    assert response.status_code == 401


def test_invalid_token_is_rejected(api, monkeypatch):
    monkeypatch.setenv("DEV_AUTH_BYPASS", "false")
    import services.auth_service as auth

    def _boom(token, check_revoked=False):
        raise auth.firebase_admin.auth.InvalidIdTokenError("nope")

    monkeypatch.setattr(auth.firebase_admin.auth, "verify_id_token", _boom)
    response = api.get("/api/v1/profiles")
    assert response.status_code == 401


def test_dev_bypass_is_disabled_in_a_managed_runtime(api, monkeypatch):
    """DEV_AUTH_BYPASS=true must not open the door on Cloud Run."""
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    monkeypatch.setenv("K_SERVICE", "medication-orchestra")
    import services.auth_service as auth

    assert auth._dev_bypass_enabled() is False


# ---------------------------------------------------------------------------
# Profiles and medications
# ---------------------------------------------------------------------------

def test_profile_crud_round_trip(api):
    created = api.post("/api/v1/profiles", json={
        "name": "Dad", "age": 78, "conditions": ["atrial fibrillation"],
    })
    assert created.status_code == 200
    profile_id = created.json()["profile_id"]

    listing = api.get("/api/v1/profiles").json()
    assert any(p["profile_id"] == profile_id for p in listing["profiles"])

    assert api.delete(f"/api/v1/profiles/{profile_id}").status_code == 200
    assert api.get("/api/v1/profiles").json()["count"] == 0


def test_manual_medication_is_stored_and_checked(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Mom"}).json()["profile_id"]
    response = api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id,
        "brand_name": "Brufen 400",
        "dosage": "400mg",
        "frequency_english": "Twice daily",
        "timing": ["08:00", "20:00"],
    })
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert response.json()["medication_id"]

    meds = api.get(f"/api/v1/profiles/{profile_id}/medications").json()
    assert meds["count"] == 1
    # The registry resolves it, so the record is fully checked, not a guess.
    assert meds["medications"][0]["generic_name"]


def test_medication_edit_and_soft_delete(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    med_id = api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Dolo 650", "dosage": "650mg",
    }).json()["medication_id"]

    patched = api.patch(f"/api/v1/profiles/{profile_id}/medications/{med_id}",
                        json={"frequency_raw": "TDS"})
    assert patched.status_code == 200

    assert api.delete(f"/api/v1/profiles/{profile_id}/medications/{med_id}").status_code == 200
    assert api.get(f"/api/v1/profiles/{profile_id}/medications").json()["count"] == 0


def test_unknown_profile_is_a_404_not_an_empty_list(api):
    response = api.get("/api/v1/interactions", params={"profile_id": "does-not-exist"})
    assert response.status_code == 404


def test_payload_size_and_control_characters_are_rejected(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Mom"}).json()["profile_id"]
    response = api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id,
        "brand_name": "a" * 500,
        "dosage": "1\u0000mg",
    })
    assert response.status_code in (400, 422)


# ---------------------------------------------------------------------------
# F-04: duplicate salt over the ceiling
# ---------------------------------------------------------------------------

def test_duplicate_paracetamol_across_two_brands_is_flagged(api):
    """Dolo 650 + Combiflam = 1300 mg per dose-time, over the 1000 mg ceiling."""
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand, dosage, timing in (
        ("Dolo 650", "650mg", ["08:00", "13:00", "20:00"]),
        ("Combiflam", "1 tablet", ["08:00", "20:00"]),
    ):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand,
            "dosage": dosage, "frequency_raw": "BD" if brand == "Combiflam" else "TDS",
            "timing": timing,
        })

    body = api.get("/api/v1/interactions").json()
    duplicates = [a for a in body["interactions"]
                  if a["kind"] in ("duplicate_ingredient", "dose_ceiling")
                  and a["ingredients"] == ["paracetamol"]]
    assert duplicates, "duplicate paracetamol was not detected"
    alert = duplicates[0]
    assert "paracetamol" in alert["detail"].lower()
    # The alert must state the arithmetic that makes it dangerous: 650 mg from
    # Dolo plus the 325 mg hidden inside Combiflam, and the daily exposure.
    detail = alert["detail"].replace(",", "")
    assert "975" in detail, "the per-dose-time arithmetic must be shown"
    assert "2600" in detail, "the daily total must be shown, not just one dose-time"


def test_paracetamol_alert_carries_a_citation(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand in ("Dolo 650", "Combiflam"):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand, "dosage": "650mg",
        })
    body = api.get("/api/v1/interactions").json()
    for alert in body["interactions"]:
        assert alert["source"], f"{alert['id']} has no source"
        assert alert["citation"], f"{alert['id']} has no citation"
        assert alert["severity"] in ("contraindicated", "major", "moderate", "minor")


# ---------------------------------------------------------------------------
# F-04b: cross-patient contamination
# ---------------------------------------------------------------------------

def _two_patients(api) -> tuple[str, str]:
    dad = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    mom = api.post("/api/v1/profiles", json={"name": "Mom"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": dad, "brand_name": "Warfarin 5mg", "dosage": "5mg",
        "frequency_raw": "OD", "timing": ["20:00"],
    })
    api.post("/api/v1/medications/manual", json={
        "profile_id": mom, "brand_name": "Brufen 400", "dosage": "400mg",
        "frequency_raw": "BD", "timing": ["08:00", "20:00"],
    })
    return dad, mom


def test_household_check_never_pairs_two_peoples_medicines(api):
    _two_patients(api)
    body = api.get("/api/v1/interactions").json()
    assert body["interactions"] == [], (
        "dad's warfarin was paired with mom's ibuprofen - medicines belonging to "
        "different patients must never be combined"
    )
    assert body["coverage"]["medications_total"] == 2


def test_single_patient_check_still_finds_that_patients_own_risk(api):
    dad, _ = _two_patients(api)
    # Two paracetamol products for the *same* patient: a genuine duplicate.
    for brand, dosage in (("Combiflam", "1 tablet"), ("Dolo 650", "650mg")):
        api.post("/api/v1/medications/manual", json={
            "profile_id": dad, "brand_name": brand, "dosage": dosage,
        })
    body = api.get("/api/v1/interactions", params={"profile_id": dad}).json()
    kinds = {a["kind"] for a in body["interactions"]}
    assert kinds & {"duplicate_ingredient", "dose_ceiling"}
    assert all(a["profile_id"] == dad for a in body["interactions"])


# ---------------------------------------------------------------------------
# F-03: identity is exact, so the real aspirin + clopidogrel pair is found
# ---------------------------------------------------------------------------

def test_aspirin_clopidogrel_is_found_from_free_text(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand, generic, dosage in (
        ("Ecosprin 75", "Aspirin (Acetylsalicylic Acid) 75mg", "75mg"),
        ("Clopilet 75", "Clopidogrel 75mg", "75mg"),
    ):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand,
            "generic_name": generic, "dosage": dosage,
        })
    body = api.get("/api/v1/interactions").json()
    found = any(
        any("Ecosprin" in name for name in a["medications"])
        and any("Clopilet" in name for name in a["medications"])
        for a in body["interactions"]
    )
    assert found, (
        "aspirin+clopidogrel was missed; got "
        f"{[a['medications'] for a in body['interactions']]}"
    )


def test_unreadable_medicine_is_reported_unchecked_never_assumed_safe(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Illegible tablet", "dosage": "1",
    })
    body = api.get("/api/v1/interactions").json()
    assert body["coverage"]["is_complete"] is False
    assert body["coverage"]["medications_unchecked"] == 1
    assert body["unchecked"][0]["reason"]


def test_coverage_ledger_names_the_unchecked_medicine(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Warfarin 5mg", "dosage": "5mg",
    })
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Illegible tablet", "dosage": "1",
    })
    coverage = api.get("/api/v1/interactions").json()["coverage"]
    assert coverage["medications_total"] == 2
    assert coverage["medications_fully_checked"] == 1
    assert f"{coverage['coverage_percent']:.0f}" == "50"


# ---------------------------------------------------------------------------
# Acknowledgement persistence (F-05: it used to be hardcoded False)
# ---------------------------------------------------------------------------

def test_acknowledgement_persists_and_merges_back(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand in ("Dolo 650", "Combiflam"):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand, "dosage": "650mg",
        })

    alerts = api.get("/api/v1/interactions").json()["interactions"]
    assert alerts and all(a["acknowledged"] is False for a in alerts)
    alert_id = alerts[0]["id"]

    assert api.post(f"/api/v1/interactions/{alert_id}/acknowledge").status_code == 200
    refreshed = api.get("/api/v1/interactions").json()["interactions"]
    assert any(a["id"] == alert_id and a["acknowledged"] for a in refreshed)

    api.delete(f"/api/v1/interactions/{alert_id}/acknowledge")
    refreshed = api.get("/api/v1/interactions").json()["interactions"]
    assert all(not a["acknowledged"] for a in refreshed if a["id"] == alert_id)


# ---------------------------------------------------------------------------
# F-05: SOS
# ---------------------------------------------------------------------------

def _sos_setup(api, *, link_device: bool = True) -> str:
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand in ("Dolo 650", "Combiflam"):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand, "dosage": "650mg",
        })
    api.post("/api/v1/family", json={
        "name": "Anita", "relationship": "daughter",
        "fcm_token": "device-token-anita" if link_device else "",
        "phone": "+919000000000",
    })
    alerts = api.get("/api/v1/interactions").json()["interactions"]
    assert alerts, "expected a finding to raise an SOS about"
    return alerts[0]["id"]


def test_sos_uses_the_alert_id_the_api_itself_issued(api):
    alert_id = _sos_setup(api)
    response = api.post("/api/v1/sos/alert", json={
        "alert_id": alert_id, "patient_name": "Dad",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sent_to"] == 1
    assert body["alert_id"] == alert_id


def test_sos_rejects_an_alert_that_does_not_exist(api):
    """The old client posted a field the payload never contained; that must fail
    loudly instead of silently doing nothing."""
    api.post("/api/v1/family", json={"name": "Anita", "fcm_token": "t"})
    response = api.post("/api/v1/sos/alert", json={
        "alert_id": "a-fabricated-id", "patient_name": "Dad",
    })
    assert response.status_code == 404
    assert "no longer on file" in response.json()["detail"]


def test_sos_requires_a_contact(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    for brand in ("Dolo 650", "Combiflam"):
        api.post("/api/v1/medications/manual", json={
            "profile_id": profile_id, "brand_name": brand, "dosage": "650mg",
        })
    alert_id = api.get("/api/v1/interactions").json()["interactions"][0]["id"]
    response = api.post("/api/v1/sos/alert", json={"alert_id": alert_id})
    assert response.status_code == 409


def test_sos_reports_a_contact_without_a_device_instead_of_pretending(api):
    alert_id = _sos_setup(api, link_device=False)
    response = api.post("/api/v1/sos/alert", json={"alert_id": alert_id})
    assert response.status_code == 409
    assert "no_device_linked" in response.json()["detail"]


def test_sos_never_scans_other_users_devices(api, monkeypatch):
    """F-05: the old code used collection_group('devices') across all users.

    FakeFirestore raises AssertionError if collection_group is ever used, so a
    regression fails here rather than in production.
    """
    calls = {"group": 0}
    original = api.store.collection_group

    def _spy(name):
        calls["group"] += 1
        return original(name)

    monkeypatch.setattr(api.store, "collection_group", _spy)
    alert_id = _sos_setup(api)
    assert api.post("/api/v1/sos/alert", json={"alert_id": alert_id}).status_code == 200
    assert calls["group"] == 0


def test_sos_rate_limit_stops_a_repeat_within_the_window(api):
    alert_id = _sos_setup(api)
    assert api.post("/api/v1/sos/alert", json={"alert_id": alert_id}).status_code == 200
    second = api.post("/api/v1/sos/alert", json={"alert_id": alert_id})
    assert second.status_code == 409
    assert "rate_limited" in second.json()["detail"]


def test_sos_records_a_delivery_receipt(api):
    alert_id = _sos_setup(api)
    api.post("/api/v1/sos/alert", json={"alert_id": alert_id})
    events = [e for e in _audit_events(api) if e["event"] == "sos.sent"]
    assert events, "an emergency alert must be recorded in the audit trail"


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------

def test_device_registration_writes_a_lookup_index(api):
    response = api.post("/api/v1/devices/register", json={
        "fcm_token": "token-abc", "platform": "android",
    })
    assert response.status_code == 200
    indexed = [p for p in api.store.docs if p[:1] == ("device_index",)]
    assert len(indexed) == 1, "a token index is required to avoid cross-user scans"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def test_schedule_is_verified_and_reports_partial_feasibility(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Warfarin 5mg", "dosage": "5mg",
        "frequency_raw": "OD", "timing": ["08:00"],
    })
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Brufen 400", "dosage": "400mg",
        "frequency_raw": "BD", "timing": ["08:00", "20:00"],
    })
    body = api.post("/api/v1/schedule/generate").json()
    schedule = body["schedules"][0]
    assert schedule["verification"]["verified"] is True
    assert schedule["verification"]["violations"] == []
    assert schedule["schedule_status"] != "unsafe_conflict"
    assert schedule["generated_by"] == "deterministic_solver"
    # Warfarin and ibuprofen need a 6-hour gap: the API must have separated them
    # rather than serving the prescription verbatim.
    for slot in schedule["dose_times"]:
        names = [m["med_name"] for m in slot["medications"]]
        assert not (any("Warfarin" in n for n in names) and any("Brufen" in n for n in names)), (
            f"warfarin and ibuprofen were scheduled together at {slot['time']}"
        )
    assert body["model_calls_in_decision_path"] == 0


def test_schedule_with_no_medications_says_so(api):
    body = api.post("/api/v1/schedule/generate").json()
    assert body["schedules"] == []
    assert "No medications found" in body["message"]


# ---------------------------------------------------------------------------
# Privacy: consent, export, deletion
# ---------------------------------------------------------------------------

def test_consent_is_recorded_with_a_version(api):
    response = api.post("/api/v1/users/consent", json={
        "consent_version": "1.0", "accepted": True,
        "purposes": ["medication_safety"],
    })
    assert response.status_code == 200
    settings = api.get("/api/v1/users/settings").json()
    assert settings["consent"]["accepted"] is True
    assert settings["consent"]["consent_version"] == "1.0"


def test_withdrawing_consent_is_recorded(api):
    api.post("/api/v1/users/consent", json={
        "consent_version": "1.0", "accepted": True,
    })
    api.post("/api/v1/users/consent", json={
        "consent_version": "1.0", "accepted": False,
    })
    events = [e for e in _audit_events(api) if e["event"] == "consent.recorded"]
    assert len(events) == 2


def test_export_returns_the_users_own_data(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Dolo 650", "dosage": "650mg",
    })
    body = api.get("/api/v1/users/export").json()
    assert body["profiles"] and body["medications"]
    assert body["user_id"] == api.user_id


def test_deletion_requires_an_explicit_confirmation(api):
    assert api.delete("/api/v1/users/data").status_code == 422
    assert api.delete("/api/v1/users/data", params={"confirm": "yes"}).status_code == 400


def test_deletion_removes_health_data_and_keeps_the_audit_trail(api):
    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Dolo 650", "dosage": "650mg",
    })
    response = api.delete("/api/v1/users/data", params={"confirm": "DELETE_MY_DATA"})
    assert response.status_code == 200
    assert api.get("/api/v1/profiles").json()["count"] == 0
    # The record that a deletion happened must survive the deletion (DPDP
    # requires a demonstrable audit trail; it contains no health data).
    assert any(e["event"] == "data.deleted" for e in _audit_events(api))


def test_every_health_data_read_is_audited(api):
    api.post("/api/v1/profiles", json={"name": "Dad"})
    api.get("/api/v1/interactions")
    events = {e["event"] for e in _audit_events(api)}
    assert {"interaction.checked"} <= events


def test_one_users_data_is_never_visible_to_another(api, store):
    from fakes import FakeFirestore
    from fastapi.testclient import TestClient

    import main
    import services.firestore_service as fs

    profile_id = api.post("/api/v1/profiles", json={"name": "Dad"}).json()["profile_id"]
    api.post("/api/v1/medications/manual", json={
        "profile_id": profile_id, "brand_name": "Dolo 650", "dosage": "650mg",
    })

    other = FakeFirestore()
    original = main.db
    try:
        main.db = other
        fs.db = other
        intruder = TestClient(main.app)
        intruder.headers.update({"Authorization": "Bearer test-token"})
        intruder.user_id = api.user_id
        assert intruder.get("/api/v1/profiles").json()["count"] == 0
    finally:
        main.db = original
        fs.db = original


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _audit_events(api) -> list[dict]:
    import services.audit_service as audit

    return audit.list_events(api.user_id, limit=500, db=api.store)
