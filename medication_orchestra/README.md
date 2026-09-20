# Medication Orchestra — Flutter client

The caregiver-facing app. It reads a prescription or strip photo, shows what was
identified and what was not, and renders the findings and the dose timetable the
backend produced.

**The client does not make clinical decisions.** Everything clinical — ingredient
identity, interactions, duplicate ingredients, dose ceilings, the schedule and its
verification — is computed by `backend/services/clinical_engine.py` from a cited
knowledge base. The app's job is to (a) capture the image, (b) show the answer
faithfully, including the parts that are missing, and (c) put the dose times into
the phone's notification system.

## Three rules for anyone changing this code

1. **Never reassure without `coverage.is_complete`.** A green tick means *every*
   medicine was identified and checked. If the response says `is_complete: false`,
   the screen must say what was not checked. The same applies to data restored
   from the on-device cache: a cache entry with no coverage block renders as "not
   fully checked", never as all-clear.
2. **Show what the backend could not do.** `schedule_status: partial` and
   `unscheduled[]` are not decoration: they are the only place a caregiver learns
   that a medicine could not be placed anywhere safely.
3. **The model never decides.** Gemini reads handwriting. It does not choose
   medicines, severities, interactions, gaps or times, and nothing on screen may
   imply otherwise.

## Running it

```bash
flutter pub get

# Point at the local demo server (backend/dev_server.py), which needs no
# Google Cloud account and seeds a demo household:
flutter run --dart-define=API_BASE_URL=http://<your-lan-ip>:8080

# Point at a deployment:
flutter run --dart-define=API_BASE_URL=https://<service-url>
```

Firebase configuration lives in `firebase.json`, `lib/firebase_options.dart` and
`android/app/google-services.json`. Those three files name the **demo** project;
regenerate them with `flutterfire configure` before pointing the app at anything
real (HANDOFF.md, task S-1).

## Tests

```bash
flutter analyze
flutter test          # pure logic: reminder planning, widget smoke test
```

The tests deliberately avoid Firebase and platform channels, so they run in CI on
a clean checkout. Anything that needs a device — camera, notifications,
permissions — has to be tested on a device and is listed as outstanding work in
`HANDOFF.md`.

## Layout

```text
lib/
├── main.dart                    # app shell, profile bootstrap, navigation
├── config/api_config.dart       # API base URL from --dart-define
├── screens/                     # login, medications, scan result, interactions,
│                                # schedule, family, settings, about, camera
└── services/
    ├── fcm_service.dart         # OS-level reminder scheduling (thin wrapper)
    ├── reminder_planner.dart    # pure planning logic — the tested part
    └── local_cache_service.dart # on-device cache, including the coverage ledger
```
