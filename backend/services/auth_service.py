"""
Medication Orchestra — Firebase auth dependency.

Verifies Firebase ID tokens with firebase-admin using ADC.

Local development uses the Firebase Auth emulator, not a bypass flag. If a
bypass is genuinely needed (for example a scripted end-to-end test), it must be
requested explicitly with DEV_AUTH_BYPASS=true, it is refused outright whenever
Cloud Run's K_SERVICE environment variable is present, and it logs a warning on
every request so it can never be left on unnoticed in production.
"""

from __future__ import annotations

import logging
import os

import firebase_admin
from fastapi import Header, HTTPException
from firebase_admin import auth

logger = logging.getLogger(__name__)

_DEV_USER = "dev-user-123"


def _dev_bypass_enabled() -> bool:
    if os.getenv("DEV_AUTH_BYPASS", "false").lower() != "true":
        return False
    if os.getenv("K_SERVICE"):
        # Cloud Run always sets K_SERVICE. Refuse to bypass auth in a deployed
        # service even if the variable is set by mistake.
        logger.error(
            "DEV_AUTH_BYPASS is set on a Cloud Run service (K_SERVICE=%s); ignoring it.",
            os.getenv("K_SERVICE"),
        )
        return False
    return True


if not firebase_admin._apps:
    _sa_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "service-account.json",
    )
    if os.path.exists(_sa_path):
        from firebase_admin import credentials

        firebase_admin.initialize_app(credentials.Certificate(_sa_path))
        logger.info("Firebase Admin initialised from a local service account file")
    else:
        firebase_admin.initialize_app()
        logger.info("Firebase Admin initialised with ADC")


async def verify_firebase_token(authorization: str = Header(default="")) -> str:
    """FastAPI dependency returning the authenticated Firebase UID."""
    if _dev_bypass_enabled():
        logger.warning("DEV_AUTH_BYPASS active — skipping token verification (local only)")
        return _DEV_USER

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Sign in to continue. The Authorization header must be 'Bearer <token>'.",
        )

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing token.")

    try:
        decoded = auth.verify_id_token(token, check_revoked=True)
        uid = decoded.get("uid")
        if not uid:
            raise HTTPException(status_code=401, detail="Token has no user id.")
        return uid
    except auth.ExpiredIdTokenError as exc:
        raise HTTPException(status_code=401, detail="Your session expired. Please sign in again.") from exc
    except auth.RevokedIdTokenError as exc:
        raise HTTPException(status_code=401, detail="This session was signed out. Please sign in again.") from exc
    except auth.InvalidIdTokenError as exc:
        raise HTTPException(status_code=401, detail="That sign-in token is not valid.") from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Could not verify your sign-in.") from exc
