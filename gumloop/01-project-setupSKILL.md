/home/user/medication_orchestra/.agents/skills/01-project-setup/SKILL.md
day 1 
---
name: project-setup
description: Sets up the Medication Orchestra project from scratch. Use this skill when initializing the project, configuring Firebase, setting up Flutter, or scaffolding the Cloud Run backend. Covers Day 1 Hours 0-2 of the sprint.
---

# Project Setup Skill

## When to use
- Starting the project from zero
- Setting up Flutter + Firebase connection
- Creating the Firestore schema and security rules
- Scaffolding the Cloud Run FastAPI backend skeleton

## Step 1: Flutter Project Init

```bash
flutter create medication_orchestra --org com.medicationorchestra --platforms android,ios,web
cd medication_orchestra
```

## Step 2: pubspec.yaml dependencies

Replace the dependencies section in pubspec.yaml with exactly:

```yaml
dependencies:
  flutter:
    sdk: flutter
  # Firebase
  firebase_core: ^3.6.0
  firebase_auth: ^5.3.1
  cloud_firestore: ^5.4.4
  firebase_messaging: ^15.1.3
  flutter_local_notifications: ^17.2.2
  # Camera
  camera: ^0.11.0+2
  image_picker: ^1.1.2
  # HTTP
  dio: ^5.7.0
  # UI
  flutter_svg: ^2.0.10+1
  google_fonts: ^6.2.1
  # Utils
  intl: ^0.19.0
  shared_preferences: ^2.3.2
  permission_handler: ^11.3.1

dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0
```

Run: `flutter pub get`

## Step 3: FlutterFire Configure

```bash
dart pub global activate flutterfire_cli
flutterfire configure --project=YOUR_FIREBASE_PROJECT_ID
```

This generates `lib/firebase_options.dart` automatically. DO NOT edit it.

## Step 4: Cloud Run Backend Skeleton

Create `backend/` directory with this structure:

### backend/requirements.txt
```
fastapi==0.115.0
uvicorn[standard]==0.30.6
google-generativeai==0.8.3
google-cloud-firestore==2.19.0
firebase-admin==6.5.0
google-cloud-aiplatform==1.68.0
python-multipart==0.0.12
pillow==10.4.0
python-dotenv==1.0.1
pydantic==2.9.2
```

### backend/Dockerfile
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### backend/main.py skeleton
```python
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(title="Medication Orchestra API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "medication-orchestra"}

# Routes will be added by subsequent skills
```

## Step 5: Firestore Schema

Create these collections manually in Firestore Console OR let the app create them on first write:

```
users/{userId}/
  - name: string
  - role: "caregiver" | "patient"
  - fcm_token: string
  - created_at: timestamp

users/{userId}/profiles/{profileId}/
  - name: string
  - relationship: "self"|"parent"|"spouse"|"child"
  - age: number
  - conditions: string[]

users/{userId}/profiles/{profileId}/medications/{medId}/
  - brand_name: string
  - generic_name: string
  - dosage: string
  - frequency_raw: string  ("BD", "TDS", etc.)
  - frequency_english: string  ("Twice daily")
  - timing: string[]  (["08:00", "20:00"])
  - instruction: string  ("After meals")
  - status: "active" | "completed"
  - source: "photo" | "manual"
  - created_at: timestamp

users/{userId}/interactions/{interactionId}/
  - med_a_name: string
  - med_b_name: string
  - severity: "contraindicated"|"major"|"moderate"|"minor"
  - title: string
  - explanation: string  (plain language)
  - time_gap_hours: number
  - time_gap_note: string
  - acknowledged: boolean
  - created_at: timestamp

users/{userId}/schedules/{date}/
  - doses: array of {time, medications[], interaction_warning}

users/{userId}/family_members/{memberId}/
  - name: string
  - relationship: string
  - fcm_token: string
  - role: "viewer" | "alert_recipient"
```

## Step 6: Firestore Security Rules

In Firebase Console → Firestore → Rules, paste:

```javascript
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /users/{userId}/{document=**} {
      allow read, write: if request.auth != null 
        && request.auth.uid == userId;
    }
  }
}
```

## Step 7: Android Config

In `android/app/build.gradle`, ensure:
```gradle
android {
    defaultConfig {
        minSdkVersion 21
    }
}
// At bottom:
apply plugin: 'com.google.gms.google-services'
```

In `android/build.gradle` (project-level):
```gradle
dependencies {
    classpath 'com.google.gms:google-services:4.4.2'
}
```

## Deliverable Checklist
- [ ] `flutter pub get` runs without errors
- [ ] `flutterfire configure` completed — `lib/firebase_options.dart` exists
- [ ] `flutter run` launches app on physical device
- [ ] Firestore test write succeeds (can be verified in Firebase Console)
- [ ] Cloud Run backend: `uvicorn main:app` starts on port 8080
- [ ] `curl localhost:8080/health` returns `{"status":"ok"}`
