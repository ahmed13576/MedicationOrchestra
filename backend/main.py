"""
Medication Orchestra — FastAPI Backend
Cloud Run service orchestrating the ADK multi-agent pipeline.

Auth: Google Cloud ADC (Application Default Credentials)
Project: project-f9540f8f-d01e-47d3-a36
"""

from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import firestore
import os
import uuid
import json
import logging
import sys
from datetime import datetime
from services.auth_service import verify_firebase_token
from services.gemini_service import parse_medication_image, parse_blister_pack

# ---------------------------------------------------------------------------
# Logging — JSON in production (Cloud Logging), plain text in dev
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON so Cloud Logging can auto-parse
    the 'severity' and 'message' fields and make them filterable in Log Explorer.
    """
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


_dev_mode = os.getenv("DEV_MODE", "false").lower() == "true"
if _dev_mode:
    # Readable plain-text logs for local development
    logging.basicConfig(level=logging.INFO)
else:
    # Structured JSON for Cloud Logging (production)
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(_JsonFormatter())
    logging.root.setLevel(logging.INFO)
    logging.root.handlers = [_handler]

logger = logging.getLogger(__name__)


app = FastAPI(
    title="Medication Orchestra API",
    version="1.0.0",
    description="Household medication safety agent — powered by Gemini 2.5 Flash + ADK",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Firestore client — uses ADC automatically
db = firestore.Client(project="project-f9540f8f-d01e-47d3-a36")

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "medication-orchestra"}


# ---------------------------------------------------------------------------
# Skill 2: Medication scanning (prescription + blister pack)
# ---------------------------------------------------------------------------

@app.post("/api/v1/medications/scan")
async def scan_medication_image(
    image: UploadFile = File(...),
    profile_id: str = Form(...),
    image_type: str = Form(default="auto"),  # "auto" | "prescription" | "blister_pack"
    user_id: str = Depends(verify_firebase_token),
):
    """
    Scan a prescription photo or medicine blister pack image.
    Auto-detects image type unless explicitly specified.
    Returns extracted medications for user review — does NOT save yet.
    """
    if image_type not in ("auto", "prescription", "blister_pack"):
        raise HTTPException(400, f"Invalid image_type '{image_type}'. Use: auto, prescription, blister_pack")

    image_bytes = await image.read()

    # Validate size (max 10MB)
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large. Maximum size is 10MB.")

    try:
        medications, detected_type, requires_review = await parse_medication_image(image_bytes, image_type)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"scan_medication_image error: {e}")
        raise HTTPException(500, "Failed to process image. Please try again.")

    if not medications:
        raise HTTPException(422, "No medications found in image. Try a clearer photo.")

    return {
        "medications": medications,
        "count": len(medications),
        "profile_id": profile_id,
        "detected_type": detected_type,
        "requires_review": requires_review,
    }


@app.post("/api/v1/medications/scan-blister")
async def scan_blister_pack(
    image: UploadFile = File(...),
    profile_id: str = Form(...),
    user_id: str = Depends(verify_firebase_token),
):
    """
    Convenience endpoint — forces blister pack mode (no auto-detection).
    Use this from the 'Scan Medicine Pack' button in Flutter.
    """
    image_bytes = await image.read()

    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Image too large. Maximum size is 10MB.")

    try:
        medications = await parse_blister_pack(image_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"scan_blister_pack error: {e}")
        raise HTTPException(500, "Failed to process image. Please try again.")

    if not medications:
        raise HTTPException(422, "No medication information found on blister pack.")

    return {
        "medications": medications,
        "count": len(medications),
        "profile_id": profile_id,
        "detected_type": "blister_pack",
    }


@app.post("/api/v1/medications/confirm")
async def confirm_medications(
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """
    Save confirmed medications to Firestore after user reviews the scan results.
    source field is set from source_type on each medication (photo/blister_pack/manual).
    """
    profile_id = payload.get("profile_id")
    medications = payload.get("medications", [])

    if not profile_id:
        raise HTTPException(400, "profile_id is required")
    if not medications:
        raise HTTPException(400, "medications list is empty")

    saved_ids = []

    # Ensure the profile *document* exists — Firestore stream() only returns documents
    # that physically exist, not implicit parents of subcollections. Without this,
    # get_household_medications() always returns [] (the 0-medication bug).
    profile_doc_ref = (
        db.collection("users")
        .document(user_id)
        .collection("profiles")
        .document(profile_id)
    )
    profile_doc_ref.set(
        {"profile_id": profile_id, "updated_at": datetime.utcnow()},
        merge=True,  # won't overwrite user-set name/avatar_color
    )

    base_ref = (
        db.collection("users")
        .document(user_id)
        .collection("profiles")
        .document(profile_id)
        .collection("medications")
    )

    for med in medications:
        med_id = str(uuid.uuid4())
        # Strip internal scoring keys — never persisted to Firestore
        med_clean = {k: v for k, v in med.items() if not k.startswith("_")}
        med_data = {
            **med_clean,
            "status": "active",
            "source": med_clean.get("source_type", "photo"),
            "created_at": datetime.utcnow(),
        }
        base_ref.document(med_id).set(med_data)
        saved_ids.append(med_id)

    from services.firestore_service import touch_household_updated_at

    logger.info(f"Saved {len(saved_ids)} medications for user {user_id}, profile {profile_id}")
    # Invalidate the interaction and schedule caches — agents must re-run
    touch_household_updated_at(user_id)
    return {"saved": len(saved_ids), "med_ids": saved_ids}


@app.post("/api/v1/medications/manual")
async def add_medication_manually(
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """
    Add a single medication manually (no photo required).
    """
    profile_id = payload.get("profile_id")
    medication = payload.get("medication", {})

    if not profile_id:
        raise HTTPException(400, "profile_id is required")
    if not medication.get("brand_name"):
        raise HTTPException(400, "medication.brand_name is required")

    med_id = str(uuid.uuid4())
    med_data = {
        **medication,
        "status": "active",
        "source": "manual",
        "source_type": "manual",
        "created_at": datetime.utcnow(),
    }

    (
        db.collection("users")
        .document(user_id)
        .collection("profiles")
        .document(profile_id)
        .collection("medications")
        .document(med_id)
        .set(med_data)
    )

    from services.firestore_service import touch_household_updated_at
    touch_household_updated_at(user_id)
    return {"med_id": med_id}


# ---------------------------------------------------------------------------
# Skill 4-pre: Profile & Medication Management (CRUD)
# ---------------------------------------------------------------------------

@app.get("/api/v1/profiles")
async def list_profiles(user_id: str = Depends(verify_firebase_token)):
    """Return all household profiles for the authenticated user."""
    from services.firestore_service import get_profiles
    profiles = await get_profiles(user_id)
    return {"profiles": profiles, "count": len(profiles)}


@app.post("/api/v1/profiles")
async def create_profile(
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """Create a new household profile (e.g. 'Mum', 'Dad', 'Child')."""
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "profile name is required")
    profile_id = str(uuid.uuid4())
    db.collection("users").document(user_id).collection("profiles").document(profile_id).set({
        "profile_id": profile_id,
        "name": name,
        "avatar_color": payload.get("avatar_color", "#1A73E8"),
        "created_at": datetime.utcnow(),
    })
    logger.info(f"Created profile '{name}' ({profile_id}) for user {user_id}")
    return {"profile_id": profile_id, "name": name}


@app.delete("/api/v1/profiles/{profile_id}")
async def remove_profile(
    profile_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Delete a profile and all its medications (cascading)."""
    from services.firestore_service import delete_profile
    await delete_profile(user_id, profile_id)
    return {"deleted": profile_id}


@app.get("/api/v1/profiles/{profile_id}/medications")
async def list_medications(
    profile_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Return all active medications for a specific profile."""
    from services.firestore_service import get_medications_for_profile
    meds = await get_medications_for_profile(user_id, profile_id)
    return {"medications": meds, "count": len(meds)}


@app.patch("/api/v1/profiles/{profile_id}/medications/{med_id}")
async def edit_medication(
    profile_id: str,
    med_id: str,
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """Edit specific fields of a saved medication (brand_name, generic_name, dosage, etc.)."""
    from services.firestore_service import update_medication
    allowed_keys = {"brand_name", "generic_name", "dosage", "frequency_english", "timing", "notes"}
    updates = {k: v for k, v in payload.items() if k in allowed_keys}
    if not updates:
        raise HTTPException(400, "No valid fields to update. Allowed: brand_name, generic_name, dosage, frequency_english, timing, notes")
    await update_medication(user_id, profile_id, med_id, updates)
    return {"updated": med_id}


@app.delete("/api/v1/profiles/{profile_id}/medications/{med_id}")
async def remove_medication(
    profile_id: str,
    med_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Soft-delete a medication (sets status=inactive, keeps the document for audit)."""
    from services.firestore_service import delete_medication, touch_household_updated_at
    await delete_medication(user_id, profile_id, med_id)
    touch_household_updated_at(user_id)
    return {"deleted": med_id}



# ---------------------------------------------------------------------------
# Skill 3: Drug Interaction Checker (RAG + SearchGroundingAgent + Gemini)
# ---------------------------------------------------------------------------

from fastapi import Query

@app.get("/api/v1/interactions")
async def get_interactions(
    profile_id: str = Query(default="default"),
    user_id: str = Depends(verify_firebase_token),
):
    """
    Run the household interaction check for the authenticated user.
    Fetches all active medications, enriches missing generics via Google Search
    grounding, then checks every unique drug pair against the DDI corpus.
    Returns interactions sorted by severity (contraindicated → major → moderate).
    """
    # Lazy import to avoid circular imports at startup
    from agents.interaction_checker_agent import check_household_interactions
    try:
        interactions = await check_household_interactions(user_id)
        return {
            "interactions": interactions,
            "count": len(interactions),
            "user_id": user_id,
        }
    except Exception as e:
        logger.error(f"Interaction check failed for {user_id}: {e}")
        raise HTTPException(500, f"Interaction check failed: {str(e)}")


@app.post("/api/v1/interactions/{interaction_id}/acknowledge")
async def acknowledge_interaction(
    interaction_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """
    Mark an interaction alert as acknowledged (dismissed) by the user.
    Stored in Firestore so the Flutter app can persist dismissals.
    """
    db.collection("users").document(user_id)\
      .collection("acknowledged_interactions").document(interaction_id)\
      .set({
          "acknowledged_at": datetime.utcnow(),
          "interaction_id": interaction_id,
      })
    logger.info(f"Interaction {interaction_id} acknowledged by {user_id}")
    return {"status": "acknowledged", "interaction_id": interaction_id}


# ---------------------------------------------------------------------------
# Phase 5: Device token registration (FCM)
# ---------------------------------------------------------------------------

@app.post("/api/v1/devices/register")
async def register_device(
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """Register or refresh a device FCM token for the authenticated user."""
    import hashlib
    token = (payload.get("fcm_token") or "").strip()
    platform = payload.get("platform", "android")
    if not token:
        raise HTTPException(400, "fcm_token is required")
    token_hash = hashlib.md5(token.encode()).hexdigest()
    db.collection("users").document(user_id)\
      .collection("devices").document(token_hash).set({
          "token": token,
          "platform": platform,
          "updated_at": datetime.utcnow(),
      }, merge=True)
    logger.info(f"Device registered for user {user_id} ({platform})")
    return {"registered": True}


# ---------------------------------------------------------------------------
# Phase 5: ADK schedule generation
# ---------------------------------------------------------------------------

@app.post("/api/v1/schedule/generate")
async def generate_schedule_endpoint(
    user_id: str = Depends(verify_firebase_token),
):
    """
    Run the full medication orchestra pipeline:
    fetch meds → check interactions → generate safe daily schedule.
    Returns the generated schedule + interaction summary.
    """
    from agents.orchestrator import run_pipeline
    try:
        result = await run_pipeline(user_id)
        return result
    except Exception as e:
        logger.error(f"Schedule generation failed for {user_id}: {e}")
        raise HTTPException(500, f"Schedule generation failed: {str(e)}")


# ---------------------------------------------------------------------------
# Phase 5: Family member management
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


class _FamilyMemberPayload(_BaseModel):
    name: str
    fcm_token: str


@app.get("/api/v1/family")
async def list_family_members(user_id: str = Depends(verify_firebase_token)):
    """List all registered family members for the authenticated user."""
    from services.firestore_service import get_family_members
    members = await get_family_members(user_id)
    return {"members": members}


@app.post("/api/v1/family")
async def add_family_member(
    payload: _FamilyMemberPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """Register a new family member SOS contact."""
    from services.firestore_service import add_family_member as _add_family_member
    if not payload.name.strip():
        raise HTTPException(400, "name is required")
    if not payload.fcm_token.strip():
        raise HTTPException(400, "fcm_token is required")
    member_id = await _add_family_member(user_id, payload.name.strip(), payload.fcm_token.strip())
    return {"member_id": member_id, "name": payload.name}


@app.delete("/api/v1/family/{member_id}")
async def remove_family_member(
    member_id: str,
    user_id: str = Depends(verify_firebase_token),
):
    """Remove a family member from SOS contacts."""
    from services.firestore_service import delete_family_member
    await delete_family_member(user_id, member_id)
    return {"deleted": member_id}


# ---------------------------------------------------------------------------
# Phase 5: SOS Emergency Alert
# ---------------------------------------------------------------------------

class _SosPayload(_BaseModel):
    interaction_id: str
    patient_name: str = "A family member"


@app.post("/api/v1/sos/alert")
async def trigger_sos_alert(
    payload: _SosPayload,
    user_id: str = Depends(verify_firebase_token),
):
    """
    Send an emergency SOS push notification to ALL registered family members.
    Requires the interaction_id of the critical interaction to alert about.
    """
    from services.notification_service import send_sos_alert
    from services.firestore_service import get_family_members
    from datetime import timezone

    # Fetch the interaction document
    interaction_doc = (
        db.collection("users").document(user_id)
          .collection("interactions").document(payload.interaction_id).get()
    )
    if not interaction_doc.exists:
        raise HTTPException(404, "Interaction not found")
    interaction = interaction_doc.to_dict()

    # Get family member FCM tokens
    members = await get_family_members(user_id)
    family_tokens = [m["fcm_token"] for m in members if m.get("fcm_token")]

    if not family_tokens:
        raise HTTPException(
            404,
            "No family members configured. Add family contacts first via /api/v1/family."
        )

    # Filter family tokens based on recipient rate limit settings
    allowed_tokens = []
    skipped_count = 0
    
    for token in family_tokens:
        try:
            # Query the user who owns this device token
            devices = db.collection_group("devices").where("token", "==", token).limit(1).get()
            if devices:
                recipient_ref = devices[0].reference.parent.parent
                recipient_doc = recipient_ref.get()
                if recipient_doc.exists:
                    rec_data = recipient_doc.to_dict()
                    last_received = rec_data.get("last_sos_received_at")
                    limit_hours = rec_data.get("sos_rate_limit_hours", 2)
                    
                    if last_received:
                        now_tz = datetime.now(timezone.utc)
                        if last_received.tzinfo is None:
                            last_received = last_received.replace(tzinfo=timezone.utc)
                        
                        elapsed = now_tz - last_received
                        if elapsed.total_seconds() < limit_hours * 3600:
                            logger.info(f"Skipping SOS push to recipient {recipient_ref.id} due to rate limit ({limit_hours}h)")
                            skipped_count += 1
                            continue
                            
                    # Update the recipient's last received timestamp
                    recipient_ref.set({"last_sos_received_at": datetime.utcnow()}, merge=True)
            
            allowed_tokens.append(token)
        except Exception as e:
            logger.error(f"Error checking rate limit for token: {e}")
            # Fallback: if error, allow sending
            allowed_tokens.append(token)

    if not allowed_tokens:
        raise HTTPException(
            429,
            f"All family members are currently rate-limited. (Skipped {skipped_count} alert(s))"
        )

    sent_count = send_sos_alert(
        family_tokens=allowed_tokens,
        patient_name=payload.patient_name,
        drug_a=interaction.get("med_a_name", "Drug A"),
        drug_b=interaction.get("med_b_name", "Drug B"),
        severity=interaction.get("severity", "major"),
        explanation=interaction.get("explanation", ""),
        interaction_id=payload.interaction_id,
    )
    logger.info(
        f"SOS alert sent to {sent_count}/{len(family_tokens)} family members for user {user_id} (Skipped {skipped_count} rate-limited)"
    )
    
    response_msg = f"Alert sent to {sent_count} family member(s)"
    if skipped_count > 0:
        response_msg += f" ({skipped_count} skipped due to rate limit)"
        
    return {
        "sent_to": sent_count,
        "total_family_members": len(family_tokens),
        "message": response_msg,
    }


# ---------------------------------------------------------------------------
# Phase 7: User settings
# ---------------------------------------------------------------------------

@app.get("/api/v1/users/settings")
async def get_user_settings(user_id: str = Depends(verify_firebase_token)):
    """Retrieve settings for the authenticated user."""
    doc = db.collection("users").document(user_id).get()
    if doc.exists:
        data = doc.to_dict()
        return {"sos_rate_limit_hours": data.get("sos_rate_limit_hours", 2)}
    return {"sos_rate_limit_hours": 2}


@app.post("/api/v1/users/settings")
async def update_user_settings(
    payload: dict,
    user_id: str = Depends(verify_firebase_token),
):
    """Update settings (e.g. SOS alert rate limit) for the authenticated user."""
    limit = payload.get("sos_rate_limit_hours", 2)
    db.collection("users").document(user_id).set({
        "sos_rate_limit_hours": limit,
    }, merge=True)
    logger.info(f"Updated settings for user {user_id}: sos_rate_limit_hours={limit}")
    return {"sos_rate_limit_hours": limit}
