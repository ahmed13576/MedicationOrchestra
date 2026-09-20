"""
notification_service.py — Medication Orchestra

FCM delivery for dose reminders and SOS alerts.

firebase_admin.initialize_app() is called once by services/auth_service.py at
import time; this module must not initialise it again.

Delivery semantics: every send returns a structured receipt instead of a bare
count, so the SOS screen can tell a caregiver exactly who was reached, who
failed and why. Emergency notifications are not a place for optimistic UI.
"""

from __future__ import annotations

import logging

from firebase_admin import messaging

logger = logging.getLogger(__name__)

#: FCM error codes that mean the token is dead and should be pruned.
_PERMANENT_TOKEN_ERRORS = {
    "messaging/registration-token-not-registered",
    "messaging/invalid-registration-token",
    "messaging/invalid-argument",
}


def _classify(exc: Exception) -> str:
    code = getattr(exc, "code", "") or ""
    if code in _PERMANENT_TOKEN_ERRORS:
        return "invalid_token"
    text = str(exc).lower()
    if "not-registered" in text or "invalid-registration" in text:
        return "invalid_token"
    if "quota" in text or "unavailable" in text or "timeout" in text:
        return "temporarily_unavailable"
    return "send_failed"


def send_dose_reminder(
    fcm_token: str,
    med_name: str,
    dose: str,
    instruction: str,
    warning: str | None = None,
) -> bool:
    """Send one dose reminder. Returns True only on confirmed acceptance by FCM."""
    body = f"{dose} — {instruction}" if instruction else dose
    if warning:
        body += f"\n⚠️ {warning}"

    message = messaging.Message(
        notification=messaging.Notification(title=f"💊 Time for {med_name}", body=body),
        android=messaging.AndroidConfig(channel_id="medication_reminders", priority="high"),
        token=fcm_token,
    )
    try:
        messaging.send(message)
        logger.info("Dose reminder sent for %s", med_name)
        return True
    except Exception as exc:
        logger.warning("FCM dose reminder failed for %s (%s)", med_name, _classify(exc))
        return False


def send_sos_alert(
    recipients: list[dict],
    patient_name: str,
    drug_a: str,
    drug_b: str,
    severity: str,
    explanation: str,
    interaction_id: str,
) -> dict:
    """Send an emergency alert to each reachable recipient.

    `recipients` is a list of {"token", "name", "member_id"}. Returns a receipt:
    {"sent": int, "failed": int, "results": [{"member_id", "name", "status"}]}.
    """
    emoji = {"contraindicated": "🚫", "major": "🚨", "moderate": "⚠️"}.get(severity, "⚠️")
    results: list[dict] = []
    sent = 0

    for recipient in recipients:
        token = (recipient.get("token") or "").strip()
        entry = {"member_id": recipient.get("member_id", ""), "name": recipient.get("name", "")}
        if not token:
            entry["status"] = "no_device_linked"
            results.append(entry)
            continue

        message = messaging.Message(
            notification=messaging.Notification(
                title=f"{emoji} MEDICATION ALERT — {patient_name}",
                body=f"{drug_a} + {drug_b}: {explanation[:120]}",
            ),
            data={
                "type": "sos_alert",
                "interaction_id": interaction_id,
                "patient_name": patient_name,
                "severity": severity,
            },
            android=messaging.AndroidConfig(
                channel_id="sos_alerts",
                priority="high",
                ttl=3600_000,  # an emergency alert that is an hour late is not useful
            ),
            token=token,
        )
        try:
            messaging.send(message)
            entry["status"] = "sent"
            sent += 1
        except Exception as exc:
            status = _classify(exc)
            entry["status"] = status
            logger.warning("SOS delivery failed for %s: %s", entry["member_id"], status)
        results.append(entry)

    logger.info(
        "SOS alerts: %d/%d delivered for interaction %s",
        sent, len(recipients), interaction_id,
    )
    return {"sent": sent, "failed": len(results) - sent, "results": results}
