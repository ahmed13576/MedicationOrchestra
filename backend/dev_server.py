#!/usr/bin/env python3
"""
dev_server.py — run the API locally with no Google Cloud credentials.

    cd backend
    PROJECT_ID=demo python3 dev_server.py            # http://localhost:8080

What it does:

* substitutes an **in-memory Firestore** for the real one, seeded with a small
  example household (an older patient on warfarin + ibuprofen and a second
  patient on paracetamol-containing products), so the interaction engine has
  something real to find;
* stubs Firebase Auth, FCM and Vertex AI — auth is bypassed and notifications
  are printed to the console instead of sent;
* serves the *real* application: the same `main.py`, the same registry, the same
  engine, the same validation. Only the boundary is fake.

This is a developer and demo harness. It stores nothing, verifies nobody, and
must never be exposed to the internet:

    uvicorn main:app  # for anything real; this file is not imported by it
"""

from __future__ import annotations

import logging
import os
import sys
import types
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
for path in (str(BACKEND), str(BACKEND / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault("PROJECT_ID", "local-demo")
os.environ.setdefault("DEV_AUTH_BYPASS", "true")
os.environ.setdefault("DEV_MODE", "true")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8080,http://localhost:3000")

print("!" * 78)
print(" DEV SERVER — in-memory data, authentication bypassed, notifications printed.")
print(" Never expose this process outside your machine.")
print("!" * 78)


def install_stubs(store) -> None:
    """Stand-ins for the Google Cloud clients, installed before main is imported.

    The Firestore client handed out here is the in-memory store itself, so the
    module-level `db = firestore.Client(...)` in services/firestore_service.py
    resolves straight to the demo data.
    """
    if "google.cloud.firestore" not in sys.modules:
        gcf = types.ModuleType("google.cloud.firestore")
        gcf.Client = lambda *a, **k: store
        gcf.SERVER_TIMESTAMP = None
        gcloud = types.ModuleType("google.cloud")
        gcloud.firestore = gcf
        sys.modules["google.cloud"] = gcloud
        sys.modules["google.cloud.firestore"] = gcf

    if "google.genai" not in sys.modules:
        genai = types.ModuleType("google.genai")
        genai.types = types.ModuleType("google.genai.types")
        genai.Client = lambda *a, **k: types.SimpleNamespace(
            models=types.SimpleNamespace(generate_content=lambda *a, **k: None)
        )
        genai.types.Part = types.SimpleNamespace(from_bytes=lambda **k: None)
        genai.types.GenerateContentConfig = lambda **k: None
        google = sys.modules.get("google") or types.ModuleType("google")
        google.genai = genai
        sys.modules["google"] = google
        sys.modules["google.genai"] = genai
        sys.modules["google.genai.types"] = genai.types

    if "firebase_admin" not in sys.modules:
        firebase_admin = types.ModuleType("firebase_admin")
        firebase_admin._apps = [object()]
        firebase_admin.initialize_app = lambda *a, **k: None
        firebase_admin.credentials = types.SimpleNamespace(Certificate=lambda p: None)

        class _Auth:
            ExpiredIdTokenError = type("ExpiredIdTokenError", (Exception,), {})
            InvalidIdTokenError = type("InvalidIdTokenError", (Exception,), {})
            RevokedIdTokenError = type("RevokedIdTokenError", (Exception,), {})

            def verify_id_token(self, token, check_revoked=False):
                return {"uid": "local-demo-user"}

        firebase_admin.auth = _Auth()

        messaging = types.ModuleType("firebase_admin.messaging")
        for name in ("Message", "Notification", "AndroidConfig"):
            setattr(messaging, name, lambda **kwargs: types.SimpleNamespace(**kwargs))

        def _log_send(message, *a, **k):
            token = getattr(message, "token", "?")
            notification = getattr(message, "notification", None)
            title = getattr(notification, "title", "(no title)")
            print(f"  [FCM → {str(token)[:12]}…] {title}")
            return "local-demo-message-id"

        messaging.send = _log_send
        firebase_admin.messaging = messaging
        sys.modules["firebase_admin"] = firebase_admin
        sys.modules["firebase_admin.auth"] = firebase_admin.auth
        sys.modules["firebase_admin.messaging"] = messaging


def seed(store) -> None:
    """A small household that exercises the engine's real behaviour."""
    import datetime as dt

    from services.auth_service import _DEV_USER

    # The same uid the dev auth bypass authenticates every request as.
    user = _DEV_USER
    now = dt.datetime.now(dt.UTC)
    profiles = [
        ("dad", "Dad", 78),
        ("mom", "Mom", 74),
    ]
    store.docs[("users", user)] = {"created_at": now}
    for profile_id, name, age in profiles:
        store.docs[("users", user, "profiles", profile_id)] = {
            "profile_id": profile_id, "name": name, "age_band": f"{age - 4}-{age}",
            "created_at": now,
        }

    medications = [
        # Dad: a classic, genuinely dangerous pair, plus a brand the registry
        # resolves from its composition.
        ("dad", "warfarin_1", "Warfarin 5mg", "5mg", "OD", ["20:00"]),
        ("dad", "brufen_1", "Brufen 400", "400mg", "BD", ["08:00", "20:00"]),
        # Dad: two products that both contain paracetamol (the duplicate-salt case).
        ("dad", "dolo_1", "Dolo 650", "650mg", "TDS", ["08:00", "13:00", "20:00"]),
        ("dad", "combiflam_1", "Combiflam", "1 tablet", "BD", ["08:00", "20:00"]),
        # An unidentifiable entry, so the coverage ledger has something to report.
        ("dad", "illegible_1", "handwritten squiggle", "1 tab", "", []),
        # Mom: her own medicine. It must never be paired with Dad's.
        ("dad", "ecosprin_1", "Ecosprin 75", "75mg", "OD", ["08:00"]),
        ("dad", "clopilet_1", "Clopilet 75", "75mg", "OD", ["08:00"]),
        ("mom", "shelcal_1", "Shelcal 500", "500mg", "OD", ["09:00"]),
    ]
    from services.medication_registry import get_registry

    registry = get_registry()
    for profile_id, med_id, brand, dosage, frequency, timing in medications:
        resolution = registry.resolve_medication(brand, "")
        record = {
            "brand_name": brand, "generic_name": "", "dosage": dosage,
            "frequency_raw": frequency, "frequency_english": "", "timing": timing,
            "status": "active", "source": "demo", "source_type": "manual",
            "created_at": now,
            "ingredient_ids": [i.ingredient_id for i in resolution.ingredients],
            "resolved_by": resolution.resolved_by,
            "resolution_confidence": resolution.confidence,
        }
        if resolution.ingredients:
            record["generic_name"] = " + ".join(
                i["name"] for i in resolution.to_dict()["ingredients"]
            )
        store.docs[("users", user, "profiles", profile_id, "medications", med_id)] = record

    store.docs[("users", user, "family_members", "contact_1")] = {
        "name": "Anita (daughter)", "phone": "+910000000000",
        "relationship": "daughter", "fcm_token": "demo-device-token",
        "status": "verified", "added_at": now,
    }
    store.docs[("users", user, "devices", "demo")] = {
        "token": "demo-device-token", "platform": "android", "updated_at": now,
    }
    store.docs[("device_index", "demo")] = {
        "user_id": user, "platform": "android", "updated_at": now,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from fakes import FakeFirestore

    store = FakeFirestore()
    install_stubs(store)

    import main as app_module
    import services.firestore_service as firestore_service

    seed(store)
    app_module.db = store
    firestore_service.db = store

    print(f"""
  Knowledge base : {app_module.REGISTRY.describe()}
  Review status  : {app_module.REGISTRY.review_status}
  Authenticated  : as the dev bypass user (DEV_AUTH_BYPASS)
  Demo data      : Dad (warfarin, Brufen, Dolo 650, Combiflam, Ecosprin, Clopilet,
                   one illegible entry) and Mom (Shelcal)

  Try:
    curl -s localhost:8080/health | python3 -m json.tool
    curl -s "localhost:8080/api/v1/interactions" -H 'Authorization: Bearer demo' | python3 -m json.tool
    curl -s -X POST "localhost:8080/api/v1/schedule/generate" -H 'Authorization: Bearer demo' | python3 -m json.tool
""")

    import uvicorn

    uvicorn.run(
        app_module.app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
