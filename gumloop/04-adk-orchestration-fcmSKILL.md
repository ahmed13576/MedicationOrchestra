day 3 /home/user/medication_orchestra/.agents/skills/04-adk-orchestration-fcm/SKILL.md
---
name: adk-orchestration-fcm
description: Wires up the Google ADK orchestrator connecting all three agents, implements the smart schedule generator, sets up Firebase Cloud Messaging for dose reminders and SOS emergency alerts, and adds the family member management feature. Covers Day 3 of the sprint.
---

# ADK Orchestration + FCM Alerts Skill

## What This Builds
- ADK Orchestrator connecting IntakeAgent → InteractionCheckerAgent → ScheduleAlertAgent
- Smart schedule generator (conflict-free daily dosing plan)
- Firebase Cloud Messaging: dose reminders + SOS emergency button
- Family member management (add family, share FCM token)
- `POST /api/v1/interactions/check` and `POST /api/v1/sos` endpoints

## Step 1: ADK Orchestrator (backend/agents/orchestrator.py)

```python
"""
Main ADK Orchestrator for Medication Orchestra.
Coordinates: Intake → Interaction Check → Schedule Generation → Alert Setup
"""
from google.adk.agents import BaseAgent, tool
from google.adk.sessions import InMemorySessionService
import asyncio
import os

from agents.interaction_checker_agent import check_household_interactions
from agents.schedule_alert_agent import generate_schedule, save_interactions_to_firestore
from services.firestore_service import get_household_medications

class MedicationOrchestratorAgent(BaseAgent):
    """
    Master orchestrator that runs the full medication safety pipeline.
    Triggered after any new medication is added to a profile.
    """
    
    name = "medication_orchestrator"
    description = "Orchestrates the full medication safety check pipeline for a household"
    
    @tool
    async def run_full_pipeline(self, user_id: str) -> dict:
        """
        Full pipeline: Fetch meds → Check interactions → Generate schedule → Save results
        """
        print(f"[Orchestrator] Starting pipeline for user: {user_id}")
        
        # Step 1: Get all household medications
        meds = await get_household_medications(user_id)
        print(f"[Orchestrator] Found {len(meds)} medications in household")
        
        if len(meds) < 2:
            return {
                "interactions": [],
                "schedule": None,
                "message": "Add at least 2 medications to check for interactions"
            }
        
        # Step 2: Check interactions across all medication pairs
        print("[Orchestrator] Checking drug interactions...")
        interactions = await check_household_interactions(user_id)
        print(f"[Orchestrator] Found {len(interactions)} interactions")
        
        # Step 3: Save interactions to Firestore
        await save_interactions_to_firestore(user_id, interactions)
        
        # Step 4: Generate safe dosing schedule
        print("[Orchestrator] Generating safe schedule...")
        schedule = await generate_schedule(user_id, meds, interactions)
        
        return {
            "interactions": interactions,
            "schedule": schedule,
            "interaction_count": len(interactions),
            "critical_count": sum(1 for i in interactions if i['severity'] in ['contraindicated', 'major'])
        }


# Create orchestrator instance
orchestrator = MedicationOrchestratorAgent()

async def run_pipeline(user_id: str) -> dict:
    """Entry point for the orchestrator pipeline."""
    return await orchestrator.run_full_pipeline(user_id)
```

## Step 2: Schedule Generator (backend/agents/schedule_alert_agent.py)

```python
"""
ScheduleAlertAgent: Generates safe medication schedules respecting interaction time gaps,
stores them in Firestore, and sets up FCM reminders.
"""
import google.generativeai as genai
from google.cloud import firestore
from services.notification_service import NotificationService
import os
import json
from datetime import datetime, date

genai.configure(api_key=os.environ['GEMINI_API_KEY'])
model = genai.GenerativeModel('gemini-2.0-flash')
db = firestore.Client()

SCHEDULE_PROMPT = """You are a medication scheduling assistant for Indian patients.

MEDICATIONS TO SCHEDULE:
{medications_json}

INTERACTIONS FOUND (with time gap requirements):
{interactions_json}

Create a safe daily medication schedule that:
1. Respects all frequency requirements (OD=1x, BD=2x, TDS=3x, QID=4x, SOS=as needed)
2. Maintains the required minimum time gaps between interacting medications
3. Follows meal instructions (AC=before meals, PC=after meals, HS=at bedtime)
4. Groups compatible medications that can be taken together
5. Minimizes total number of dose times per day (patient convenience)
6. Assumes standard Indian meal times: Breakfast 8am, Lunch 1pm, Dinner 8pm, Bedtime 10pm

Return ONLY valid JSON in this format:
{{
  "dose_times": [
    {{
      "time": "08:00",
      "label": "Morning (with breakfast)",
      "medications": [
        {{
          "med_id": "id_here",
          "med_name": "Dolo 650",
          "generic_name": "Paracetamol",
          "dose": "1 tablet (650mg)",
          "instruction": "After breakfast",
          "interaction_warning": null
        }}
      ]
    }}
  ],
  "total_dose_times": 3,
  "safety_notes": ["Warfarin and Ibuprofen kept 6 hours apart"]
}}
"""

async def generate_schedule(user_id: str, medications: list, interactions: list) -> dict:
    """Generate a safe daily medication schedule."""
    
    # Prepare meds for prompt
    meds_for_prompt = [{
        "id": m.get('id'),
        "brand_name": m.get('brand_name'),
        "generic_name": m.get('generic_name'),
        "dosage": m.get('dosage'),
        "frequency_raw": m.get('frequency_raw'),
        "frequency_english": m.get('frequency_english'),
        "instruction": m.get('instruction', ''),
        "timing": m.get('timing', [])
    } for m in medications if m.get('status') == 'active']
    
    # Prepare interactions for prompt
    interactions_for_prompt = [{
        "drug_a": i.get('med_a_name'),
        "drug_b": i.get('med_b_name'),
        "severity": i.get('severity'),
        "time_gap_hours": i.get('time_gap_hours', 0),
        "time_gap_note": i.get('time_gap_note', '')
    } for i in interactions]
    
    prompt = SCHEDULE_PROMPT.format(
        medications_json=json.dumps(meds_for_prompt, indent=2),
        interactions_json=json.dumps(interactions_for_prompt, indent=2) if interactions_for_prompt else "None"
    )
    
    response = model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(temperature=0.1)
    )
    
    try:
        text = response.text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        schedule = json.loads(text.strip())
    except Exception as e:
        print(f"Schedule parse error: {e}")
        # Fallback: use the timing from medications directly
        schedule = _fallback_schedule(medications, interactions)
    
    # Save to Firestore
    today = date.today().isoformat()
    db.collection("users").document(user_id)\
      .collection("schedules").document(today)\
      .set({**schedule, "generated_at": datetime.utcnow(), "date": today})
    
    return schedule


async def save_interactions_to_firestore(user_id: str, interactions: list):
    """Save detected interactions to Firestore."""
    # Delete old interactions first
    old_docs = db.collection("users").document(user_id)\
                 .collection("interactions").stream()
    for doc in old_docs:
        doc.reference.delete()
    
    # Save new interactions
    for interaction in interactions:
        db.collection("users").document(user_id)\
          .collection("interactions")\
          .add({**interaction, "created_at": datetime.utcnow()})


def _fallback_schedule(medications: list, interactions: list) -> dict:
    """Simple fallback schedule when Gemini fails."""
    dose_times = []
    timing_groups = {}
    
    for med in medications:
        if med.get('status') != 'active':
            continue
        for t in med.get('timing', ['08:00']):
            if t not in timing_groups:
                timing_groups[t] = []
            timing_groups[t].append({
                "med_id": med.get('id'),
                "med_name": med.get('brand_name'),
                "generic_name": med.get('generic_name'),
                "dose": "1 tablet",
                "instruction": med.get('instruction', ''),
                "interaction_warning": None
            })
    
    for time, meds in sorted(timing_groups.items()):
        dose_times.append({
            "time": time,
            "label": f"Dose at {time}",
            "medications": meds
        })
    
    return {"dose_times": dose_times, "total_dose_times": len(dose_times), "safety_notes": []}
```

## Step 3: Notification Service (backend/services/notification_service.py)

```python
import firebase_admin
from firebase_admin import messaging, credentials
import json
import os

if not firebase_admin._apps:
    cred = credentials.Certificate(json.loads(os.environ['FIREBASE_SERVICE_ACCOUNT']))
    firebase_admin.initialize_app(cred)


def send_dose_reminder(fcm_token: str, med_name: str, dose: str, 
                       instruction: str, warning: str = None) -> bool:
    """Send a single dose reminder push notification."""
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
        return True
    except Exception as e:
        print(f"FCM error: {e}")
        return False


def send_sos_alert(family_tokens: list[str], patient_name: str,
                   drug_a: str, drug_b: str, severity: str,
                   explanation: str, interaction_id: str) -> int:
    """Send SOS emergency alert to all family members."""
    
    emoji = {"contraindicated": "🚫", "major": "🚨", "moderate": "⚠️"}.get(severity, "⚠️")
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
        except Exception as e:
            print(f"SOS FCM error for token: {e}")
    
    return sent
```

## Step 4: Cloud Run Endpoints (add to backend/main.py)

```python
from agents.orchestrator import run_pipeline
from services.notification_service import send_sos_alert
from google.cloud import firestore

@app.post("/api/v1/interactions/check")
async def check_interactions(user_id: str = Depends(verify_firebase_token)):
    """Trigger full interaction check pipeline for household."""
    result = await run_pipeline(user_id)
    return result

@app.get("/api/v1/interactions")
async def get_interactions(user_id: str = Depends(verify_firebase_token)):
    """Get current interactions for household."""
    docs = db.collection("users").document(user_id)\
             .collection("interactions")\
             .order_by("severity")\
             .stream()
    return {"interactions": [doc.to_dict() | {"id": doc.id} for doc in docs]}

@app.post("/api/v1/sos")
async def trigger_sos(payload: dict, user_id: str = Depends(verify_firebase_token)):
    """Emergency SOS: send interaction alert to all family members."""
    interaction_id = payload.get("interaction_id")
    
    # Get the specific interaction
    interaction_doc = db.collection("users").document(user_id)\
                        .collection("interactions").document(interaction_id).get()
    
    if not interaction_doc.exists:
        raise HTTPException(404, "Interaction not found")
    
    interaction = interaction_doc.to_dict()
    
    # Get user profile for patient name
    user_doc = db.collection("users").document(user_id).get()
    patient_name = user_doc.to_dict().get("name", "Family member")
    
    # Get family member FCM tokens
    family_docs = db.collection("users").document(user_id)\
                    .collection("family_members").stream()
    
    family_tokens = [
        doc.to_dict().get("fcm_token") 
        for doc in family_docs 
        if doc.to_dict().get("fcm_token")
    ]
    
    if not family_tokens:
        raise HTTPException(404, "No family members configured for alerts")
    
    # Send SOS
    sent_count = send_sos_alert(
        family_tokens=family_tokens,
        patient_name=patient_name,
        drug_a=interaction["med_a_name"],
        drug_b=interaction["med_b_name"],
        severity=interaction["severity"],
        explanation=interaction["explanation"],
        interaction_id=interaction_id
    )
    
    return {
        "sent_to": sent_count,
        "total_family_members": len(family_tokens),
        "message": f"Alert sent to {sent_count} family member(s)"
    }
```

## Deliverable Checklist
- [ ] `POST /api/v1/interactions/check` runs full pipeline and returns interactions + schedule
- [ ] Schedule respects meal times (AC=before, PC=after) and time gaps between interacting meds
- [ ] FCM dose reminder arrives on device within 2 seconds of API call
- [ ] SOS button: `POST /api/v1/sos` → family member receives push notification
- [ ] Interactions saved to Firestore, visible in Firebase Console
- [ ] Schedule saved to Firestore under `users/{id}/schedules/{date}`
- [ ] End-to-end test: add 3 medications with known interaction → interaction detected → schedule generated → reminder set
