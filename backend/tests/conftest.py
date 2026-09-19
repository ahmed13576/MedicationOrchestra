"""Shared test fixtures.

The backend talks to Google Cloud in production. Tests never do: Firestore,
Vertex AI, Firebase Auth and FCM are replaced by in-memory fakes here, so the
safety logic and the API contract can be exercised with no credentials, no
network and no cost.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
for path in (str(BACKEND), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)


# The in-memory Firestore lives in tests/fakes.py so that the invariant audit can
# drive the real endpoints against exactly the same fake.
from fakes import (
    FakeFirestore,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store() -> FakeFirestore:
    return FakeFirestore()


@pytest.fixture
def api(store, monkeypatch):
    """A TestClient for the real app, backed by the in-memory Firestore.

    Google SDKs are stubbed only at the boundary (Firestore, Vertex, FCM,
    Firebase Auth); every line of application logic under test is the real one.
    """
    _install_sdk_stubs()

    import firebase_admin.messaging as messaging

    import main
    import services.auth_service as auth_service
    import services.firestore_service as fs
    import services.notification_service as notif

    monkeypatch.setattr(main, "db", store, raising=False)
    monkeypatch.setattr(fs, "db", store, raising=False)
    monkeypatch.setattr(main.rate_limit, "limiter", main.rate_limit.RateLimiter())
    monkeypatch.setattr(messaging, "send", lambda *a, **k: "message-id")
    monkeypatch.setattr(
        notif, "_send_one", getattr(notif, "_send_one", None), raising=False
    )

    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    client.headers.update({"Authorization": "Bearer test-token"})
    client.store = store  # convenience for assertions
    #: the uid every request in these tests is authenticated as
    client.user_id = auth_service._DEV_USER

    # Consent gates every health-data endpoint (D-1), so the ordinary test user
    # has a real, recorded consent - the same record the API writes. Tests about
    # consent itself clear or narrow it explicitly.
    from services import consent_service

    store.collection("users").document(client.user_id).set({
        "consent": {
            "consent_version": "test-1",
            "accepted": True,
            "purposes": sorted(consent_service.SCOPES),
        },
    }, merge=True)
    return client


@pytest.fixture(autouse=True)
def _dev_auth(monkeypatch):
    """Use the explicit dev bypass so tests need no Firebase credentials."""
    monkeypatch.setenv("DEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("K_SERVICE", raising=False)


@pytest.fixture
def registry():
    from services.medication_registry import get_registry

    return get_registry(reload=True)


_SDK_STUBS_INSTALLED = False


def _install_sdk_stubs() -> None:
    """Install minimal stand-ins for the Google SDKs imported at module load."""
    global _SDK_STUBS_INSTALLED
    if _SDK_STUBS_INSTALLED:
        return
    _SDK_STUBS_INSTALLED = True

    google = sys.modules.get("google") or types.ModuleType("google")
    sys.modules.setdefault("google", google)

    if "google.cloud" not in sys.modules:
        gcloud = types.ModuleType("google.cloud")
        gcf = types.ModuleType("google.cloud.firestore")

        class _Client:  # replaced by FakeFirestore in the api fixture
            def __init__(self, *a, **k):
                pass

        gcf.Client = _Client
        gcf.SERVER_TIMESTAMP = None
        gcloud.firestore = gcf
        sys.modules["google.cloud"] = gcloud
        sys.modules["google.cloud.firestore"] = gcf
        google.cloud = gcloud

    if "google.genai" not in sys.modules:
        genai = types.ModuleType("google.genai")
        gtypes = types.ModuleType("google.genai.types")
        genai.Client = lambda *a, **k: types.SimpleNamespace(
            models=types.SimpleNamespace(generate_content=lambda *a, **k: None)
        )
        gtypes.Part = types.SimpleNamespace(from_bytes=lambda **k: None)
        gtypes.GenerateContentConfig = lambda **k: None
        gtypes.Tool = lambda **k: None
        gtypes.GoogleSearch = lambda *a, **k: None
        genai.types = gtypes
        sys.modules["google.genai"] = genai
        sys.modules["google.genai.types"] = gtypes
        google.genai = genai

    if "firebase_admin" not in sys.modules:
        fa = types.ModuleType("firebase_admin")
        fa._apps = [object()]
        fa.initialize_app = lambda *a, **k: None
        fa.credentials = types.SimpleNamespace(Certificate=lambda path: None)

        class _Auth:
            ExpiredIdTokenError = type("ExpiredIdTokenError", (Exception,), {})
            InvalidIdTokenError = type("InvalidIdTokenError", (Exception,), {})
            RevokedIdTokenError = type("RevokedIdTokenError", (Exception,), {})

            def verify_id_token(self, token, check_revoked=False):
                return {"uid": "test-user"}

        auth_mod = _Auth()
        fa.auth = auth_mod

        messaging = types.ModuleType("firebase_admin.messaging")
        for name in ("Message", "Notification", "AndroidConfig"):
            setattr(messaging, name, lambda **kwargs: types.SimpleNamespace(**kwargs))
        messaging.send = lambda *a, **k: "message-id"
        fa.messaging = messaging

        sys.modules["firebase_admin"] = fa
        sys.modules["firebase_admin.auth"] = auth_mod
        sys.modules["firebase_admin.messaging"] = messaging
