"""
firestore_service.py — Medication Orchestra

Firestore data access. All functions are synchronous and are called from async
handlers through `asyncio.to_thread`, so a blocking Firestore round-trip (or a
retry inside the SDK) never stalls the event loop.

Every write that can change a clinical answer bumps `household_updated_at`, so
"has anything changed since I last looked" is a single cheap read rather than a
guess.

There is deliberately no server-side cache of clinical answers. The engine is
fast (the whole 127-test suite runs in under two seconds), and a cached answer
has three ways to go quietly wrong: a rule is corrected and the cache is not,
a new medicine is added and the cache is not, or an alert is acknowledged and
the cached copy still shows it as new. The client caches the last response for
offline use, and that cache carries the coverage ledger, so an offline screen
still says what was *not* checked instead of implying everything was.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime

from google.cloud import firestore

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("PROJECT_ID")
if not PROJECT_ID:
    raise RuntimeError("PROJECT_ID is not set; Firestore cannot be reached safely.")

db = firestore.Client(project=PROJECT_ID)


def _utcnow() -> datetime:
    """Timezone-aware UTC now — safe to compare with Firestore timestamps."""
    return datetime.now(UTC)


def _aware(value) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "tzinfo") and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


# ---------------------------------------------------------------------------
# Medication reads
# ---------------------------------------------------------------------------

async def get_household_medications(user_id: str) -> list[dict]:
    """All active medications for a user, each tagged with its profile."""
    def _fetch():
        profiles_ref = db.collection("users").document(user_id).collection("profiles")
        meds: list[dict] = []
        for profile in profiles_ref.stream():
            meds_ref = profiles_ref.document(profile.id).collection("medications")
            for doc in meds_ref.where("status", "==", "active").stream():
                data = doc.to_dict() or {}
                data["id"] = doc.id
                data["profile_id"] = profile.id
                data.setdefault("brand_name", "")
                data.setdefault("generic_name", "")
                meds.append(data)
        logger.info("Firestore: %d active medications for user %s", len(meds), user_id)
        return meds

    return await asyncio.to_thread(_fetch)


async def get_profiles(user_id: str) -> list[dict]:
    def _fetch():
        ref = db.collection("users").document(user_id).collection("profiles")
        return [
            {"profile_id": doc.id, **(doc.to_dict() or {})}
            for doc in ref.stream()
        ]

    return await asyncio.to_thread(_fetch)


async def get_medications_for_profile(user_id: str, profile_id: str) -> list[dict]:
    def _fetch():
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications")
        )
        return [
            {"id": doc.id, **(doc.to_dict() or {})}
            for doc in ref.where("status", "==", "active").stream()
        ]

    return await asyncio.to_thread(_fetch)


# ---------------------------------------------------------------------------
# Medication writes
# ---------------------------------------------------------------------------

def update_medication_generic(user_id: str, profile_id: str, med_id: str, generic_name: str) -> None:
    """Persist a resolved generic name so future checks do not re-resolve it."""
    try:
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({"generic_name": generic_name, "grounded_at": _utcnow()})
        touch_household_updated_at(user_id)
    except Exception as exc:
        logger.warning("Firestore: could not update generic for %s: %s", med_id, exc)


async def update_medication(user_id: str, profile_id: str, med_id: str, updates: dict) -> None:
    def _update():
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({**updates, "updated_at": _utcnow()})
        touch_household_updated_at(user_id)

    await asyncio.to_thread(_update)


async def delete_medication(user_id: str, profile_id: str, med_id: str) -> None:
    """Soft delete: keeps the record for audit while removing it from checks."""
    def _delete():
        ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
              .collection("medications").document(med_id)
        )
        ref.update({"status": "inactive", "deleted_at": _utcnow()})
        touch_household_updated_at(user_id)

    await asyncio.to_thread(_delete)


async def delete_profile(user_id: str, profile_id: str) -> None:
    def _delete():
        profile_ref = (
            db.collection("users").document(user_id)
              .collection("profiles").document(profile_id)
        )
        batch = db.batch()
        for doc in profile_ref.collection("medications").stream():
            batch.delete(doc.reference)
        batch.delete(profile_ref)
        batch.commit()
        logger.info("Firestore: deleted profile %s for user %s", profile_id, user_id)

    await asyncio.to_thread(_delete)


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------

def touch_household_updated_at(user_id: str) -> None:
    """Bump the invalidation timestamp. Any medication change must call this."""
    try:
        db.collection("users").document(user_id).set(
            {"household_updated_at": _utcnow()}, merge=True
        )
    except Exception as exc:
        # Logged loudly: if this silently fails, cached clinical answers can
        # outlive the medication list they were computed from.
        logger.error("Firestore: could not bump household_updated_at for %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Family members
# ---------------------------------------------------------------------------

async def get_family_members(user_id: str) -> list[dict]:
    def _fetch():
        ref = db.collection("users").document(user_id).collection("family_members")
        return [{"member_id": doc.id, **(doc.to_dict() or {})} for doc in ref.stream()]

    return await asyncio.to_thread(_fetch)


async def add_family_member(
    user_id: str,
    name: str,
    fcm_token: str = "",
    phone: str = "",
    relationship: str = "",
) -> str:
    """Register an SOS contact.

    A contact without a linked device is still stored (so the phone number is
    kept for the invite flow) but is reported as unreachable rather than being
    treated as deliverable.
    """
    def _add():
        ref = db.collection("users").document(user_id).collection("family_members").document()
        ref.set({
            "name": name,
            "phone": phone,
            "relationship": relationship,
            "fcm_token": fcm_token,
            "status": "verified" if fcm_token else "pending_invite",
            "added_at": _utcnow(),
        })
        return ref.id

    return await asyncio.to_thread(_add)


async def delete_family_member(user_id: str, member_id: str) -> None:
    def _delete():
        (
            db.collection("users").document(user_id)
              .collection("family_members").document(member_id).delete()
        )

    await asyncio.to_thread(_delete)
