"""
notification_service.py — Medication Orchestra

Sends FCM push notifications via the firebase-admin SDK.

IMPORTANT: firebase_admin.initialize_app() is already called by auth_service.py
at startup. This module MUST NOT call initialize_app() again.
Simply importing firebase_admin.messaging works after any app has been initialized.
"""

import logging
from firebase_admin import messaging

logger = logging.getLogger(__name__)


def send_dose_reminder(
    fcm_token: str,
    med_name: str,
    dose: str,
    instruction: str,
    warning: str | None = None,
) -> bool:
    """
    Send a single dose reminder push notification to a device.

    Args:
        fcm_token: The FCM registration token for the target device.
        med_name: Brand/generic name of the medication.
        dose: Dose amount (e.g. "1 tablet (650mg)").
        instruction: When/how to take (e.g. "After breakfast").
        warning: Optional interaction warning string.

    Returns:
        True on success, False on any FCM error.
    """
    body = f"{dose} — {instruction}"
    if warning:
        body += f"\n⚠️ {warning}"

    message = messaging.Message(
        notification=messaging.Notification(
            title=f"💊 Time for {med_name}",
            body=body,
        ),
        android=messaging.AndroidConfig(
            channel_id="medication_reminders",
            priority="high",
        ),
        token=fcm_token,
    )

    try:
        messaging.send(message)
        logger.info(f"Dose reminder sent for {med_name}")
        return True
    except Exception as e:
        logger.warning(f"FCM dose reminder error for {med_name}: {e}")
        return False


def send_sos_alert(
    family_tokens: list[str],
    patient_name: str,
    drug_a: str,
    drug_b: str,
    severity: str,
    explanation: str,
    interaction_id: str,
) -> int:
    """
    Send an emergency SOS notification to all registered family member devices.

    Args:
        family_tokens: List of FCM registration tokens for family members.
        patient_name: Name of the patient in danger (shown in notification title).
        drug_a: First drug name in the interaction.
        drug_b: Second drug name in the interaction.
        severity: Severity level ('contraindicated', 'major', 'moderate').
        explanation: Plain-language description of the interaction risk.
        interaction_id: Firestore document ID for deep-linking.

    Returns:
        Count of successfully delivered notifications.
    """
    emoji_map = {
        "contraindicated": "🚫",
        "major": "🚨",
        "moderate": "⚠️",
    }
    emoji = emoji_map.get(severity, "⚠️")
    sent = 0

    for token in family_tokens:
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
            ),
            token=token,
        )
        try:
            messaging.send(message)
            sent += 1
            logger.info(f"SOS alert sent to family member (token hash: {token[:8]}...)")
        except Exception as e:
            logger.warning(f"SOS FCM error for family token: {e}")

    logger.info(f"SOS alerts: {sent}/{len(family_tokens)} delivered for interaction {interaction_id}")
    return sent
