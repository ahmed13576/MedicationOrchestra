"""
Medication Orchestra — FastAPI backend.

Auth: Firebase ID tokens (verified with firebase-admin). No API keys are used
for Google Cloud services; ADC supplies credentials in Cloud Run.

Design rules enforced here (see docs/ADVERSARIAL_REVIEW.md):
  * The service refuses to start if the clinical knowledge base is missing or
    too small. There is no degraded mode that answers "no interactions found"
    from an empty database.
  * Every response carries a `coverage` block, so a medication the engine could
    not identify can never be presented as checked-and-clear.
  * The API never returns a bare "safe". The client renders coverage and the
    worst unfixed finding together, always.
  * All clinical decisions come from the deterministic engine. The model only
    rephrases (services/explanation_service.py).
  * Health data access is written to an audit trail.
  * Per-user rate limits bound the cost of the vision and model calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.cloud import firestore
from pydantic import BaseModel, Field

from agents.agent_security import sanitize_text_input
from services import audit_service, consent_service, firestore_service, rate_limit
from services.auth_service import verify_firebase_token
from services.clinical_engine import build_schedule, check_household
from services.explanation_service import SUPPORTED_LANGUAGES, rewrite_findings
from services.image_service import UnsupportedImage, prepare_image
from services.medication_registry import KnowledgeBaseError, get_registry

PROJECT_ID = os.getenv("PROJECT_ID")
if not PROJECT_ID:
    raise RuntimeError(
        "PROJECT_ID is not set. Refusing to start: without it the service cannot "
        "reach Firestore or Vertex AI, and any answer it gave would be wrong."
    )


# ---------------------------------------------------------------------------
# Logging — JSON in production (Cloud Logging), readable in dev
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key in ("user_id", "profile_id", "event"):
            value = getattr(record, key, None)
            if value:
                payload[key] = value
        return json.dumps(payload)


_dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
if _dev_mode:
    logging.basicConfig(level=logging.INFO)
else:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [_handler]

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Medication Orchestra API",
    version="2.0.0",
    description=(
        "Household medication safety. Deterministic clinical checks with a "
        "pharmacist-auditable knowledge base; the language model only phrases "
        "explanations."
    ),
)

# ---------------------------------------------------------------------------
# CORS — explicit origins only. `allow_origins=["*"]` with credentials is both
# invalid per the CORS spec and an open door on a public, unauthenticated host.
# ---------------------------------------------------------------------------

_default_origins = "http://localhost:8080,http://localhost:3000"
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

db = firestore.Client(project=PROJECT_ID)


# ---------------------------------------------------------------------------
# Knowledge base — loaded at import; failure is fatal by design
# ---------------------------------------------------------------------------

try:
    REGISTRY = get_registry()
except KnowledgeBaseError as exc:  # pragma: no cover - startup guard
    logger.critical("Clinical knowledge base unavailable: %s", exc)
    raise RuntimeError(
        "Refusing to start without a clinical knowledge base. An empty or partial "
        "knowledge base produces false 'no interactions found' answers, which is the "
        "one failure mode this product must never have."
    ) from exc


def _guard(user_id: str, key: str) -> None:
    rate_limit.limiter.check(key, user_id)


def _stored_consent(user_id: str) -> dict:
    try:
        doc = _user_ref(user_id).get()
        return (doc.to_dict() or {}).get("consent") or {} if doc.exists else {}
    except Exception:
        # A read failure is never treated as consent.
        return {}


def _require_consent(user_id: str, scope: str) -> None:
    """Refuse the request unless this exact purpose has been agreed to.

    Checked before any health data is read, written or sent to a sub-processor.
    An account with no record is re-prompted, not grandfathered.
    """
    consent_service.require(_stored_consent(user_id), scope)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness + knowledge-base readiness.

    Cloud Run health checks (and the deploy script) must fail when the KB is not
    loaded, so a bad build never receives traffic.
    """
    return {
        "status": "ok",
        "service": "medication-orchestra",
        "version": app.version,
        "decision_engine": "deterministic",
        "knowledge_base": REGISTRY.describe(),
        "review_status": REGISTRY.review_status,
        "model_calls_in_decision_path": 0,
    }


@app.get("/readyz")
async def readyz():
    counts = REGISTRY.describe()
    # Minimum thresholds, not merely "non-empty": a knowledge base with three
    # ingredients would pass a >0 check and still be clinically useless.
    kb_ok = counts["ingredients"] >= 50 and counts["interaction_rules"] >= 10
    checks = {
        "knowledge_base": kb_ok,
        "brand_table": counts["brand_presentations"] >= 20,
        "review_status": bool(REGISTRY.review_status),
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks, "knowledge_base": counts},
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    avatar_color: str = Field(default="#1A73E8", max_length=16)
    age_band: str | None = Field(default=None, max_length=24)
    is_elderly: bool = True
    language: str = Field(default="en", max_length=12)


class AllergyPayload(BaseModel):
    """An allergy as the household recorded it - a belief, not a diagnosis."""
    label: str = Field(min_length=1, max_length=120)
    note: str = Field(default="", max_length=300)
    ingredient_id: str = Field(default="", max_length=80)


class MealTimesPayload(BaseModel):
    """When this household actually eats. Never inferred, only stated."""
    breakfast: str = Field(default="", max_length=5)
    lunch: str = Field(default="", max_length=5)
    dinner: str = Field(default="", max_length=5)


class MedicationPayload(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    brand_name: str = Field(min_length=1, max_length=120)
    generic_name: str = Field(default="", max_length=200)
    dosage: str = Field(default="", max_length=80)
    frequency_raw: str = Field(default="", max_length=40)
    frequency_english: str = Field(default="", max_length=60)
    timing: list[str] = Field(default_factory=list, max_length=8)
    duration: str = Field(default="", max_length=60)
    instruction: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=500)
    # fixed | taper | prn. Anything else falls back to fixed and is disclosed.
    schedule_kind: str = Field(default="", max_length=16)
    # before_food | after_food | with_food | empty_stomach. Anything else is
    # disclosed as unrecognised rather than guessed at.
    food_relation: str = Field(default="", max_length=24)
    taper_steps: list[dict] = Field(default_factory=list, max_length=12)


class ConfirmPayload(BaseModel):
    profile_id: str = Field(min_length=1, max_length=64)
    medications: list[dict] = Field(default_factory=list, max_length=50)
    consent_version: str = Field(default="", max_length=32)


class MedicationEditPayload(BaseModel):
    brand_name: str | None = Field(default=None, max_length=120)
    generic_name: str | None = Field(default=None, max_length=200)
    dosage: str | None = Field(default=None, max_length=80)
    frequency_raw: str | None = Field(default=None, max_length=40)
    frequency_english: str | None = Field(default=None, max_length=60)
    timing: list[str] | None = Field(default=None, max_length=8)
    instruction: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, max_length=16)


class DevicePayload(BaseModel):
    fcm_token: str = Field(min_length=8, max_length=4096)
    platform: str = Field(default="android", max_length=16)


class FamilyMemberPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(default="", max_length=20)
    fcm_token: str = Field(default="", max_length=4096)
    relationship: str = Field(default="", max_length=40)


class SosPayload(BaseModel):
    alert_id: str = Field(min_length=1, max_length=200)
    patient_name: str = Field(default="", max_length=80)


class SettingsPayload(BaseModel):
    sos_rate_limit_hours: int = Field(default=2, ge=1, le=24)
    language: str = Field(default="en", max_length=12)
    preferred_contact: str = Field(default="whatsapp", max_length=16)


class ConsentPayload(BaseModel):
    consent_version: str = Field(min_length=1, max_length=32)
    accepted: bool
    purposes: list[str] = Field(default_factory=list, max_length=10)
    #: For data about someone who is not the app user (a parent, a child):
    #: on whose authority this consent was given.
    authority_note: str = Field(default="", max_length=200)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user_ref(user_id: str):
    return db.collection("users").document(user_id)


def _profile_ref(user_id: str, profile_id: str):
    return _user_ref(user_id).collection("profiles").document(profile_id)


def _medications_ref(user_id: str, profile_id: str):
    return _profile_ref(user_id, profile_id).collection("medications")


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_fields(data: dict, allowed: set[str], max_len: int = 200) -> dict:
    """Sanitise every user-supplied string before it reaches Firestore or a prompt."""
    out = {}
    for key, value in data.items():
        if key not in allowed or value is None:
            continue
        if isinstance(value, str):
            cleaned = sanitize_text_input(value, max_length=max_len)
            out[key] = cleaned
        elif isinstance(value, list):
            out[key] = [
                sanitize_text_input(str(v), max_length=40) if isinstance(v, str) else v
                for v in value
            ][:8]
        else:
            out[key] = value
    return out


def _clean_taper_steps(raw: object) -> list[dict]:
    """Sanitise the stated steps of a reducing course.

    Only the stated steps are stored; nothing is filled in from a neighbour,
    because inventing a step the prescription did not state is a dosing error.
    """
    steps: list[dict] = []
    if not isinstance(raw, list):
        return steps
    for index, step in enumerate(raw[:12], start=1):
        if not isinstance(step, dict):
            continue
        steps.append({
            "step": index,
            "dose": sanitize_text_input(str(step.get("dose") or step.get("dosage") or ""),
                                        max_length=80),
            "duration": sanitize_text_input(str(step.get("duration") or ""), max_length=60),
            "timing": [
                sanitize_text_input(str(t), max_length=10)
                for t in (step.get("timing") or [])[:8] if t
            ],
            "instruction": sanitize_text_input(str(step.get("instruction") or ""),
                                               max_length=200),
        })
    return steps


def _resolve_for_storage(clean: dict) -> dict:
    """Attach the deterministic ingredient resolution to a medication record.

    Resolving at save time (rather than only at check time) means the medicines
    list, the coverage ledger and the interaction check all agree about what a
    brand actually contains, and the user sees the active ingredient immediately
    instead of after a round trip. The model is not involved: this is the
    registry's exact-match lookup.
    """
    resolution = REGISTRY.resolve_medication(clean.get("brand_name"), clean.get("generic_name"))
    if resolution.ingredients:
        names = [i["name"] for i in resolution.to_dict()["ingredients"]]
        if not (clean.get("generic_name") or "").strip():
            clean["generic_name"] = " + ".join(names)
        clean["ingredient_ids"] = [i.ingredient_id for i in resolution.ingredients]
        clean["resolved_by"] = resolution.resolved_by
        clean["resolution_confidence"] = resolution.confidence
    else:
        # Store the record, but mark it as not checked. The coverage ledger
        # reports it, so the app can never present it as safe.
        clean["resolution_confidence"] = "none"
        clean["unresolved_parts"] = list(resolution.unresolved)
    return clean


def _fetch_medications(user_id: str, profile_id: str | None = None) -> list[dict]:
    """Active medications for one profile, or for every profile when None."""
    profiles_ref = _user_ref(user_id).collection("profiles")
    target_profiles = (
        [profile_id] if profile_id and profile_id != "all"
        else [doc.id for doc in profiles_ref.stream()]
    )
    meds: list[dict] = []
    for pid in target_profiles:
        for doc in _medications_ref(user_id, pid).where("status", "==", "active").stream():
            data = doc.to_dict() or {}
            data["id"] = doc.id
            data["profile_id"] = pid
            data.setdefault("brand_name", "")
            data.setdefault("generic_name", "")
            meds.append(data)
    return meds


def _profile_names(user_id: str) -> dict[str, str]:
    try:
        return {
            doc.id: (doc.to_dict() or {}).get("name", "")
            for doc in _user_ref(user_id).collection("profiles").stream()
        }
    except Exception:
        return {}


def _meal_times(user_id: str, profile_id: str) -> dict:
    """Stated meal times for a profile, or nothing. Never a default."""
    try:
        doc = _profile_ref(user_id, profile_id).get()
        return (doc.to_dict() or {}).get("meal_times") or {} if doc.exists else {}
    except Exception:
        return {}


def _allergies_ref(user_id: str, profile_id: str):
    return _profile_ref(user_id, profile_id).collection("allergies")


def _profile_allergies(user_id: str, profile_id: str | None = None) -> dict[str, list[dict]]:
    """Recorded allergies per profile, for the deterministic check.

    A read failure returns nothing recorded, and the engine then reports the
    check as incomplete - it never means "no allergies".
    """
    out: dict[str, list[dict]] = {}
    try:
        ids = [profile_id] if profile_id else list(_profile_names(user_id))
        for pid in ids:
            entries = [
                {"id": doc.id, **(doc.to_dict() or {})}
                for doc in _allergies_ref(user_id, pid).stream()
            ]
            if entries:
                out[pid] = entries
    except Exception:
        return out
    return out


def _acknowledged_ids(user_id: str) -> set[str]:
    """Alert ids the user has already dismissed.

    The previous backend wrote these and never read them back, so every
    acknowledgement silently reverted on the next refresh.
    """
    try:
        return {
            doc.id
            for doc in _user_ref(user_id).collection("acknowledged_interactions").stream()
        }
    except Exception as exc:
        logger.warning("Could not read acknowledgements for %s: %s", user_id, exc)
        return set()


async def _run_check(
    user_id: str,
    profile_id: str | None,
    language: str = "en",
    persist_alerts: bool = True,
) -> dict:
    """Run the deterministic engine and prepare the API payload."""
    meds = _fetch_medications(user_id, profile_id)
    names = _profile_names(user_id)

    if not meds:
        return {
            "alerts": [],
            "unchecked": [],
            "coverage": {
                "medications_total": 0,
                "medications_fully_checked": 0,
                "medications_unchecked": 0,
                "coverage_percent": 0.0,
                "is_complete": False,
                "unmatched_allergies": [],
                "patients": [],
            },
            "unmatched_allergies": [],
            "critical_count": 0,
            "knowledge_base": REGISTRY.describe(),
            "review_status": REGISTRY.review_status,
            "explanation_language": language,
            "message": "No medications found. Scan or add your medicines first.",
        }

    result = check_household(
        meds, names, REGISTRY, _profile_allergies(user_id, profile_id)
    )

    acknowledged = _acknowledged_ids(user_id)
    for alert in result["alerts"]:
        alert["acknowledged"] = alert["id"] in acknowledged

    # Persist the current findings so SOS can reference a real, stable id
    # (the old implementation looked up ids the payload never contained).
    if persist_alerts:
        _persist_alerts(user_id, result["alerts"])

    # Phrase the most important findings in the user's language. Purely
    # cosmetic: severity, facts and citations are already fixed.
    top = [a for a in result["alerts"] if not a["acknowledged"]][:8]
    if top and language != "en":
        rewrites = await rewrite_findings(top, language=language)
        for alert in result["alerts"]:
            improved = rewrites.get(alert["id"])
            if improved:
                alert["title"] = improved["title"]
                alert["detail"] = improved["detail"]
                alert["explanation"] = improved["detail"]

    result["explanation_language"] = language
    result["model_calls_in_decision_path"] = 0
    return result


def _persist_alerts(user_id: str, alerts: list[dict]) -> None:
    try:
        batch = db.batch()
        col = _user_ref(user_id).collection("alerts")
        for alert in alerts:
            doc = col.document(alert["id"])
            batch.set(doc, {
                "alert": alert,
                "profile_id": alert.get("profile_id", ""),
                "severity": alert.get("severity", ""),
                "kind": alert.get("kind", ""),
                "generated_at": _now(),
            })
        batch.commit()
    except Exception as exc:
        logger.warning("Could not persist alerts for %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Skill 2: medication scanning
# ---------------------------------------------------------------------------

@app.post("/api/v1/medications/scan")
async def scan_medication_image(
    request: Request,
    image: UploadFile = File(...),
    profile_id: str = Form(...),
    image_type: str = Form(default="auto"),
    language: str = Form(default="en"),
    user_id: str = Depends(verify_firebase_token),
):
    """Extract medications from a prescription or strip photo. Does not save."""
    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    _guard(user_id, "scan")
    if image_type not in ("auto", "prescription", "blister_pack"):
        raise HTTPException(400, "Invalid image_type. Use: auto, prescription, blister_pack.")
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    raw = await image.read()
    try:
        prepared = prepare_image(raw)
    except UnsupportedImage as exc:
        raise HTTPException(400, str(exc)) from exc

    from services.gemini_service import parse_medication_image

    try:
        medications, detected_type, requires_review = await parse_medication_image(
            prepared.data, image_type, mime_type=prepared.mime_type, language=language,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.error("scan failed for %s: %s", user_id, exc)
        raise HTTPException(
            503,
            "We could not read that prescription right now. Please try again in a "
            "moment - your previous medicines are unaffected.",
        ) from exc

    if not medications:
        raise HTTPException(
            422,
            "We could not find any medicine in that photo. Try again with more light, "
            "or add the medicine by hand.",
        )

    audit_service.record(
        user_id, audit_service.EVENT_SCAN, profile_id=profile_id,
        detail={"image_type": detected_type, "count": len(medications),
                "bytes": len(prepared.data), "notes": prepared.notes},
    )
    return {
        "medications": medications,
        "count": len(medications),
        "profile_id": profile_id,
        "detected_type": detected_type,
        "requires_review": requires_review,
        "image": {
            "width": prepared.width,
            "height": prepared.height,
            "size_kb": prepared.size_kb,
            "metadata_stripped": prepared.re_encoded,
            "notes": prepared.notes,
        },
    }


@app.post("/api/v1/medications/scan-blister")
async def scan_blister_pack(
    image: UploadFile = File(...),
    profile_id: str = Form(...),
    user_id: str = Depends(verify_firebase_token),
):
    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    _guard(user_id, "scan")
    raw = await image.read()
    try:
        prepared = prepare_image(raw)
    except UnsupportedImage as exc:
        raise HTTPException(400, str(exc)) from exc

    from services.gemini_service import parse_blister_pack

    try:
        medications = await parse_blister_pack(prepared.data, mime_type=prepared.mime_type)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.error("blister scan failed for %s: %s", user_id, exc)
        raise HTTPException(
            503, "We could not read that strip right now. Please try again."
        ) from exc

    if not medications:
        raise HTTPException(422, "We could not find medicine information on that strip.")

    audit_service.record(
        user_id, audit_service.EVENT_SCAN, profile_id=profile_id,
        detail={"image_type": "blister_pack", "count": len(medications)},
    )
    return {
        "medications": medications,
        "count": len(medications),
        "profile_id": profile_id,
        "detected_type": "blister_pack",
    }


@app.post("/api/v1/medications/confirm")
async def confirm_medications(
    payload: ConfirmPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Save reviewed medications after the user confirms them."""
    _guard(user_id, "confirm")

    profile_ref = _profile_ref(user_id, payload.profile_id)
    # Firestore stream() only returns existing documents, so the profile document
    # must exist for its medications subcollection to be readable later.
    profile_ref.set({"profile_id": payload.profile_id, "updated_at": _now()}, merge=True)

    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    col = _medications_ref(user_id, payload.profile_id)
    saved_ids: list[str] = []
    for med in payload.medications[:50]:
        med_id = str(uuid.uuid4())
        clean = _clean_fields(
            {k: v for k, v in med.items() if not k.startswith("_")},
            {"brand_name", "generic_name", "dosage", "frequency_raw", "frequency_english",
             "timing", "duration", "condition", "instruction", "notes", "source_type",
             "total_tablets", "expiry_date", "batch_no", "manufacturer", "confidence",
             "schedule_kind", "food_relation"},
            max_len=200,
        )
        if not clean.get("brand_name"):
            continue
        clean = _resolve_for_storage(clean)
        steps = _clean_taper_steps(med.get("taper_steps"))
        if steps:
            clean["taper_steps"] = steps
        clean.update({
            "status": "active",
            "source": clean.get("source_type", "photo"),
            "created_at": _now(),
        })
        col.document(med_id).set(clean)
        saved_ids.append(med_id)

    firestore_service.touch_household_updated_at(user_id)
    audit_service.record(
        user_id, audit_service.EVENT_MEDICATION_CREATED, profile_id=payload.profile_id,
        detail={"count": len(saved_ids), "consent_version": payload.consent_version},
    )
    return {"saved": len(saved_ids), "medication_ids": saved_ids}


@app.post("/api/v1/medications/manual")
async def add_medication_manually(
    payload: MedicationPayload,
    user_id: str = Depends(verify_firebase_token),
):
    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    _guard(user_id, "confirm")
    med_id = str(uuid.uuid4())
    data = _clean_fields(payload.model_dump(), {
        "brand_name", "generic_name", "dosage", "frequency_raw", "frequency_english",
        "timing", "duration", "instruction", "notes", "schedule_kind",
        "food_relation",
    })
    data = _resolve_for_storage(data)
    steps = _clean_taper_steps(payload.taper_steps)
    if steps:
        data["taper_steps"] = steps
    data.update({"status": "active", "source": "manual", "source_type": "manual",
                 "created_at": _now()})

    _profile_ref(user_id, payload.profile_id).set(
        {"profile_id": payload.profile_id, "updated_at": _now()}, merge=True
    )
    _medications_ref(user_id, payload.profile_id).document(med_id).set(data)
    firestore_service.touch_household_updated_at(user_id)
    audit_service.record(
        user_id, audit_service.EVENT_MEDICATION_CREATED, profile_id=payload.profile_id,
        subject_id=med_id, detail={"source": "manual"},
    )
    return {"saved": True, "medication_id": med_id}


# ---------------------------------------------------------------------------
# Profiles & medication CRUD
# ---------------------------------------------------------------------------

@app.get("/api/v1/profiles")
async def list_profiles(user_id: str = Depends(verify_firebase_token)):
    profiles = await firestore_service.get_profiles(user_id)
    return {"profiles": profiles, "count": len(profiles)}


@app.post("/api/v1/profiles")
async def create_profile(
    payload: ProfilePayload,
    user_id: str = Depends(verify_firebase_token),
):
    profile_id = str(uuid.uuid4())
    _profile_ref(user_id, profile_id).set({
        "profile_id": profile_id,
        "name": sanitize_text_input(payload.name, max_length=80),
        "avatar_color": payload.avatar_color,
        "age_band": payload.age_band or "",
        "is_elderly": payload.is_elderly,
        "language": payload.language if payload.language in SUPPORTED_LANGUAGES else "en",
        "created_at": _now(),
    })
    audit_service.record(user_id, audit_service.EVENT_MEDICATION_CREATED,
                         profile_id=profile_id, detail={"action": "profile_created"})
    return {"profile_id": profile_id, "name": payload.name}


@app.delete("/api/v1/profiles/{profile_id}")
async def remove_profile(
    profile_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    await firestore_service.delete_profile(user_id, profile_id)
    audit_service.record(user_id, audit_service.EVENT_DELETION, profile_id=profile_id,
                         detail={"action": "profile_deleted"})
    return {"deleted": profile_id}


@app.post("/api/v1/profiles/{profile_id}/meal-times")
async def set_meal_times(
    profile_id: str,
    payload: MealTimesPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Record when this household eats, so meal instructions can be honoured.

    Without this the scheduler reports a meal instruction as unmet; it never
    assumes a breakfast time.
    """
    if not _profile_ref(user_id, profile_id).get().exists:
        raise HTTPException(404, "Profile not found")
    meals = {
        k: v for k, v in payload.model_dump().items()
        if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(v or "").strip())
    }
    _profile_ref(user_id, profile_id).set({"meal_times": meals}, merge=True)
    audit_service.record(user_id, audit_service.EVENT_MEDICATION_UPDATED,
                         profile_id=profile_id, detail={"action": "meal_times_set"})
    return {"profile_id": profile_id, "meal_times": meals}


@app.get("/api/v1/profiles/{profile_id}/allergies")
async def list_allergies(
    profile_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    entries = _profile_allergies(user_id, profile_id).get(profile_id, [])
    return {"allergies": entries, "count": len(entries)}


@app.post("/api/v1/profiles/{profile_id}/allergies")
async def record_allergy(
    profile_id: str,
    payload: AllergyPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Record an allergy against a profile.

    Stored with who recorded it and when, because it is the household's own
    record. The engine phrases every alert as "you recorded", never as fact.
    """
    if not _profile_ref(user_id, profile_id).get().exists:
        raise HTTPException(404, "Profile not found")
    allergy_id = str(uuid.uuid4())
    entry = {
        "id": allergy_id,
        "label": sanitize_text_input(payload.label, max_length=120),
        "note": sanitize_text_input(payload.note, max_length=300),
        "ingredient_id": sanitize_text_input(payload.ingredient_id, max_length=80).lower(),
        "recorded_by": user_id,
        "recorded_at": _now().isoformat(),
    }
    _allergies_ref(user_id, profile_id).document(allergy_id).set(entry)
    audit_service.record(user_id, audit_service.EVENT_MEDICATION_UPDATED,
                         profile_id=profile_id, detail={"action": "allergy_recorded"})
    return {"allergy": entry}


@app.delete("/api/v1/profiles/{profile_id}/allergies/{allergy_id}")
async def remove_allergy(
    profile_id: str,
    allergy_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    _allergies_ref(user_id, profile_id).document(allergy_id).delete()
    audit_service.record(user_id, audit_service.EVENT_DELETION, profile_id=profile_id,
                         detail={"action": "allergy_removed"})
    return {"deleted": allergy_id}


@app.get("/api/v1/profiles/{profile_id}/medications")
async def list_medications(
    profile_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    meds = await firestore_service.get_medications_for_profile(user_id, profile_id)
    for item in meds:
        resolution = REGISTRY.resolve_medication(item.get("brand_name"), item.get("generic_name"))
        item["ingredients"] = resolution.to_dict()["ingredients"]
        item["resolution"] = {
            "resolved_by": resolution.resolved_by,
            "confidence": resolution.confidence,
            "unresolved": resolution.unresolved,
            "unrecognised_labels": resolution.unrecognised_labels,
        }
    return {"medications": meds, "count": len(meds)}


@app.patch("/api/v1/profiles/{profile_id}/medications/{med_id}")
async def edit_medication(
    profile_id: str,
    med_id: str,
    payload: MedicationEditPayload,
    user_id: str = Depends(verify_firebase_token),
):
    allowed = {"brand_name", "generic_name", "dosage", "frequency_raw",
               "frequency_english", "timing", "instruction", "notes", "status"}
    updates = _clean_fields(payload.model_dump(exclude_none=True), allowed)
    if not updates:
        raise HTTPException(400, "No valid fields to update.")
    await firestore_service.update_medication(user_id, profile_id, med_id, updates)
    audit_service.record(user_id, audit_service.EVENT_MEDICATION_UPDATED,
                         profile_id=profile_id, subject_id=med_id,
                         detail={"fields": sorted(updates)})
    return {"updated": med_id}


@app.post("/api/v1/profiles/{profile_id}/medications/{med_id}/confirm-identity")
async def confirm_medication_identity(
    profile_id: str,
    med_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Record that a person checked this medicine and says the identity is right.

    A medicine read with low confidence is kept out of the timetable until this
    is recorded. The confirmation is evidence about the *human act* only - it is
    stored against the record with who and when, and never treated as evidence
    about the drug itself.
    """
    _guard(user_id, "confirm")
    await firestore_service.update_medication(user_id, profile_id, med_id, {
        "identity_confirmed": True,
        "identity_confirmed_at": _now(),
        "identity_confirmed_by": user_id,
    })
    audit_service.record(
        user_id, audit_service.EVENT_MEDICATION_UPDATED, profile_id=profile_id,
        subject_id=med_id, detail={"action": "identity_confirmed"},
    )
    return {"confirmed": med_id}


@app.delete("/api/v1/profiles/{profile_id}/medications/{med_id}")
async def remove_medication(
    profile_id: str,
    med_id: str,
    user_id: str = Depends(verify_firebase_token),
):

    await firestore_service.delete_medication(user_id, profile_id, med_id)
    firestore_service.touch_household_updated_at(user_id)
    audit_service.record(user_id, audit_service.EVENT_MEDICATION_DELETED,
                         profile_id=profile_id, subject_id=med_id)
    return {"deleted": med_id}


# ---------------------------------------------------------------------------
# Interaction checking
# ---------------------------------------------------------------------------

@app.get("/api/v1/interactions")
async def get_interactions(
    profile_id: str = Query(default="all", max_length=64),
    language: str = Query(default="en", max_length=12),
    user_id: str = Depends(verify_firebase_token),
):
    """Run the deterministic check.

    `profile_id` is honoured: "all" checks each patient independently and never
    pairs two people's medicines. The client's on-device cache is keyed the same
    way, so the two layers finally agree.
    """
    _guard(user_id, "interactions")
    if profile_id != "all":
        profile = _profile_ref(user_id, profile_id).get()
        if not profile.exists:
            raise HTTPException(404, "Profile not found")

    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    result = await _run_check(user_id, None if profile_id == "all" else profile_id, language)
    audit_service.record(
        user_id, audit_service.EVENT_INTERACTION_CHECK,
        profile_id=profile_id, detail={
            "alerts": len(result["alerts"]),
            "critical": result["critical_count"],
            "coverage": result["coverage"]["coverage_percent"],
        },
    )
    return {
        "interactions": result["alerts"],
        "count": len(result["alerts"]),
        "critical_count": result["critical_count"],
        "unchecked": result["unchecked"],
        "unmatched_allergies": result.get("unmatched_allergies", []),
        "coverage": result["coverage"],
        "knowledge_base": result["knowledge_base"],
        "review_status": result["review_status"],
        "explanation_language": result["explanation_language"],
        "profile_id": profile_id,
        "user_id": user_id,
        "decision_engine": "deterministic",
        "model_calls_in_decision_path": result.get("model_calls_in_decision_path", 0),
        **({"message": result["message"]} if "message" in result else {}),
    }


@app.post("/api/v1/interactions/{alert_id}/acknowledge")
async def acknowledge_interaction(
    alert_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Acknowledge (dismiss) an alert. The acknowledgement now persists and is
    merged back into every subsequent check response."""
    if len(alert_id) > 200 or "/" in alert_id:
        raise HTTPException(400, "Invalid alert id")
    _user_ref(user_id).collection("acknowledged_interactions").document(alert_id).set({
        "acknowledged_at": _now(),
        "interaction_id": alert_id,
    })
    audit_service.record(user_id, audit_service.EVENT_ALERT_ACKNOWLEDGED,
                         subject_id=alert_id)
    return {"status": "acknowledged", "interaction_id": alert_id}


@app.delete("/api/v1/interactions/{alert_id}/acknowledge")
async def unacknowledge_interaction(
    alert_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    if len(alert_id) > 200 or "/" in alert_id:
        raise HTTPException(400, "Invalid alert id")
    _user_ref(user_id).collection("acknowledged_interactions").document(alert_id).delete()
    return {"status": "unacknowledged", "interaction_id": alert_id}


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------

@app.post("/api/v1/devices/register")
async def register_device(
    payload: DevicePayload,
    user_id: str = Depends(verify_firebase_token),
):
    token = payload.fcm_token.strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    _user_ref(user_id).collection("devices").document(token_hash).set({
        "token": token,
        "platform": payload.platform,
        "updated_at": _now(),
    }, merge=True)
    # Indexed lookup so notifications never need a collection-group scan across
    # every user's devices (which is both slow and a tenancy boundary violation).
    db.collection("device_index").document(token_hash).set({
        "user_id": user_id,
        "platform": payload.platform,
        "updated_at": _now(),
    }, merge=True)
    audit_service.record(user_id, audit_service.EVENT_DEVICE_REGISTERED,
                         detail={"platform": payload.platform})
    return {"registered": True}


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

@app.post("/api/v1/schedule/generate")
async def generate_schedule_endpoint(
    profile_id: str = Query(default="all", max_length=64),
    language: str = Query(default="en", max_length=12),
    user_id: str = Depends(verify_firebase_token),
):
    """Generate verified daily schedules - one per patient, deterministically."""
    _require_consent(user_id, consent_service.SCOPE_MEDICATION_REVIEW)
    _guard(user_id, "schedule")
    meds = _fetch_medications(user_id, None if profile_id == "all" else profile_id)
    if not meds:
        return {
            "schedules": [], "schedule": None, "coverage": None,
            "message": "No medications found. Scan or add your medicines first.",
        }

    names = _profile_names(user_id)
    check = check_household(meds, names, REGISTRY, _profile_allergies(user_id, profile_id))
    _persist_alerts(user_id, check["alerts"])

    schedules = []
    for pid in sorted({m["profile_id"] for m in meds}):
        patient_meds = [m for m in meds if m["profile_id"] == pid]
        schedule = build_schedule(
            patient_meds, check["alerts"], pid, REGISTRY, _meal_times(user_id, pid)
        )
        schedule["patient_name"] = names.get(pid, "")
        _user_ref(user_id).collection("schedules").document(f"{pid}_{_today()}").set({
            **schedule, "generated_at": _now(), "date": _today(),
        })
        schedules.append(schedule)

    audit_service.record(
        user_id, audit_service.EVENT_SCHEDULE_GENERATED, profile_id=profile_id,
        detail={
            "statuses": [s["schedule_status"] for s in schedules],
            "coverage": check["coverage"]["coverage_percent"],
        },
    )
    return {
        "schedules": schedules,
        "schedule": schedules[0] if len(schedules) == 1 else None,
        "interactions": check["alerts"],
        "interaction_count": len(check["alerts"]),
        "critical_count": check["critical_count"],
        "unchecked": check["unchecked"],
        "coverage": check["coverage"],
        "knowledge_base": check["knowledge_base"],
        "review_status": check["review_status"],
        "model_calls_in_decision_path": 0,
    }


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ---------------------------------------------------------------------------
# Family & SOS
# ---------------------------------------------------------------------------

@app.get("/api/v1/family")
async def list_family_members(user_id: str = Depends(verify_firebase_token)):
    members = await firestore_service.get_family_members(user_id)
    for member in members:
        token = member.get("fcm_token") or ""
        member["has_device"] = bool(token)
        member["fcm_token"] = (token[:6] + "...") if token else ""
    return {"members": members}


@app.post("/api/v1/family")
async def add_family_member(
    payload: FamilyMemberPayload,
    user_id: str = Depends(verify_firebase_token),
):
    _require_consent(user_id, consent_service.SCOPE_CAREGIVER_SHARING)
    member_id = await firestore_service.add_family_member(
        user_id,
        sanitize_text_input(payload.name, max_length=80),
        payload.fcm_token.strip(),
        phone=sanitize_text_input(payload.phone, max_length=20),
        relationship=sanitize_text_input(payload.relationship, max_length=40),
    )
    audit_service.record(user_id, audit_service.EVENT_FAMILY_CHANGED,
                         subject_id=member_id, detail={"action": "added"})
    return {
        "member_id": member_id,
        "name": payload.name,
        "has_device": bool(payload.fcm_token.strip()),
        "note": (
            "" if payload.fcm_token.strip()
            else "No device linked yet. This contact will be skipped when you send an "
                 "SOS until they install the app and accept your invite."
        ),
    }


@app.delete("/api/v1/family/{member_id}")
async def remove_family_member(
    member_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    await firestore_service.delete_family_member(user_id, member_id)
    audit_service.record(user_id, audit_service.EVENT_FAMILY_CHANGED,
                         subject_id=member_id, detail={"action": "removed"})
    return {"deleted": member_id}


@app.post("/api/v1/sos/alert")
async def trigger_sos_alert(
    payload: SosPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Send an emergency alert for a real, persisted finding.

    Changes from the previous implementation:
      * the alert id is validated against a document that actually exists;
      * no cross-user collection-group query is performed;
      * recipients are rate-limited on the *sender's* own record, so no other
        user's document is ever written;
      * the response is a delivery receipt, not a count.
    """
    _require_consent(user_id, consent_service.SCOPE_SOS_CONTACTS)
    _guard(user_id, "sos")
    _guard(user_id, "sos_outbound")

    alert_doc = _user_ref(user_id).collection("alerts").document(payload.alert_id).get()
    if not alert_doc.exists:
        raise HTTPException(
            404,
            "That alert is no longer on file. Open the Interactions screen and try again - "
            "SOS only works for findings currently shown in the app.",
        )
    alert = (alert_doc.to_dict() or {}).get("alert", {})

    from services.notification_service import send_sos_alert

    members = await firestore_service.get_family_members(user_id)
    if not members:
        raise HTTPException(
            409,
            "No family contacts yet. Add at least one contact before using SOS.",
        )

    settings = _user_ref(user_id).get()
    rate_limit_hours = (
        (settings.to_dict() or {}).get("sos_rate_limit_hours", 2) if settings.exists else 2
    )
    now = _now()

    recipients, skipped = [], []
    for member in members:
        last = member.get("last_notified_at")
        if last is not None and getattr(last, "tzinfo", None) is None:
            last = last.replace(tzinfo=UTC)
        if last is not None and (now - last).total_seconds() < rate_limit_hours * 3600:
            skipped.append({"member_id": member.get("member_id"),
                            "name": member.get("name"),
                            "reason": "rate_limited"})
            continue
        token = (member.get("fcm_token") or "").strip()
        if not token:
            skipped.append({"member_id": member.get("member_id"),
                            "name": member.get("name"),
                            "reason": "no_device_linked"})
            continue
        recipients.append({"token": token, "name": member.get("name", ""),
                           "member_id": member.get("member_id")})

    if not recipients:
        raise HTTPException(
            409,
            "None of your family contacts could be reached right now "
            f"({len(skipped)} skipped: "
            + ", ".join(f"{s['name'] or s['member_id']} ({s['reason']})" for s in skipped)
            + ").",
        )

    receipt = send_sos_alert(
        recipients=recipients,
        patient_name=sanitize_text_input(payload.patient_name, max_length=80)
        or (alert.get("patient_name") or "A family member"),
        drug_a=alert.get("med_a_name", ""),
        drug_b=alert.get("med_b_name", ""),
        severity=alert.get("severity", "major"),
        explanation=alert.get("detail", "") or alert.get("explanation", ""),
        interaction_id=payload.alert_id,
    )

    # Record the attempt only for members we actually tried to notify.
    for recipient in recipients:
        try:
            _user_ref(user_id).collection("family_members").document(
                recipient["member_id"]
            ).set({"last_notified_at": now}, merge=True)
        except Exception as exc:
            logger.warning("Could not stamp last_notified_at: %s", exc)

    audit_service.record(
        user_id, audit_service.EVENT_SOS_SENT, subject_id=payload.alert_id,
        detail={"sent": receipt["sent"], "failed": receipt["failed"],
                "skipped": len(skipped)},
    )
    return {
        "sent_to": receipt["sent"],
        "failed": receipt["failed"],
        "skipped": skipped,
        "total_family_members": len(members),
        "alert_id": payload.alert_id,
        "message": (
            f"Alert delivered to {receipt['sent']} of {len(members)} family contact(s)."
            + (f" {len(skipped)} skipped." if skipped else "")
        ),
    }


# ---------------------------------------------------------------------------
# Settings, consent, privacy controls
# ---------------------------------------------------------------------------

@app.get("/api/v1/users/settings")
async def get_user_settings(user_id: str = Depends(verify_firebase_token)):
    doc = _user_ref(user_id).get()
    data = doc.to_dict() or {} if doc.exists else {}
    return {
        "sos_rate_limit_hours": data.get("sos_rate_limit_hours", 2),
        "language": data.get("language", "en"),
        "preferred_contact": data.get("preferred_contact", "whatsapp"),
        "consent": data.get("consent", {}),
        "supported_languages": SUPPORTED_LANGUAGES,
    }


@app.post("/api/v1/users/settings")
async def update_user_settings(
    payload: SettingsPayload,
    user_id: str = Depends(verify_firebase_token),
):
    language = payload.language if payload.language in SUPPORTED_LANGUAGES else "en"
    _user_ref(user_id).set({
        "sos_rate_limit_hours": payload.sos_rate_limit_hours,
        "language": language,
        "preferred_contact": payload.preferred_contact,
    }, merge=True)
    return {
        "sos_rate_limit_hours": payload.sos_rate_limit_hours,
        "language": language,
        "preferred_contact": payload.preferred_contact,
    }


@app.post("/api/v1/users/consent")
async def record_consent(
    payload: ConsentPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Record the user's consent decision, with version and timestamp.

    Prescriptions and medicine lists are the most sensitive category of personal
    data; processing them without a recorded, versioned consent is not defensible
    under India's DPDP Act 2023.
    """
    unknown = [p for p in payload.purposes if p not in consent_service.SCOPES]
    if unknown:
        raise HTTPException(
            400,
            "Unknown consent purpose(s): " + ", ".join(sorted(unknown))
            + ". Known purposes: " + ", ".join(sorted(consent_service.SCOPES)),
        )
    entry = {
        "consent_version": payload.consent_version,
        "accepted": payload.accepted,
        # Withdrawal grants nothing, whatever the body says. Processing stops on
        # the very next request, because every gated endpoint reads this record.
        "purposes": payload.purposes if payload.accepted else [],
        "recorded_by": user_id,
        "authority_note": payload.authority_note,
        "at": _now(),
    }
    if not payload.accepted:
        # Withdrawal starts a retention-limited deletion clock rather than
        # leaving the data in place indefinitely.
        entry["withdrawn_at"] = entry["at"]
        entry["deletion_due_at"] = entry["at"] + timedelta(days=30)
    _user_ref(user_id).set({"consent": entry}, merge=True)
    _user_ref(user_id).collection("consent_history").add(dict(entry))
    audit_service.record(user_id, audit_service.EVENT_CONSENT,
                         detail={"version": payload.consent_version,
                                 "accepted": payload.accepted})
    out = {**entry, "at": entry["at"].isoformat()}
    for key in ("withdrawn_at", "deletion_due_at"):
        if key in out:
            out[key] = out[key].isoformat()
    return {"recorded": True, "consent": out, "known_scopes": consent_service.SCOPES}


@app.get("/api/v1/users/export")
async def export_user_data(
    audit_cursor: str = Query("", max_length=64),
    audit_page_size: int = Query(audit_service.MAX_PAGE_SIZE, ge=1,
                                 le=audit_service.MAX_PAGE_SIZE),
    user_id: str = Depends(verify_firebase_token),
):
    """Data-subject access request: everything we hold, in one JSON document.

    The audit trail can outgrow a single response, so it is paged rather than
    cut off at 500 entries: follow `audit_trail.next_cursor` until it is empty
    and the export is complete. `audit_trail.complete` says, in the document
    itself, whether the caller is holding the whole trail.
    """
    _guard(user_id, "interactions")

    try:
        entries, next_cursor = audit_service.page_events(
            user_id, page_size=audit_page_size, cursor=audit_cursor
        )
    except Exception as exc:
        # A partial export that looks complete is worse than a failed one.
        logger.error("Export failed to read the audit trail for %s: %s", user_id, exc)
        raise HTTPException(
            503, "We could not read the full history, so we did not send a "
                 "partial export. Please try again."
        ) from exc

    profiles = await firestore_service.get_profiles(user_id)
    payload = {
        "exported_at": _now().isoformat(),
        "user_id": user_id,
        "profiles": profiles,
        "medications": {},
        "settings": ( _user_ref(user_id).get().to_dict() or {}),
        "audit_trail": {
            "entries": entries,
            "count": len(entries),
            "next_cursor": next_cursor,
            "complete": not next_cursor,
            "how_to_continue": (
                "Call this endpoint again with audit_cursor set to next_cursor "
                "to receive the rest of the history."
            ) if next_cursor else "",
        },
    }
    for profile in profiles:
        pid = profile.get("profile_id") or profile.get("id")
        if pid:
            payload["medications"][pid] = _fetch_medications(user_id, pid)
    audit_service.record(user_id, audit_service.EVENT_EXPORT)
    return payload



# ── Deletion: every store, or it is not a deletion ───────────────────────────

#: Subcollections hanging off users/{uid}. "audit" is deliberately absent: the
#: record that a deletion happened must survive it (DPDP requires a
#: demonstrable trail) and it holds no health data.
_USER_SUBCOLLECTIONS = (
    "profiles", "alerts", "acknowledged_interactions", "schedules",
    "interactions", "family_members", "devices", "consent_history",
)

#: Subcollections hanging off users/{uid}/profiles/{pid}. Deleting the profile
#: document does NOT delete these - in Firestore a subcollection outlives its
#: parent document, so the medicines of a "deleted" household stayed readable.
_PROFILE_SUBCOLLECTIONS = ("medications", "allergies")


def _delete_all(collection_ref) -> int:
    count = 0
    for doc in collection_ref.stream():
        doc.reference.delete()
        count += 1
    return count


def _erase_everything(user_id: str) -> dict[str, int]:
    """Delete every store that holds this household's data. Returns the counts."""
    deleted: dict[str, int] = {}

    # Profiles first, reaching into their subcollections before the parent goes.
    profile_ids = [doc.id for doc in _user_ref(user_id).collection("profiles").stream()]
    for name in _PROFILE_SUBCOLLECTIONS:
        total = 0
        for pid in profile_ids:
            total += _delete_all(_profile_ref(user_id, pid).collection(name))
        deleted[name] = total

    for name in _USER_SUBCOLLECTIONS:
        deleted[name] = _delete_all(_user_ref(user_id).collection(name))

    # The device index is a top-level collection keyed by token hash; it maps a
    # device back to this user, so leaving it behind leaves an identifier behind.
    index_removed = 0
    for doc in db.collection("device_index").where("user_id", "==", user_id).stream():
        doc.reference.delete()
        index_removed += 1
    deleted["device_index"] = index_removed

    # The user document itself keeps only the tombstone and the withdrawn
    # consent; every other field is health-adjacent settings and goes.
    _user_ref(user_id).set({
        "deleted_at": _now(),
        "consent": {"accepted": False, "consent_version": "withdrawn", "purposes": []},
    })
    return deleted


def _remaining_health_data(user_id: str) -> dict[str, int]:
    """Re-read every store after the delete. Anything left is a failed deletion."""
    left: dict[str, int] = {}
    for pid in [doc.id for doc in _user_ref(user_id).collection("profiles").stream()]:
        for name in _PROFILE_SUBCOLLECTIONS:
            n = len(list(_profile_ref(user_id, pid).collection(name).stream()))
            if n:
                left[f"profiles/{name}"] = left.get(f"profiles/{name}", 0) + n
    for name in _USER_SUBCOLLECTIONS:
        n = len(list(_user_ref(user_id).collection(name).stream()))
        if n:
            left[name] = n
    n = len(list(db.collection("device_index").where("user_id", "==", user_id).stream()))
    if n:
        left["device_index"] = n
    return left


@app.delete("/api/v1/users/data")
async def delete_user_data(
    confirm: str = Query(..., max_length=32),
    user_id: str = Depends(verify_firebase_token),
):
    """Delete all household health data. Requires an explicit confirmation token
    so an accidental tap cannot erase a medication history."""
    if confirm != "DELETE_MY_DATA":
        raise HTTPException(
            400, "Pass confirm=DELETE_MY_DATA to erase all data for this account."
        )
    try:
        deleted = _erase_everything(user_id)
        remaining = _remaining_health_data(user_id)
    except Exception as exc:
        logger.error("Data deletion failed for %s: %s", user_id, exc)
        raise HTTPException(
            500, "We could not complete the deletion. Please contact support."
        ) from exc

    if remaining:
        # Reporting a deletion that did not happen is the one outcome worse
        # than failing it, so the leftovers are named rather than swallowed.
        logger.error("Data deletion left documents behind for %s: %s", user_id, remaining)
        audit_service.record(user_id, audit_service.EVENT_DELETION,
                             detail={"documents": sum(deleted.values()),
                                     "complete": False, "remaining": remaining})
        raise HTTPException(500, {
            "error": "deletion_incomplete",
            "message": "Some of your data could not be erased. Support has been "
                       "alerted; nothing is being reported as deleted that is not.",
            "remaining": remaining,
        })

    audit_service.record(user_id, audit_service.EVENT_DELETION,
                         detail={"documents": sum(deleted.values()),
                                 "complete": True, "by_store": deleted})
    return {
        "deleted_documents": sum(deleted.values()),
        "by_store": deleted,
        "stores_checked": sorted(_USER_SUBCOLLECTIONS + _PROFILE_SUBCOLLECTIONS
                                 + ("device_index",)),
        "audit_trail_retained": True,
        "message": "All health data erased.",
    }
