"""
Medication Orchestra — Firebase Auth Middleware
Verifies Firebase ID tokens from Flutter app using firebase-admin with ADC.

Auth: Google Cloud ADC — no service account file path needed in production.
For local dev: set GOOGLE_APPLICATION_CREDENTIALS or DEV_MODE=true.
"""

import os
import logging
import firebase_admin
from firebase_admin import auth
from fastapi import HTTPException, Header

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Initialize Firebase Admin — ADC handles credentials automatically
# ---------------------------------------------------------------------------

if not firebase_admin._apps:
    # For local dev: if service-account.json exists, use it (enables FCM).
    # In production (Cloud Run): ADC picks up the service account automatically.
    import os as _os
    _sa_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "service-account.json",
    )
    if _os.path.exists(_sa_path):
        from firebase_admin import credentials as _cred
        firebase_admin.initialize_app(_cred.Certificate(_sa_path))
        logger.info(f"Firebase Admin initialized with service account: {_sa_path}")
    else:
        firebase_admin.initialize_app()
        logger.info("Firebase Admin initialized with ADC")


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------

async def verify_firebase_token(authorization: str = Header(...)) -> str:
    """
    FastAPI dependency — verifies Firebase ID token from Authorization header.

    DEV_MODE bypass: if DEV_MODE=true env var is set, returns 'dev-user-123'
    without any token verification (local development only).

    Returns:
        user_id (str) — Firebase UID of the authenticated user

    Raises:
        HTTPException(401) — invalid or expired token
        HTTPException(400) — missing Authorization header
    """
    # DEV bypass for local testing without a real Firebase user
    if os.getenv("DEV_MODE") == "true":
        logger.warning("DEV_MODE active — skipping token verification")
        return "dev-user-123"

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=400, detail="Authorization header must be 'Bearer <token>'")

    token = authorization.replace("Bearer ", "", 1).strip()

    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token["uid"]
    except auth.ExpiredIdTokenError:
        raise HTTPException(status_code=401, detail="Token has expired. Please sign in again.")
    except auth.InvalidIdTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")
    except Exception as e:
        logger.error(f"Token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired token")
