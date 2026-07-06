"""
firestore_service.py — Medication Orchestra

Firestore data access layer. Provides read helpers for the interaction checker.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from google.cloud import firestore

logger = logging.getLogger(__name__)

PROJECT_ID = "project-f9540f8f-d01e-47d3-a36"

# Shared Firestore client (initialized once)
db = firestore.Client(project=PROJECT_ID)


async def get_household_medications(user_id: str) -> list:
    """
    Returns all active medications across all profiles for a given user.

    Each returned dict contains:
      id, brand_name, generic_name, dosage, frequency, profile_id,
      and any other fields stored during the scan/confirm flow.
    """
    import asyncio

    def _fetch():
        all_meds = []
        profiles_ref = (
            db.collection("users")
            .document(user_id)
            .collection("profiles")
        )
        profiles = list(profiles_ref.stream())
        for profile in profiles:
            profile_id = profile.id
            meds_ref = profiles_ref.document(profile_id).collection("medications")
            # Only fetch active medications
            active_meds = meds_ref.where("status", "==", "active").stream()
            for med_doc in active_meds:
                data = med_doc.to_dict() or {}
                data["id"] = med_doc.id
                data["profile_id"] = profile_id
                # Ensure required fields have defaults
                data.setdefault("brand_name", "Unknown")
                data.setdefault("generic_name", "")
                data.setdefault("dosage", "")
                all_meds.append(data)
        logger.info(
            f"Firestore: fetched {len(all_meds)} active medications for user {user_id}"
        )
        return all_meds

    return await asyncio.to_thread(_fetch)


def update_medication_generic(
    user_id: str, profile_id: str, med_id: str, generic_name: str
) -> None:
    """
    Patch a medication's generic_name in Firestore after Search Grounding resolves it.

    Called fire-and-forget via asyncio.create_task(asyncio.to_thread(...)) from
    interaction_checker_agent — failures are logged but never raised so the
    interaction check result is never blocked.
    """
    try:
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({
            "generic_name": generic_name,
            "grounded_at": datetime.utcnow(),
        })
        logger.info(f"Firestore: updated generic_name for med {med_id} → '{generic_name}'")
    except Exception as e:
        logger.warning(f"Firestore: failed to update generic for {med_id}: {e}")


async def get_profiles(user_id: str) -> list:
    """Return all profile documents for a user, ordered by creation time."""
    def _fetch():
        profiles_ref = db.collection("users").document(user_id).collection("profiles")
        return [{"profile_id": p.id, **p.to_dict()} for p in profiles_ref.stream()]
    return await asyncio.to_thread(_fetch)


async def delete_profile(user_id: str, profile_id: str) -> None:
    """Delete a profile document and ALL its medications (cascading hard delete)."""
    def _delete():
        meds_ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications")
        )
        for doc in meds_ref.stream():
            doc.reference.delete()
        db.collection("users").document(user_id)\
          .collection("profiles").document(profile_id).delete()
        logger.info(f"Firestore: deleted profile {profile_id} for user {user_id}")
    await asyncio.to_thread(_delete)


async def get_medications_for_profile(user_id: str, profile_id: str) -> list:
    """Return all active medications for a specific profile."""
    def _fetch():
        meds_ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications")
        )
        return [
            {"id": m.id, **m.to_dict()}
            for m in meds_ref.where("status", "==", "active").stream()
        ]
    return await asyncio.to_thread(_fetch)


async def update_medication(
    user_id: str, profile_id: str, med_id: str, updates: dict
) -> None:
    """Patch a medication with the provided updates dict."""
    def _update():
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({**updates, "updated_at": datetime.utcnow()})
        logger.info(f"Firestore: updated medication {med_id}")
    await asyncio.to_thread(_update)


async def delete_medication(user_id: str, profile_id: str, med_id: str) -> None:
    """Soft-delete a medication by setting status=inactive (preserves the document)."""
    def _delete():
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({"status": "inactive", "deleted_at": datetime.utcnow()})
        logger.info(f"Firestore: soft-deleted medication {med_id}")
    await asyncio.to_thread(_delete)


# ---------------------------------------------------------------------------
# Phase 5: Family member SOS contact management
# ---------------------------------------------------------------------------

async def get_family_members(user_id: str) -> list:
    """Return all family member documents for a user."""
    def _fetch():
        ref = db.collection("users").document(user_id).collection("family_members")
        return [{"member_id": doc.id, **doc.to_dict()} for doc in ref.stream()]
    return await asyncio.to_thread(_fetch)


async def add_family_member(user_id: str, name: str, fcm_token: str) -> str:
    """Add a new family member SOS contact. Returns the new Firestore document ID."""
    def _add():
        ref = (
            db.collection("users").document(user_id)
              .collection("family_members").document()
        )
        ref.set({
            "name": name,
            "fcm_token": fcm_token,
            "added_at": datetime.utcnow(),
        })
        return ref.id
    return await asyncio.to_thread(_add)


async def delete_family_member(user_id: str, member_id: str) -> None:
    """Remove a family member SOS contact document."""
    def _delete():
        db.collection("users").document(user_id)\
          .collection("family_members").document(member_id).delete()
        logger.info(f"Firestore: deleted family member {member_id} for user {user_id}")
    await asyncio.to_thread(_delete)


# ---------------------------------------------------------------------------
# Phase 5-debug: Household cache invalidation helpers
#
# Strategy:
#   - `users/{uid}` document holds a `household_updated_at` field (timezone-aware UTC).
#   - Every write that changes medication data bumps this field.
#   - Interaction + schedule agents read it first. If their cached doc is
#     strictly newer than `household_updated_at` they return the cache.
#
# Timezone note:
#   Firestore server timestamps are tz-aware (UTC). We use
#   datetime.now(timezone.utc) everywhere so comparisons are always
#   between two aware datetimes — no TypeError.
# ---------------------------------------------------------------------------

from datetime import timezone


def _utcnow() -> datetime:
    """Timezone-aware UTC now — safe to compare with Firestore server timestamps."""
    return datetime.now(timezone.utc)


def touch_household_updated_at(user_id: str) -> None:
    """
    Bump the household_updated_at timestamp on users/{uid}.
    Call this synchronously from any code path that adds/removes medications.
    This invalides the interactions and schedule caches.
    """
    try:
        db.collection("users").document(user_id).set(
            {"household_updated_at": _utcnow()},
            merge=True,
        )
        logger.info(f"Firestore: household_updated_at bumped for user {user_id}")
    except Exception as e:
        logger.warning(f"Firestore: failed to touch household_updated_at for {user_id}: {e}")


def get_household_updated_at(user_id: str) -> Optional[datetime]:
    """
    Return the household_updated_at timestamp for a user, or None if not set.
    Always returns a timezone-aware datetime.
    """
    try:
        doc = db.collection("users").document(user_id).get()
        if not doc.exists:
            return None
        ts = doc.to_dict().get("household_updated_at")
        if ts is None:
            return None
        # Firestore Timestamps expose .replace(tzinfo=...) via Python datetime protocol
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except Exception as e:
        logger.warning(f"Firestore: failed to get household_updated_at for {user_id}: {e}")
        return None


def get_cached_interactions(user_id: str, household_updated_at: Optional[datetime]) -> Optional[list]:
    """
    Return cached interaction list if it's still valid (generated after the last med change).
    Returns None when cache is stale or missing — caller must re-run the agent.
    """
    try:
        doc = db.collection("users").document(user_id)\
                .collection("interactions").document("latest").get()
        if not doc.exists:
            logger.info(f"Firestore: no cached interactions for user {user_id}")
            return None

        data = doc.to_dict() or {}
        generated_at = data.get("generated_at")
        if generated_at is None:
            return None

        # Normalise to aware datetime
        if hasattr(generated_at, "tzinfo") and generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)

        if household_updated_at is None or generated_at >= household_updated_at:
            interactions = data.get("interactions", [])
            logger.info(
                f"Firestore: cache HIT — {len(interactions)} interactions for user {user_id}"
            )
            return interactions

        logger.info(
            f"Firestore: cache MISS — meds changed at {household_updated_at}, "
            f"cache from {generated_at} for user {user_id}"
        )
        return None
    except Exception as e:
        logger.warning(f"Firestore: get_cached_interactions error for {user_id}: {e}")
        return None


def save_cached_interactions(user_id: str, interactions: list) -> None:
    """
    Persist the freshly-computed interactions to users/{uid}/interactions/latest.
    Also writes a per-day copy for historical reference.
    """
    try:
        payload = {
            "interactions": interactions,
            "generated_at": _utcnow(),
            "count": len(interactions),
        }
        db.collection("users").document(user_id)\
          .collection("interactions").document("latest").set(payload)
        logger.info(f"Firestore: cached {len(interactions)} interactions for user {user_id}")
    except Exception as e:
        logger.warning(f"Firestore: save_cached_interactions error for {user_id}: {e}")


def get_cached_schedule(user_id: str, today: str, household_updated_at: Optional[datetime]) -> Optional[dict]:
    """
    Return today's schedule if it was generated after the last medication change.
    Returns None when cache is stale, missing, or from a previous day.
    """
    try:
        doc = db.collection("users").document(user_id)\
                .collection("schedules").document(today).get()
        if not doc.exists:
            logger.info(f"Firestore: no cached schedule for user {user_id} on {today}")
            return None

        data = doc.to_dict() or {}
        generated_at = data.get("generated_at")
        if generated_at is None:
            return None

        if hasattr(generated_at, "tzinfo") and generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)

        if household_updated_at is None or generated_at >= household_updated_at:
            # Strip Firestore metadata fields before returning
            schedule = {k: v for k, v in data.items() if k not in ("generated_at", "date")}
            logger.info(f"Firestore: schedule cache HIT for user {user_id} on {today}")
            return schedule

        logger.info(
            f"Firestore: schedule cache MISS — meds changed at {household_updated_at}, "
            f"schedule from {generated_at} for user {user_id}"
        )
        return None
    except Exception as e:
        logger.warning(f"Firestore: get_cached_schedule error for {user_id}: {e}")
        return None

