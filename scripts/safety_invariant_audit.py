#!/usr/bin/env python3
"""
safety_invariant_audit.py — Medication Orchestra

Executable specification of the safety invariants this product claims to
guarantee. Every check runs against the real backend code — the registry, the
clinical engine, and the actual FastAPI endpoint functions — and drives them
with an in-memory Firestore. Nothing is described, everything is executed.

Run from the repository root:

    python3 scripts/safety_invariant_audit.py

Exit code is 0 only when every invariant holds, so this is safe to wire into CI
as a gate. Invariants are numbered INV-1..INV-10 and map to the findings in
docs/ADVERSARIAL_REVIEW.md:

  INV-1  the clinical knowledge base ships with the service
  INV-2  an empty or unresolved state fails loud, never "safe"
  INV-3  drug identity is exact (no substring matching)
  INV-4  a medicine we could not identify is reported as unchecked
  INV-5  duplicate active ingredients and dose ceilings are visible
  INV-6  the schedule honours required gaps, or says it could not
  INV-7  no language model participates in a clinical decision
  INV-8  medicines belonging to different patients are never paired
  INV-9  acknowledgements persist and SOS references a real finding
  INV-10 access is tenant-scoped and profile-scoped

Requires the app's own dependencies (fastapi, pydantic, pillow). No credentials,
no network: the Google Cloud clients are stubbed below.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
TESTS = BACKEND / "tests"
for path in (str(BACKEND), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("PROJECT_ID", "audit-project")
os.environ.setdefault("DEV_AUTH_BYPASS", "true")
logging.disable(logging.CRITICAL)

RESULTS: list[tuple[str, str, str]] = []
AUDIT_USER = "audit-user"


def record(inv_id: str, status: str, detail: str) -> None:
    RESULTS.append((inv_id, status, detail))
    print(f"  [{status}] {inv_id}: {detail}")


def check(inv_id: str, condition: bool, ok: str, fail: str) -> bool:
    record(inv_id, "PASS" if condition else "FAIL", ok if condition else fail)
    return condition


# ---------------------------------------------------------------------------
# Google Cloud stubs — the real modules build clients at import time.
# ---------------------------------------------------------------------------

def install_stubs() -> None:
    if "google.cloud.firestore" not in sys.modules:
        gcf = types.ModuleType("google.cloud.firestore")

        class _Client:
            def __init__(self, *a, **k): ...

        gcf.Client = _Client
        gcf.SERVER_TIMESTAMP = None
        gcloud = types.ModuleType("google.cloud")
        gcloud.firestore = gcf
        sys.modules["google.cloud"] = gcloud
        sys.modules["google.cloud.firestore"] = gcf

    if "google.genai" not in sys.modules:
        ggenai = types.ModuleType("google.genai")
        ggenai.types = types.ModuleType("google.genai.types")
        ggenai.Client = lambda *a, **k: types.SimpleNamespace(
            models=types.SimpleNamespace(generate_content=lambda *a, **k: None)
        )
        ggenai.types.Part = types.SimpleNamespace(from_bytes=lambda **k: None)
        ggenai.types.GenerateContentConfig = lambda **k: None
        google = sys.modules.get("google") or types.ModuleType("google")
        google.genai = ggenai
        sys.modules["google"] = google
        sys.modules["google.genai"] = ggenai
        sys.modules["google.genai.types"] = ggenai.types

    if "firebase_admin" not in sys.modules:
        fa = types.ModuleType("firebase_admin")
        fa._apps = [object()]
        fa.initialize_app = lambda *a, **k: None
        fa.credentials = types.SimpleNamespace(Certificate=lambda p: None)

        class _Auth:
            ExpiredIdTokenError = type("ExpiredIdTokenError", (Exception,), {})
            InvalidIdTokenError = type("InvalidIdTokenError", (Exception,), {})
            RevokedIdTokenError = type("RevokedIdTokenError", (Exception,), {})

            def verify_id_token(self, token, check_revoked=False):
                return {"uid": AUDIT_USER}

        fa.auth = _Auth()
        messaging = types.ModuleType("firebase_admin.messaging")
        for name in ("Message", "Notification", "AndroidConfig"):
            setattr(messaging, name, lambda **kwargs: types.SimpleNamespace(**kwargs))
        messaging.send = lambda *a, **k: "audit-message-id"
        fa.messaging = messaging
        sys.modules["firebase_admin"] = fa
        sys.modules["firebase_admin.auth"] = fa.auth
        sys.modules["firebase_admin.messaging"] = messaging


# ---------------------------------------------------------------------------
# A live app bound to an in-memory Firestore
# ---------------------------------------------------------------------------

class Harness:
    """The real endpoints, the real engine, an in-memory database."""

    def __init__(self):
        from fakes import FakeFirestore

        import main
        import services.firestore_service as firestore_service
        from services.rate_limit import RateLimiter

        self.main = main
        self.firestore_service = firestore_service
        self.store = FakeFirestore()
        main.rate_limit.limiter = RateLimiter()
        self.rebind()
        self.record_consent()

    def rebind(self) -> None:
        """Point the app at the in-memory store (also after a module reload)."""
        self.main.db = self.store
        self.firestore_service.db = self.store

    def reset(self) -> None:
        """Start an invariant from a clean slate.

        Each invariant gets its own database and its own rate-limit counters, so
        it cannot be affected by (or accidentally depend on) the ones before it.
        The limiter itself is exercised in the test-suite.
        """
        from fakes import FakeFirestore
        from services.rate_limit import RateLimiter

        self.store = FakeFirestore()
        self.main.rate_limit.limiter = RateLimiter()
        self.rebind()
        self.record_consent()

    def record_consent(self, user_id: str = None) -> None:
        """Give the audit user a real consent record (D-1 gates every endpoint)."""
        from services import consent_service

        self.store.collection("users").document(user_id or AUDIT_USER).set({
            "consent": {
                "consent_version": "audit-1",
                "accepted": True,
                "purposes": sorted(consent_service.SCOPES),
            },
        }, merge=True)

    # -- endpoint callers (the real functions, not reimplementations) --------

    def add_profile(self, name: str = "Audit patient") -> str:
        return asyncio.run(
            self.main.create_profile(
                self.main.ProfilePayload(name=name), user_id=AUDIT_USER
            )
        )["profile_id"]

    def add_medication(self, profile_id: str, brand: str, **fields) -> str:
        payload = {"profile_id": profile_id, "brand_name": brand}
        payload.update(fields)
        return asyncio.run(
            self.main.add_medication_manually(
                self.main.MedicationPayload(**payload), user_id=AUDIT_USER
            )
        )["medication_id"]

    def interactions(self, profile_id: str = "all", language: str = "en") -> dict:
        return asyncio.run(
            self.main.get_interactions(
                profile_id=profile_id, language=language, user_id=AUDIT_USER
            )
        )

    def schedule(self, profile_id: str = "all") -> dict:
        return asyncio.run(
            self.main.generate_schedule_endpoint(
                profile_id=profile_id, language="en", user_id=AUDIT_USER
            )
        )

    def add_family(self, name: str, fcm_token: str = "") -> str:
        return asyncio.run(
            self.main.add_family_member(
                self.main.FamilyMemberPayload(name=name, fcm_token=fcm_token),
                user_id=AUDIT_USER,
            )
        )["member_id"]

    def sos(self, alert_id: str):
        return asyncio.run(
            self.main.trigger_sos_alert(
                self.main.SosPayload(alert_id=alert_id, patient_name="Audit patient"),
                user_id=AUDIT_USER,
            )
        )

    def acknowledge(self, alert_id: str) -> dict:
        return asyncio.run(
            self.main.acknowledge_interaction(alert_id, user_id=AUDIT_USER)
        )

    def send_sos(self, recipients, **kwargs) -> dict:
        from services.notification_service import send_sos_alert

        return send_sos_alert(recipients=recipients, **kwargs)


# ---------------------------------------------------------------------------
# INV-1 — the knowledge base ships with the service
# ---------------------------------------------------------------------------

def inv_1_knowledge_base(harness: Harness) -> None:
    print("\nINV-1  clinical knowledge base ships with the service")
    knowledge = BACKEND / "knowledge"
    for name in ("ingredients.json", "interactions.json", "brand_mapping.csv"):
        if not (knowledge / name).exists():
            check("INV-1", False, "", f"{name} is missing from backend/knowledge/")
            return

    registry = harness.main.REGISTRY
    counts = registry.describe()
    check(
        "INV-1",
        counts["ingredients"] >= 50 and counts["interaction_rules"] >= 10,
        f"registry loaded {counts['ingredients']} ingredients, "
        f"{counts['interaction_rules']} interaction rules, "
        f"{counts['brand_presentations']} brand presentations",
        f"knowledge base is too small to be real: {counts}",
    )

    dockerignore = (BACKEND / ".dockerignore").read_text()
    excluded = [
        line.strip() for line in dockerignore.splitlines()
        if line.strip() in ("data", "data/", "knowledge", "knowledge/")
    ]
    check(
        "INV-1", not excluded,
        "no knowledge directory is excluded from the build context",
        f".dockerignore excludes the clinical data: {excluded}",
    )

    dockerfile = (BACKEND / "Dockerfile").read_text()
    check(
        "INV-1",
        "COPY knowledge/" in dockerfile and "get_registry" in dockerfile,
        "the image copies the knowledge base and verifies it loads at build time",
        "the Dockerfile does not copy/verify the knowledge base",
    )

    readyz = asyncio.run(harness.main.readyz())
    body = json.loads(readyz.body)
    check(
        "INV-1",
        readyz.status_code == 200 and body["ready"] is True,
        f"/readyz reports ready with checks {body['checks']}",
        f"/readyz is not ready: {body}",
    )


# ---------------------------------------------------------------------------
# INV-2 — empty states fail loud
# ---------------------------------------------------------------------------

def inv_2_fail_loud(harness: Harness) -> None:
    print("\nINV-2  an empty or unconfigured service fails loud, never 'safe'")
    empty = harness.interactions()
    coverage = empty["coverage"]
    check(
        "INV-2",
        coverage["coverage_percent"] == 0.0 and coverage["is_complete"] is False
        and "No medications found" in empty.get("message", ""),
        "with no medications the API reports 0% coverage, is_complete=False and says so",
        f"empty state could be read as safe: {coverage}",
    )

    check(
        "INV-2",
        empty["knowledge_base"]["ingredients"] > 0 and empty["count"] == 0,
        "the empty response still reports the loaded knowledge base and zero findings",
        "the empty response does not report knowledge-base provenance",
    )

    # A missing PROJECT_ID must stop the process, not degrade it. Reloading the
    # module in-process is how we observe that a fresh start would refuse.
    saved = os.environ.pop("PROJECT_ID", None)
    try:
        importlib.reload(harness.main)
        started = True
    except RuntimeError:
        started = False
    finally:
        if saved:
            os.environ["PROJECT_ID"] = saved
        importlib.reload(harness.main)
        harness.rebind()
    check(
        "INV-2", not started,
        "a missing PROJECT_ID aborts startup instead of serving empty answers",
        "the service started with no PROJECT_ID configured",
    )

    from services.medication_registry import KnowledgeBaseError, MedicationRegistry

    try:
        MedicationRegistry(ingredients_file=BACKEND / "knowledge" / "absent.json")
        raised = False
    except KnowledgeBaseError:
        raised = True
    check(
        "INV-2", raised,
        "a missing knowledge base raises KnowledgeBaseError (fatal at startup)",
        "a missing knowledge base did not raise",
    )


# ---------------------------------------------------------------------------
# INV-3 — exact drug identity
# ---------------------------------------------------------------------------

def inv_3_exact_identity(harness: Harness) -> None:
    print("\nINV-3  drug identity is exact, and a rule only fires across an interaction")
    registry = harness.main.REGISTRY

    collisions = []
    for left, right in (("cortisone", "hydrocortisone"), ("ampicillin", "pivampicillin"),
                        ("codeine", "dihydrocodeine")):
        a = registry.resolve_ingredient(left)
        b = registry.resolve_ingredient(right)
        if not a or not b:
            collisions.append(f"{left if not a else right} does not resolve")
            continue
        if a == b:
            collisions.append(f"{left}=={right}")
        if registry.rules_for_pair(a, b):
            collisions.append(f"{left}~{right} flagged as one drug")
    check(
        "INV-3", not collisions,
        "cortisone/hydrocortisone, ampicillin/pivampicillin and codeine/dihydrocodeine "
        "resolve to distinct ingredients",
        f"different drugs are treated as the same: {collisions}",
    )

    # The real pair the substring matcher missed.
    pair = registry.rules_for_pair("aspirin", "clopidogrel")
    check(
        "INV-3", bool(pair),
        "aspirin + clopidogrel resolves to a cited rule: " + (pair[0]["id"] if pair else ""),
        "aspirin + clopidogrel has no rule",
    )

    # Free text, as it appears on an Indian pack, still resolves exactly.
    resolution = registry.resolve_medication(
        "Ecosprin 75", "Aspirin (Acetylsalicylic Acid) 75mg"
    )
    ids = [i.ingredient_id for i in resolution.ingredients]
    check(
        "INV-3", ids == ["aspirin"],
        f'"Aspirin (Acetylsalicylic Acid) 75mg" resolved to {ids}',
        f"free-text identity failed: {ids} (unresolved: {resolution.unresolved})",
    )

    sources = "\n".join(
        (BACKEND / "services" / name).read_text()
        for name in ("medication_registry.py", "clinical_engine.py")
    )
    check(
        "INV-3", "_token_match" not in sources and "in generic.lower()" not in sources,
        "no substring matcher remains in the clinical path",
        "a substring matcher is still present in the clinical path",
    )

    # Mechanism groups. A rule that lists several drugs of one class must only
    # fire when the patient takes a drug from each side of the interaction: two
    # NSAIDs are not "an antidepressant plus an NSAID".
    problems: list[str] = []
    classes = {
        "ddi_ssri_nsaid": [("ibuprofen", "naproxen"), ("sertraline", "fluoxetine")],
        "ddi_steroid_nsaid": [("ibuprofen", "aspirin"), ("prednisolone", "dexamethasone")],
        "ddi_warfarin_nsaid": [("ibuprofen", "aspirin"), ("warfarin", "warfarin")],
        "ddi_aspirin_antiplatelet": [("clopidogrel", "ticagrelor"), ("aspirin", "aspirin")],
    }
    for rule_id, pairs in classes.items():
        for left, right in pairs:
            if left == right:
                continue
            firing = [r["id"] for r in registry.rules_for_pair(left, right)]
            if rule_id in firing:
                problems.append(f"{rule_id} fires on {left}+{right}, same side of the interaction")
    check(
        "INV-3", not problems,
        "a rule never fires on two drugs from the same side of the interaction "
        "(two NSAIDs are not an SSRI interaction)",
        "; ".join(problems),
    )

    # Every rule declares disjoint groups that cover its ingredient list.
    malformed: list[str] = []
    for rule in registry.rules:
        groups = rule.get("_groups") or []
        if len(groups) < 2:
            malformed.append(f"{rule['id']} has {len(groups)} group(s)")
            continue
        seen: set[str] = set()
        for group in groups:
            overlap = seen & set(group)
            if overlap:
                malformed.append(f"{rule['id']} repeats {sorted(overlap)}")
            seen |= set(group)
        for ingredient_id in rule.get("_ingredients") or []:
            if ingredient_id not in seen:
                malformed.append(f"{rule['id']} never pairs {ingredient_id}")
    check(
        "INV-3", not malformed,
        f"all {len(registry.rules)} rules declare disjoint mechanism groups covering "
        f"their ingredients",
        "; ".join(malformed),
    )


# ---------------------------------------------------------------------------
# INV-4 — unknown medicines are reported as unchecked
# ---------------------------------------------------------------------------

def inv_4_unchecked_is_visible(harness: Harness) -> None:
    print("\nINV-4  a medicine we could not identify is reported, never dropped")
    profile = harness.add_profile()
    harness.add_medication(profile, "Warfarin 5mg", dosage="5mg", frequency_raw="OD",
                           timing=["08:00"])
    harness.add_medication(profile, "handwritten squiggle", dosage="1 tab")

    body = harness.interactions(profile)
    coverage = body["coverage"]
    check(
        "INV-4",
        coverage["medications_total"] == 2 and coverage["medications_unchecked"] == 1
        and coverage["coverage_percent"] == 50.0 and coverage["is_complete"] is False,
        f"1 of 2 medicines reported unchecked; coverage {coverage['coverage_percent']}%",
        f"an unidentifiable medicine was hidden: {coverage}",
    )
    check(
        "INV-4",
        bool(body["unchecked"]) and bool(body["unchecked"][0]["reason"]),
        f"the unchecked medicine is named with a reason: "
        f"{body['unchecked'][0]['brand_name']!r} - {body['unchecked'][0]['reason'][:60]}...",
        "the unchecked medicine has no user-facing reason",
    )


# ---------------------------------------------------------------------------
# INV-5 — duplicate salt / dose ceiling
# ---------------------------------------------------------------------------

def inv_5_duplicate_salt(harness: Harness) -> None:
    print("\nINV-5  duplicate active ingredients and dose ceilings are visible")
    profile = harness.add_profile()

    harness.add_medication(profile, "Dolo 650", dosage="650mg", frequency_raw="TDS",
                           timing=["08:00", "13:00", "20:00"])
    harness.add_medication(profile, "Combiflam", dosage="1 tablet", frequency_raw="BD",
                           timing=["08:00", "20:00"])

    body = harness.interactions(profile)
    duplicates = [
        a for a in body["interactions"]
        if a["kind"] in ("duplicate_ingredient", "dose_ceiling")
        and a["ingredients"] == ["paracetamol"]
    ]
    if not duplicates:
        check("INV-5", False, "", "Dolo 650 + Combiflam produced no duplicate-paracetamol alert")
        return

    alert = duplicates[0]
    detail = alert["detail"]
    check(
        "INV-5", "975" in detail,
        "the alert shows the per-dose-time arithmetic (650 mg from Dolo + 325 mg hidden "
        "inside Combiflam = 975 mg)",
        f"the alert does not show the arithmetic: {detail[:120]}",
    )
    check(
        "INV-5", "2600" in detail,
        "the alert shows the daily exposure (2600 mg/day as prescribed)",
        f"the alert does not show the daily total: {detail[:120]}",
    )
    check(
        "INV-5", bool(alert["citation"]) and bool(alert["source"]) and alert["severity"] == "major",
        f"the alert is cited ({alert['source']}) and rated {alert['severity']}",
        "the duplicate alert carries no citation or severity",
    )


# ---------------------------------------------------------------------------
# INV-6 — the schedule honours the gaps it claims
# ---------------------------------------------------------------------------

def inv_6_schedule(harness: Harness) -> None:
    print("\nINV-6  the schedule honours required gaps, or reports that it cannot")
    profile = harness.add_profile()
    harness.add_medication(profile, "Warfarin 5mg", dosage="5mg", frequency_raw="OD",
                           timing=["08:00"])
    harness.add_medication(profile, "Brufen 400", dosage="400mg", frequency_raw="BD",
                           timing=["08:00", "20:00"])

    body = harness.schedule(profile)
    schedule = body["schedules"][0]
    for slot in schedule["dose_times"]:
        names = [m["med_name"] for m in slot["medications"]]
        if any("Warfarin" in n for n in names) and any("Brufen" in n for n in names):
            check("INV-6", False, "",
                  f"warfarin and ibuprofen share the {slot['time']} slot despite a 6-hour gap rule")
            break
    else:
        check("INV-6", True, "warfarin and ibuprofen were never placed in the same slot", "")

    check(
        "INV-6",
        schedule["verification"]["verified_against_rules"] is True
        and schedule["verification"]["violations"] == []
        and schedule["schedule_status"] != "unsafe_conflict",
        f"the finished schedule passes independent verification "
        f"({schedule['schedule_status']}, {len(schedule['dose_times'])} dose times)",
        f"the schedule failed verification: {schedule['verification']}",
    )

    # The verifier must reject a tampered schedule, not rubber-stamp it. The
    # tampered payload uses the *real* medication ids, so this tests the
    # verifier rather than the id matching.
    from services.clinical_engine import verify_schedule

    meds = harness.main._fetch_medications(AUDIT_USER, profile)
    ids = {m["brand_name"]: m["id"] for m in meds}
    tampered = {
        "profile_id": profile,
        "dose_times": [{"time": "08:00", "label": "Morning", "medications": [
            {"med_id": ids["Warfarin 5mg"], "med_name": "Warfarin 5mg", "dose": "5mg",
             "instruction": ""},
            {"med_id": ids["Brufen 400"], "med_name": "Brufen 400", "dose": "400mg",
             "instruction": ""},
        ]}],
    }
    result = verify_schedule(tampered, body["interactions"])
    check(
        "INV-6", result["verified_against_rules"] is False,
        "the independent verifier rejects a hand-written schedule that co-locates them",
        "the verifier accepted a schedule that violates the required gap",
    )

    # A model-authored note must never appear: notes come from the solver.
    check(
        "INV-6", schedule.get("generated_by") == "deterministic_solver",
        "the schedule records the deterministic solver as its author",
        f"unexpected schedule author: {schedule.get('generated_by')}",
    )

    # A rule whose own cited advice names an interval must be scheduled to that
    # interval. The aspirin/NSAID rule says 8 hours; the severity default is 2,
    # and printing "kept apart" over 2 hours would be a claim the citation does
    # not support.
    gap_profile = harness.add_profile()
    harness.add_medication(gap_profile, "Ecosprin 75", dosage="75mg", frequency_raw="OD",
                           timing=["08:00"])
    harness.add_medication(gap_profile, "Brufen 400", dosage="400mg", frequency_raw="BD",
                           timing=["08:00", "20:00"])
    gap_body = harness.schedule(gap_profile)
    gap_schedule = gap_body["schedules"][0]
    aspirin_alert = next(
        (a for a in gap_body["interactions"] if a["rule_id"] == "ddi_aspirin_nsaid"), None
    )
    place: dict[str, list[int]] = {}
    for slot in gap_schedule["dose_times"]:
        hour = int(slot["time"][:2])
        for entry in slot["medications"]:
            place.setdefault(entry["med_name"], []).append(hour)
    aspirin_slots = place.get("Ecosprin 75", [])
    brufen_slots = place.get("Brufen 400", [])
    if aspirin_slots and brufen_slots:
        # Both were placed: the cited 8 hours must actually hold.
        separated = all(
            abs(a - b) >= 8 for a in aspirin_slots for b in brufen_slots
        )
        detail = f"placed {aspirin_slots} vs {brufen_slots}"
    else:
        # One of them could not be placed at all, so nothing false is claimed -
        # but the schedule must be honest about it rather than quietly dropping
        # the medicine from a "verified" timetable.
        separated = gap_schedule["schedule_status"] == "partial" and any(
            u["med_name"] == "Ecosprin 75" for u in gap_schedule["unscheduled"]
        )
        detail = (f"Ecosprin 75 could not be placed inside the cited 8 hours "
                  f"({gap_schedule['schedule_status']})")
    check(
        "INV-6",
        aspirin_alert is not None
        and aspirin_alert["time_gap_hours"] == 8.0
        and separated,
        f"the aspirin/NSAID rule's own 8-hour interval is what the solver enforces "
        f"({detail})",
        f"the timetable separates the aspirin/NSAID pair by less than the cited "
        f"8 hours, or hides that it could not: {place} / "
        f"{gap_schedule['schedule_status']}",
    )

    # A medicine that cannot be placed is reported once, with a reason the user
    # can act on, and the schedule says it is partial rather than verified.
    tight_profile = harness.add_profile()
    harness.add_medication(tight_profile, "Warfarin 5mg", dosage="5mg",
                           frequency_raw="OD", timing=["20:00"])
    harness.add_medication(tight_profile, "Brufen 400", dosage="400mg",
                           frequency_raw="BD", timing=["08:00", "20:00"])
    harness.add_medication(tight_profile, "Combiflam", dosage="1 tab",
                           frequency_raw="BD", timing=["08:00", "20:00"])
    tight = harness.schedule(tight_profile)["schedules"][0]
    if tight["unscheduled"]:
        names = [u["med_name"] for u in tight["unscheduled"]]
        check(
            "INV-6", len(names) == len(set(names)),
            f"each unplaceable medicine is reported once: {sorted(set(names))}",
            f"a medicine is listed more than once in unscheduled: {names}",
        )
        explained = {
            c["medications"][0] for c in tight["conflicts"]
            if c.get("reason") == "no_safe_slot_available" and c.get("message")
        }
        check(
            "INV-6", set(names) <= explained,
            "every unplaceable medicine has a conflict entry with a reason",
            f"unplaceable medicines without an explanation: {sorted(set(names) - explained)}",
        )
        check(
            "INV-6", tight["schedule_status"] == "partial",
            "the schedule reports itself as partial",
            f"a schedule with unplaceable doses claims {tight['schedule_status']}",
        )

    # An alert must name the medicines that interact, not everything in the list.
    bystander_profile = harness.add_profile()
    harness.add_medication(bystander_profile, "Warfarin 5mg", dosage="5mg",
                           frequency_raw="OD", timing=["08:00"])
    harness.add_medication(bystander_profile, "Brufen 400", dosage="400mg",
                           frequency_raw="OD", timing=["20:00"])
    harness.add_medication(bystander_profile, "Eltroxin 50", dosage="50mcg",
                           frequency_raw="OD", timing=["07:00"])
    bystander_body = harness.interactions(bystander_profile)
    bleeding = [
        a for a in bystander_body["interactions"]
        if "warfarin" in a["ingredients"] and "ibuprofen" in a["ingredients"]
    ]
    check(
        "INV-6",
        bool(bleeding) and "Eltroxin 50" not in bleeding[0]["medications"],
        f"the bleeding-risk alert names only the interacting medicines: "
        f"{bleeding[0]['medications'] if bleeding else 'no alert'}",
        "an alert names a medicine that is not part of the interaction",
    )


# ---------------------------------------------------------------------------
# INV-7 — no model in the decision path
# ---------------------------------------------------------------------------

def inv_7_no_model_in_decisions(harness: Harness) -> None:
    print("\nINV-7  no language model participates in a clinical decision")
    clinical_sources = "\n".join(
        (BACKEND / "services" / name).read_text()
        for name in ("medication_registry.py", "clinical_engine.py")
    )
    check(
        "INV-7", "genai" not in clinical_sources and "gemini" not in clinical_sources.lower(),
        "the registry and the engine import no model SDK",
        "a model SDK appears in the clinical decision path",
    )

    profile = harness.add_profile()
    harness.add_medication(profile, "Dolo 650", dosage="650mg", timing=["08:00", "13:00"])
    harness.add_medication(profile, "Combiflam", dosage="1 tab", timing=["08:00"])
    without_model = harness.interactions(profile, language="hi")

    import services.explanation_service as explanation

    def _model_is_down():
        raise RuntimeError("model unavailable")

    original = explanation._client
    explanation._client = _model_is_down
    try:
        with_broken_model = harness.interactions(profile, language="hi")
    finally:
        explanation._client = original

    same = [a["id"] for a in without_model["interactions"]] == [
        a["id"] for a in with_broken_model["interactions"]
    ]
    check(
        "INV-7", same and with_broken_model["count"] > 0,
        "the findings are identical when the model is unavailable (curated text is used)",
        "the model changed the finding set",
    )
    check(
        "INV-7", with_broken_model["model_calls_in_decision_path"] == 0,
        "the response declares 0 model calls in the decision path",
        "the response does not declare its decision provenance",
    )


# ---------------------------------------------------------------------------
# INV-8 — patient scoping
# ---------------------------------------------------------------------------

def inv_8_patient_scope(harness: Harness) -> None:
    print("\nINV-8  medicines belonging to different patients are never paired")
    dad = harness.add_profile("Dad")
    mom = harness.add_profile("Mom")
    harness.add_medication(dad, "Warfarin 5mg", dosage="5mg", frequency_raw="OD",
                           timing=["08:00"])
    harness.add_medication(mom, "Brufen 400", dosage="400mg", frequency_raw="BD",
                           timing=["08:00", "20:00"])

    household = harness.interactions("all")
    check(
        "INV-8", household["interactions"] == [],
        "the household view pairs nobody: dad's warfarin and mom's ibuprofen produce no alert",
        f"cross-patient pairing: {[a['medications'] for a in household['interactions']]}",
    )
    check(
        "INV-8", household["coverage"]["medications_total"] == 2,
        "both patients' medicines are still counted in coverage",
        f"coverage lost medicines: {household['coverage']}",
    )

    # Cross-patient isolation must not hide a real, same-patient risk.
    harness.add_medication(dad, "Combiflam", dosage="1 tab", timing=["08:00"])
    dad_view = harness.interactions(dad)
    check(
        "INV-8",
        bool(dad_view["interactions"]) and all(a["profile_id"] == dad for a in dad_view["interactions"]),
        "a single-patient view still finds that patient's own duplicate paracetamol",
        "the single-patient view lost the patient's own finding",
    )


# ---------------------------------------------------------------------------
# INV-9 — acknowledgement round-trip and SOS
# ---------------------------------------------------------------------------

def inv_9_ack_and_sos(harness: Harness) -> None:
    print("\nINV-9  acknowledgements persist and SOS references a real finding")
    profile = harness.add_profile()
    harness.add_medication(profile, "Dolo 650", dosage="650mg", timing=["08:00", "13:00"])
    harness.add_medication(profile, "Combiflam", dosage="1 tab", timing=["08:00"])

    first = harness.interactions(profile)
    alert_id = first["interactions"][0]["id"]
    harness.acknowledge(alert_id)
    second = harness.interactions(profile)
    still_acknowledged = any(
        a["id"] == alert_id and a["acknowledged"] for a in second["interactions"]
    )
    check(
        "INV-9", still_acknowledged,
        "an acknowledged finding stays acknowledged after a refresh",
        "acknowledgements do not survive a refresh",
    )

    check(
        "INV-9", bool(alert_id) and alert_id.startswith(("duplicate_ingredient:", "dose_ceiling:", "interaction:")),
        f"the API issues a stable, documented alert id ({alert_id[:48]}...)",
        f"unusable alert id: {alert_id!r}",
    )

    # SOS must accept the id the API itself issued.
    harness.add_family("Audit daughter", fcm_token="audit-device-token")
    receipt = harness.sos(alert_id)
    check(
        "INV-9", receipt["sent_to"] == 1 and receipt["alert_id"] == alert_id,
        f"SOS delivered to {receipt['sent_to']} contact(s) for alert {alert_id[:32]}...",
        f"SOS did not deliver: {receipt}",
    )

    # A fabricated id must fail loudly, and must not scan other users' devices.
    from fastapi import HTTPException

    try:
        harness.sos("fabricated-alert-id")
        raised = None
    except HTTPException as exc:
        raised = exc
    check(
        "INV-9", raised is not None and raised.status_code == 404,
        "SOS with an id the API never issued returns 404 instead of silently doing nothing",
        f"a fabricated alert id was accepted: {raised}",
    )

    # Delivery receipts must not claim success for a contact with no device.
    no_device = harness.add_family("Audit son")

    # A repeat SOS inside the per-contact window must be reported, not re-sent.
    # The endpoint's own rate limiter is reset first so this tests the window
    # logic rather than the burst limiter.
    harness.main.rate_limit.limiter.reset()
    try:
        harness.sos(alert_id)
        blocked = None
    except HTTPException as exc:
        blocked = exc
    check(
        "INV-9", blocked is not None and blocked.status_code in (409,) and (
            "rate_limited" in blocked.detail or "no_device_linked" in blocked.detail
        ),
        f"a repeat SOS inside the delivery window is reported ({blocked.detail[:80]}...)",
        f"repeat SOS was not reported: {blocked}",
    )

    receipt_obj = harness.send_sos(
        recipients=[{"token": "", "name": "No device", "member_id": no_device}],
        patient_name="Dad", drug_a="Dolo 650", drug_b="Combiflam",
        severity="major", explanation="Duplicate paracetamol", interaction_id=alert_id,
    )
    check(
        "INV-9", receipt_obj["sent"] == 0 and receipt_obj["failed"] == 1
        and receipt_obj["results"][0]["status"] == "no_device_linked",
        "the notification layer reports an unreachable contact instead of counting it as sent",
        f"the delivery receipt is optimistic: {receipt_obj}",
    )


# ---------------------------------------------------------------------------
# INV-10 — tenancy and profile scoping
# ---------------------------------------------------------------------------

def inv_10_tenancy(harness: Harness) -> None:
    print("\nINV-10  access is tenant-scoped and profile-scoped")

    # The device lookup must never be a collection-group scan across tenants.
    from fakes import FakeFirestore

    try:
        FakeFirestore().collection_group("devices")
        guarded = False
    except AssertionError:
        guarded = True
    check(
        "INV-10", guarded,
        "the in-memory Firestore used for verification refuses collection-group scans",
        "the verification database permits cross-tenant scans",
    )

    # Device registration writes a per-user index instead of a global scan.
    asyncio.run(harness.main.register_device(
        harness.main.DevicePayload(fcm_token="tenant-token-1234", platform="android"),
        user_id=AUDIT_USER,
    ))
    indexed = [
        path for path in harness.store.docs if path[:1] == ("device_index",)
    ]
    check(
        "INV-10", len(indexed) == 1 and harness.store.docs[indexed[0]]["user_id"] == AUDIT_USER,
        "device registration writes a token index owned by that user only",
        "device registration did not write a tenant-scoped index",
    )

    # A different tenant sees nothing of the first tenant's data.
    other_store = FakeFirestore()
    first_store = harness.store
    harness.main.db = other_store
    harness.firestore_service.db = other_store
    try:
        other = asyncio.run(
            harness.main.list_profiles(user_id="tenant-b")
        )
    finally:
        harness.main.db = first_store
        harness.firestore_service.db = first_store
    check(
        "INV-10", other["count"] == 0 and other["profiles"] == [],
        "a second tenant sees none of the first tenant's profiles",
        f"cross-tenant read: {other}",
    )

    # profile_id must reach the query, and an unknown profile must 404.
    from fastapi import HTTPException

    profile = harness.add_profile("Scoped")
    harness.add_medication(profile, "Dolo 650", dosage="650mg", timing=["08:00", "13:00"])
    scoped = harness.interactions(profile)
    check(
        "INV-10", scoped["profile_id"] == profile and scoped["coverage"]["medications_total"] == 1,
        "?profile_id= reaches the query and scopes the result",
        f"profile_id was not honoured: {scoped.get('profile_id')}",
    )

    try:
        harness.interactions("profile-that-does-not-exist")
        raised = None
    except HTTPException as exc:
        raised = exc
    check(
        "INV-10", raised is not None and raised.status_code == 404,
        "an unknown profile_id returns 404 rather than an empty, reassuring list",
        f"unknown profile was accepted: {raised}",
    )

    # Health-data access, schedules and emergency alerts must all leave an audit
    # trail. These are produced here so the check does not depend on another
    # invariant having run first.
    harness.add_medication(profile, "Combiflam", dosage="1 tab", frequency_raw="BD",
                           timing=["08:00", "20:00"])
    harness.schedule(profile)
    member = harness.add_family("Audit contact", fcm_token="audit-token-2")
    alert_id = harness.interactions(profile)["interactions"][0]["id"]
    harness.sos(alert_id)
    del member

    import services.audit_service as audit

    events = {e["event"] for e in audit.list_events(AUDIT_USER, limit=500, db=harness.store)}
    for expected in ("interaction.checked", "medication.created", "schedule.generated",
                     "sos.sent", "device.registered"):
        if expected not in events:
            check("INV-10", False, "", f"no audit event recorded for {expected}")
            break
    else:
        check("INV-10", True, f"audit trail present ({len(events)} event types)", "")


# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print(" Medication Orchestra — safety invariant audit")
    print(" Every check below executes the real backend code. A FAIL is a safety")
    print(" property the product claims and the code does not enforce.")
    print("=" * 78)
    install_stubs()
    harness = Harness()

    for invariant in (
        inv_1_knowledge_base,
        inv_2_fail_loud,
        inv_3_exact_identity,
        inv_4_unchecked_is_visible,
        inv_5_duplicate_salt,
        inv_6_schedule,
        inv_7_no_model_in_decisions,
        inv_8_patient_scope,
        inv_9_ack_and_sos,
        inv_10_tenancy,
    ):
        harness.reset()
        invariant(harness)

    failed = [r for r in RESULTS if r[1] == "FAIL"]
    print("\n" + "=" * 78)
    print(f" {len(RESULTS) - len(failed)}/{len(RESULTS)} checks hold — "
          f"{len(failed)} violated")
    print("=" * 78)
    for inv_id, status, detail in RESULTS:
        if status == "FAIL":
            print(f"  violated: {inv_id}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
