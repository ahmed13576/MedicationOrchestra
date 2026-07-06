"""
ScheduleAlertAgent — Medication Orchestra

Generates a conflict-free daily dosing schedule using Gemini 2.5 Flash.
Respects interaction time gaps, Indian meal times, and frequency codes.
Schedule is saved to Firestore under users/{uid}/schedules/{date}.

Caching: If household_updated_at has not advanced past the stored schedule's
generated_at, the cached schedule is returned instantly without any Gemini call.
"""

import json
import logging
import asyncio
from datetime import datetime, date, timezone
from typing import Optional

from google.cloud import firestore
from services.gemini_service import client as _gemini_client, _retry_on_quota
from google.genai import types

logger = logging.getLogger(__name__)

_PROJECT_ID = "project-f9540f8f-d01e-47d3-a36"
db = firestore.Client(project=_PROJECT_ID)
_MODEL = "gemini-2.5-flash"

SCHEDULE_PROMPT = """You are a medication scheduling assistant for Indian patients.

MEDICATIONS TO SCHEDULE:
{medications_json}

INTERACTIONS FOUND (with time gap requirements):
{interactions_json}

Create a safe daily medication schedule that:
1. Respects frequency codes: OD=once daily, BD=twice daily, TDS=three times daily, QID=four times, SOS=as needed
2. Maintains the required minimum time gaps between interacting medications
3. Follows meal instructions: AC=before meals, PC=after meals, HS=at bedtime
4. Groups compatible medications that can be taken together at the same time
5. Assumes Indian meal times: Breakfast 08:00, Lunch 13:00, Dinner 20:00, Bedtime 22:00
6. Minimizes total number of dose times per day for patient convenience

Return ONLY valid JSON (no markdown fences):
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


async def generate_schedule(
    user_id: str,
    medications: list,
    interactions: list,
    household_updated_at: Optional[datetime] = None,
    today: Optional[str] = None,
) -> dict:
    """
    Generate a safe daily medication schedule with Gemini 2.5 Flash.
    Falls back to a simple timing-based schedule if Gemini fails.
    Saves the result to Firestore under users/{uid}/schedules/{date}.

    Args:
        user_id: Firebase UID
        medications: list of active medication dicts (already fetched)
        interactions: list of interaction dicts (already computed — NOT re-fetched here)
        household_updated_at: pre-fetched cache invalidation timestamp (optional)
        today: ISO date string (optional, defaults to today)

    Returns:
        The schedule dict.
    """
    from services.firestore_service import get_cached_schedule

    today = today or date.today().isoformat()

    # ── Cache check ────────────────────────────────────────────────────────────
    # Only check if household_updated_at was passed in (avoid duplicate Firestore read)
    cached = await asyncio.to_thread(
        get_cached_schedule, user_id, today, household_updated_at
    )
    if cached is not None:
        logger.info(f"ScheduleAlertAgent: returning cached schedule for user {user_id} on {today}")
        return cached

    # ── Build prompt ───────────────────────────────────────────────────────────
    meds_for_prompt = [
        {
            "id": m.get("id"),
            "brand_name": m.get("brand_name"),
            "generic_name": m.get("generic_name"),
            "dosage": m.get("dosage"),
            "frequency_english": m.get("frequency_english", ""),
            "timing": m.get("timing", []),
            "instruction": m.get("instruction", ""),
        }
        for m in medications
        if m.get("status") == "active"
    ]

    interactions_for_prompt = [
        {
            "drug_a": i.get("med_a_name"),
            "drug_b": i.get("med_b_name"),
            "severity": i.get("severity"),
            "time_gap_hours": i.get("time_gap_hours", 0),
            "time_gap_note": i.get("time_gap_note", ""),
        }
        for i in interactions
    ]

    prompt = SCHEDULE_PROMPT.format(
        medications_json=json.dumps(meds_for_prompt, indent=2),
        interactions_json=(
            json.dumps(interactions_for_prompt, indent=2)
            if interactions_for_prompt
            else "None"
        ),
    )

    schedule = None

    # ── Gemini call with quota-aware retry ─────────────────────────────────────
    # FIX: _retry_on_quota expects an async callable (coro_factory).
    # Wrap the sync SDK call in asyncio.to_thread so _retry_on_quota can
    # correctly await it (previous bug: awaiting a non-coroutine sync return).
    try:
        async def _call_gemini():
            return await asyncio.to_thread(
                _gemini_client.models.generate_content,
                model=_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1024,
                ),
            )

        response = await _retry_on_quota(_call_gemini)
        text = (response.text or "").strip()

        # Strip markdown fences if Gemini adds them
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        schedule = json.loads(text)
        logger.info(f"ScheduleAlertAgent: Gemini schedule generated for user {user_id}")
    except json.JSONDecodeError as e:
        logger.warning(f"ScheduleAlertAgent: JSON parse error, using fallback: {e}")
    except Exception as e:
        logger.warning(f"ScheduleAlertAgent: Gemini error, using fallback: {e}")

    if schedule is None:
        schedule = _fallback_schedule(medications)
        logger.info(f"ScheduleAlertAgent: Using fallback schedule for user {user_id}")

    # ── Persist to Firestore ───────────────────────────────────────────────────
    def _save():
        db.collection("users").document(user_id)\
          .collection("schedules").document(today)\
          .set({
              **schedule,
              "generated_at": datetime.now(timezone.utc),
              "date": today,
          })
    await asyncio.to_thread(_save)
    logger.info(f"ScheduleAlertAgent: Schedule saved for {user_id} on {today}")

    return schedule


def _fallback_schedule(medications: list) -> dict:
    """Simple timing-based fallback when Gemini is unavailable."""
    timing_groups: dict = {}
    for med in medications:
        if med.get("status") != "active":
            continue
        for t in (med.get("timing") or ["08:00"]):
            timing_groups.setdefault(t, []).append({
                "med_id": med.get("id"),
                "med_name": med.get("brand_name"),
                "generic_name": med.get("generic_name", ""),
                "dose": med.get("dosage", "1 tablet"),
                "instruction": med.get("instruction", ""),
                "interaction_warning": None,
            })
    dose_times = [
        {"time": t, "label": f"Dose at {t}", "medications": meds}
        for t, meds in sorted(timing_groups.items())
    ]
    return {
        "dose_times": dose_times,
        "total_dose_times": len(dose_times),
        "safety_notes": [],
    }
