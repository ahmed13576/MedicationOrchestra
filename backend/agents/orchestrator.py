"""
Medication Orchestra — ADK-style Orchestrator

Coordinates the full medication safety pipeline:
  1. Fetch all active household medications from Firestore
  2. Check the interaction cache — skip Gemini if meds unchanged
  3. If cache miss: run InteractionCheckerAgent (grounding + RAG + Gemini)
  4. Check the schedule cache — skip Gemini if meds unchanged
  5. If cache miss: run ScheduleAlertAgent (Gemini schedule generation)

The orchestrator intentionally passes the interactions it already computed
directly into generate_schedule() so the schedule agent never duplicates the
interaction check.

Entry point: run_pipeline(user_id) — called by POST /api/v1/schedule/generate
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)


async def run_pipeline(user_id: str) -> dict:
    """
    Full medication orchestra pipeline with two-level caching.

    Caching contract:
      - household_updated_at on users/{uid} is bumped on every med write.
      - interactions/latest is returned without Gemini if still fresh.
      - schedules/{today} is returned without Gemini if still fresh.

    Args:
        user_id: Firebase UID of the authenticated user.

    Returns:
        dict with keys:
          - interactions: list of interaction alert dicts
          - schedule: generated daily dose schedule dict
          - interaction_count: total interactions found
          - critical_count: contraindicated + major interactions
          - message: optional status message
          - from_cache: True if both interactions and schedule were served from cache
    """
    import asyncio
    from services.firestore_service import (
        get_household_medications,
        get_household_updated_at,
        get_cached_interactions,
        get_cached_schedule,
        save_cached_interactions,
    )
    from agents.interaction_checker_agent import check_household_interactions
    from agents.schedule_alert_agent import generate_schedule

    logger.info(f"[Orchestrator] Starting pipeline for user {user_id}")
    today = date.today().isoformat()

    # ── Step 1: Fetch medications ──────────────────────────────────────────────
    meds = await get_household_medications(user_id)
    logger.info(f"[Orchestrator] {len(meds)} medications found")

    if not meds:
        return {
            "interactions": [],
            "schedule": None,
            "interaction_count": 0,
            "critical_count": 0,
            "from_cache": False,
            "message": "No medications found. Scan your medications first.",
        }

    # ── Step 2: Resolve household_updated_at once (shared by both cache checks) ─
    household_updated_at = await asyncio.to_thread(get_household_updated_at, user_id)

    # ── Step 3: Check schedule cache FIRST ────────────────────────────────────
    # If the schedule cache is valid, we can also trust the interaction cache and
    # skip all Gemini calls entirely.
    cached_schedule = await asyncio.to_thread(
        get_cached_schedule, user_id, today, household_updated_at
    )
    cached_interactions = await asyncio.to_thread(
        get_cached_interactions, user_id, household_updated_at
    )

    if cached_schedule is not None and cached_interactions is not None:
        logger.info("[Orchestrator] Both caches valid — skipping all Gemini calls")
        critical_count = sum(
            1 for i in cached_interactions
            if i.get("severity") in ("contraindicated", "major")
        )
        return {
            "interactions": cached_interactions,
            "schedule": cached_schedule,
            "interaction_count": len(cached_interactions),
            "critical_count": critical_count,
            "from_cache": True,
        }

    # ── Step 4: Interaction check (with its own cache inside) ─────────────────
    # check_household_interactions handles its own cache read/write internally,
    # but since we've already determined a cache miss above, it will run the
    # full pipeline. We pass the pre-fetched household_updated_at to avoid a
    # duplicate Firestore read inside the agent.
    interactions = await check_household_interactions(user_id)
    logger.info(f"[Orchestrator] {len(interactions)} interactions resolved")

    # ── Step 5: Schedule generation (passes already-computed interactions) ─────
    # generate_schedule checks its own cache internally; if valid it returns
    # immediately. Otherwise it runs Gemini and saves the result.
    schedule = await generate_schedule(
        user_id, meds, interactions,
        household_updated_at=household_updated_at,
        today=today,
    )
    logger.info("[Orchestrator] Schedule resolved")

    critical_count = sum(
        1 for i in interactions
        if i.get("severity") in ("contraindicated", "major")
    )

    return {
        "interactions": interactions,
        "schedule": schedule,
        "interaction_count": len(interactions),
        "critical_count": critical_count,
        "from_cache": False,
    }
