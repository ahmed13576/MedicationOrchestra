"""
consent_service.py — Medication Orchestra

Consent that gates processing, not consent that is merely filed.

The previous build recorded a versioned consent entry and then processed
prescriptions regardless. Recording intent while processing anyway is the exact
pattern a data-protection regulator looks for first, so the scopes below are
checked in the request path, before any health data is read, written or sent to
a sub-processor.

Decision on existing accounts (written down deliberately, per D-1): accounts
with no consent record are **re-prompted**, not grandfathered. A missing record
is treated as "no consent", the request is refused with the scope that is
missing, and the client shows the consent screen. There is no third rule where
the check applies only to new users.

Consent for another person (a caregiver recording data about a parent) is a
lawful-basis question, so each entry records who consented, for whom, and on
what authority - see `authority_note`.
"""

from __future__ import annotations

import os

from fastapi import HTTPException

#: The scopes the product asks for. Each is a separate, revocable purpose.
SCOPE_MEDICATION_REVIEW = "medication_review"
SCOPE_PHOTO_STORAGE = "photo_storage"
SCOPE_CAREGIVER_SHARING = "caregiver_sharing"
SCOPE_SOS_CONTACTS = "sos_contacts"

SCOPES: dict[str, str] = {
    SCOPE_MEDICATION_REVIEW:
        "Reading your medicines and checking them for safety problems",
    SCOPE_PHOTO_STORAGE:
        "Keeping the photographs of your prescriptions",
    SCOPE_CAREGIVER_SHARING:
        "Showing this household's medicines to the caregivers you add",
    SCOPE_SOS_CONTACTS:
        "Sending an emergency message to the contacts you name",
}


def _dev_relaxation_enabled() -> bool:
    """Local development only: BOTH switches must be on.

    In production neither is set, so the refusal is unconditional. This exists
    so the demo and the dev server keep working, never to soften the live rule.
    """
    return (
        os.getenv("DEV_MODE", "false").lower() == "true"
        and os.getenv("DEV_AUTH_BYPASS", "false").lower() == "true"
    )


def granted_scopes(consent: dict | None) -> set[str]:
    """The scopes a stored consent record actually grants.

    An entry that was never accepted, or was withdrawn, grants nothing - the
    stored purposes list is ignored in that case rather than lingering.
    """
    consent = consent or {}
    if not consent.get("accepted"):
        return set()
    if not str(consent.get("consent_version") or "").strip():
        # An empty version cannot be tied to a notice the user actually saw, so
        # it is not a consent. This is what stops an empty consent_version in a
        # request body from being treated as agreement.
        return set()
    return {str(p) for p in (consent.get("purposes") or []) if str(p) in SCOPES}


def require(consent: dict | None, scope: str) -> None:
    """Raise 403 naming the missing scope, unless it has been granted.

    The refusal names the scope so the client can open the right consent screen
    instead of showing a generic "permission denied".
    """
    if scope not in SCOPES:
        raise ValueError(f"Unknown consent scope: {scope}")
    if scope in granted_scopes(consent):
        return
    if _dev_relaxation_enabled():
        return
    raise HTTPException(
        status_code=403,
        detail={
            "error": "consent_required",
            "missing_scope": scope,
            "what_this_covers": SCOPES[scope],
            "message": (
                "Before we can do this we need your agreement: "
                f"{SCOPES[scope]}."
            ),
        },
    )
