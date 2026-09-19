"""
audit_service.py — Medication Orchestra

Append-only audit trail for every access to or change of health data.

Why this exists: a medication-safety product handling prescriptions cannot
answer "who saw this, when, and why?" without it, which blocks both partner
due diligence and any regulatory review (DPDP Act 2023, HIPAA if applicable).
Events are written to users/{uid}/audit/... so the existing security rules keep
them inside the user's own tree, and the record id makes each entry immutable
to accidental overwrite.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

#: Event names kept stable - they are queried by support and compliance.
EVENT_SCAN = "medication.scan"
EVENT_MEDICATION_CREATED = "medication.created"
EVENT_MEDICATION_UPDATED = "medication.updated"
EVENT_MEDICATION_DELETED = "medication.deleted"
EVENT_INTERACTION_CHECK = "interaction.checked"
EVENT_SCHEDULE_GENERATED = "schedule.generated"
EVENT_ALERT_ACKNOWLEDGED = "alert.acknowledged"
EVENT_SOS_SENT = "sos.sent"
EVENT_DEVICE_REGISTERED = "device.registered"
EVENT_FAMILY_CHANGED = "family.changed"
EVENT_EXPORT = "data.exported"
EVENT_DELETION = "data.deleted"
EVENT_CONSENT = "consent.recorded"


def record(
    user_id: str,
    event: str,
    *,
    actor: str | None = None,
    profile_id: str = "",
    subject_id: str = "",
    detail: dict | None = None,
    db=None,
) -> None:
    """Append one audit event. Never raises: auditing must not break a request."""
    if db is None:
        from services.firestore_service import db as _db

        db = _db
    try:
        entry = {
            "event": event,
            "actor": actor or user_id,
            "profile_id": profile_id,
            "subject_id": subject_id,
            "detail": detail or {},
            "at": datetime.now(UTC),
        }
        (
            db.collection("users").document(user_id)
              .collection("audit").document(str(uuid.uuid4()))
              .set(entry)
        )
        logger.info(
            "audit: %s user=%s profile=%s subject=%s",
            event, user_id, profile_id or "-", subject_id or "-",
        )
    except Exception as exc:
        logger.warning("audit: failed to record %s for %s: %s", event, user_id, exc)


def list_events(user_id: str, limit: int = 100, db=None) -> list[dict]:
    """Return recent audit entries, newest last. Used by the export endpoint."""
    if db is None:
        from services.firestore_service import db as _db

        db = _db
    try:
        docs = (
            db.collection("users").document(user_id)
              .collection("audit").limit(limit).stream()
        )
        out = []
        for doc in docs:
            data = doc.to_dict() or {}
            at = data.get("at")
            data["audit_id"] = doc.id
            data["at"] = at.isoformat() if hasattr(at, "isoformat") else str(at)
            out.append(data)
        return sorted(out, key=lambda e: e.get("at", ""))
    except Exception as exc:
        logger.warning("audit: could not list events for %s: %s", user_id, exc)
        return []
