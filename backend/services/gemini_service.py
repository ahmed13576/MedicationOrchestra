"""
Medication Orchestra — Gemini Vision service.

Two jobs, both extraction-only:
  1. Read a prescription photo or a strip photo into structured medication rows.
  2. Classify an image as prescription / strip / other.

Hard rules:
  * This module never decides anything clinical. It returns raw fields plus a
    confidence score. Ingredient identity, interactions, duplicates and timings
    are all computed deterministically elsewhere
    (services/medication_registry.py, services/clinical_engine.py).
  * The declared image MIME type is whatever the bytes actually are, supplied by
    services/image_service.py. The previous version hard-coded "image/jpeg" for
    every upload, including PNG, HEIC and AVIF.
  * Brand-to-generic mapping is a *hint*. It is validated against the ingredient
    registry, and anything the registry does not recognise is left empty so the
    coverage ledger can report it as unchecked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from google import genai
from google.genai import types

from services.medication_registry import get_registry

logger = logging.getLogger(__name__)

PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("LOCATION", "us-central1")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not PROJECT_ID:
    raise RuntimeError("PROJECT_ID is not set; Vertex AI cannot be reached.")

client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)


async def _retry_on_quota(coro_factory, max_retries: int = 3, base_delay: float = 2.0):
    """Retry an async callable with exponential backoff on 429/RESOURCE_EXHAUSTED."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as exc:
            text = str(exc)
            if "429" in text or "RESOURCE_EXHAUSTED" in text:
                last_exc = exc
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        "Vertex AI throttled us (attempt %d/%d); retrying in %.0fs",
                        attempt + 1, max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
            raise
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA_INSTRUCTIONS = """
Return a JSON array, one object per medicine, with EXACTLY these keys:
  brand_name          - as printed (e.g. "Dolo 650"). "" if unreadable.
  generic_name        - the active ingredient(s) if printed on the pack
                        (e.g. "Paracetamol", "Ibuprofen + Paracetamol"). "" if not visible.
  dosage              - strength per dose (e.g. "650mg", "1 tablet"). "" if not stated.
  frequency_raw       - the shorthand as written (OD, BD, TDS, QID, HS, SOS, STAT, "1-0-1").
  frequency_english   - plain English ("Twice daily"). "" if not written.
  timing              - JSON array of "HH:MM" strings, using Indian meal times:
                        breakfast 08:00, lunch 13:00, evening 17:00, dinner 20:00,
                        bedtime 22:00. [] if the prescription does not say.
  duration            - e.g. "5 days". "" if not written.
  condition           - why it was prescribed, if written. "" otherwise.
  confidence          - "high", "medium" or "low" - your honest reading confidence.
  notes               - anything a pharmacist should know (e.g. "handwriting unclear").

NEVER invent a medicine, a strength or a frequency. If a field is not visible on
the image, return "" (or [] for timing). An empty field is always better than a
guess: the app shows the user what could not be read and asks them to confirm.
If the image contains no medicine at all, return [].
"""

PRESCRIPTION_EXTRACTION_PROMPT = f"""
You are reading an Indian doctor's prescription. Indian prescriptions use heavy
abbreviation; expand it:
  OD = once daily, BD = twice daily, TDS = three times daily, QID = four times daily,
  HS = at bedtime, SOS = when required, STAT = immediately,
  AC = before food, PC = after food.
Dosing patterns like "1-0-1" mean morning-0-evening (twice daily) and "1-1-1" means
three times daily.
Read printed and handwritten text. Where handwriting is genuinely ambiguous, give
your best reading and set confidence to "low".
{EXTRACTION_SCHEMA_INSTRUCTIONS}
"""

BLISTER_PACK_EXTRACTION_PROMPT = f"""
You are reading an Indian medicine strip or box. Read the text printed on the foil,
blister or carton. Extract the brand name (largest text), the composition line
(smaller text, often "Each tablet contains ..."), and the strength.
Also capture, when visible: total tablets ("1x10", "10's"), expiry ("EXP 06/2026")
and batch number - put those in `notes`.
{EXTRACTION_SCHEMA_INSTRUCTIONS}
"""

IMAGE_TYPE_DETECTION_PROMPT = (
    "Classify this image as exactly one of: prescription, blister_pack, other.\n"
    "A prescription is a handwritten or printed doctor's note listing medicines.\n"
    "A blister pack is foil or plastic pill packaging with a medicine name printed on it.\n"
    "Reply with only the single word, nothing else."
)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

#: Fields that must be present for a medication to be considered fully checked.
REQUIRED_FIELDS = ("brand_name", "generic_name", "dosage")
REVIEW_THRESHOLD = 80


def calculate_confidence_score(med: dict, source_type: str) -> tuple[int, list[str]]:
    """Score how completely a row was extracted.

    Returns (score, issues). The score drives whether the user is asked to
    confirm a field, and both are surfaced to the client - the coverage ledger
    depends on this being honest rather than optimistic.
    """
    score = 0
    issues: list[str] = []

    if (med.get("brand_name") or "").strip():
        score += 30
    else:
        issues.append("Medicine name not detected")

    generic = (med.get("generic_name") or "").strip()
    brand = (med.get("brand_name") or "").strip()
    if generic and generic.lower() != brand.lower():
        score += 20
    else:
        issues.append("Active ingredient not read from the pack - please confirm")

    if (med.get("dosage") or "").strip():
        score += 15
    else:
        issues.append("Strength not read - please confirm")

    self_reported = str(med.get("confidence") or "").strip().lower()
    if self_reported == "high":
        score += 15
    elif self_reported == "medium":
        score += 5
    elif self_reported == "low":
        score -= 15
        issues.append("The photo was hard to read")

    if source_type == "prescription":
        if (med.get("frequency_english") or med.get("frequency_raw") or "").strip():
            score += 10
        else:
            issues.append("How often to take it was not clear")
        if med.get("timing"):
            score += 10
        else:
            issues.append("Times of day were not clear")
    else:
        if (med.get("expiry_date") or "").strip():
            score += 10
        else:
            issues.append("Expiry date not found")

    return max(0, min(100, score)), issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _make_image_part(image_bytes: bytes, mime_type: str) -> types.Part:
    return types.Part.from_bytes(data=image_bytes, mime_type=mime_type)


def _call_gemini(
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    json_mode: bool = True,
) -> str:
    config_kwargs: dict = {"temperature": temperature, "max_output_tokens": max_tokens}
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt, _make_image_part(image_bytes, mime_type)],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text or ""


def _validate_brand_hint(med: dict) -> None:
    """Check the model's generic name against the registry.

    A generic the registry does not recognise is left in place (the user still
    sees what the pack said) but is *not* treated as verified, so the coverage
    ledger reports the medication as unchecked.
    """
    if not (med.get("generic_name") or "").strip():
        return
    registry = get_registry()
    resolution = registry.resolve_medication(med.get("brand_name"), med.get("generic_name"))
    med["_registry_resolved"] = bool(resolution.ingredients)
    med["_registry_parts"] = [i["ingredient_id"] for i in resolution.ingredients]
    if not resolution.ingredients:
        med["_review_note"] = (
            "We did not recognise the active ingredient on this one - "
            "please check the strip or ask your pharmacist."
        )


def _finalise(rows: list[dict], source_type: str) -> list[dict]:
    for med in rows:
        if not isinstance(med, dict):
            continue
        med["source_type"] = source_type
        _validate_brand_hint(med)
        score, issues = calculate_confidence_score(med, source_type)
        med["_confidence_score"] = score
        med["_review_issues"] = issues + ([med["_review_note"]] if med.get("_review_note") else [])
        med["requires_review"] = score < REVIEW_THRESHOLD
    return [m for m in rows if isinstance(m, dict)]


def _parse_rows(raw: str, source_type: str) -> list[dict]:
    cleaned = _strip_json_fences(raw)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        logger.error("Could not parse model JSON (%s): %s", exc, cleaned[:300])
        return []

    if isinstance(result, dict) and result.get("error"):
        raise ValueError(str(result["error"]))
    if isinstance(result, dict):
        for key in ("medications", "medicines", "data", "items"):
            if isinstance(result.get(key), list):
                result = result[key]
                break
    if not isinstance(result, list):
        return []
    return _finalise(result, source_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def detect_image_type(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    """Classify an image. Fails closed to 'prescription' on any error."""
    try:
        raw = await _retry_on_quota(
            lambda: asyncio.to_thread(
                _call_gemini, IMAGE_TYPE_DETECTION_PROMPT, image_bytes,
                mime_type, 0.0, 16, False,
            )
        )
        result = (raw or "").strip().lower().strip("'\"").replace(" ", "_")
        if result in ("prescription", "blister_pack", "other"):
            return result
        if "blister" in result or "pack" in result or "strip" in result:
            return "blister_pack"
        if "prescription" in result or "doctor" in result:
            return "prescription"
        return "prescription"
    except Exception as exc:
        logger.warning("Image type detection failed (%s); assuming prescription", exc)
        return "prescription"


async def parse_prescription(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[dict]:
    raw = await _retry_on_quota(
        lambda: asyncio.to_thread(
            _call_gemini, PRESCRIPTION_EXTRACTION_PROMPT, image_bytes, mime_type, 0.0, 8192
        )
    )
    return _parse_rows(raw, "prescription")


async def parse_blister_pack(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[dict]:
    raw = await _retry_on_quota(
        lambda: asyncio.to_thread(
            _call_gemini, BLISTER_PACK_EXTRACTION_PROMPT, image_bytes, mime_type, 0.0, 4096
        )
    )
    return _parse_rows(raw, "blister_pack")


async def parse_medication_image(
    image_bytes: bytes,
    image_type: str = "auto",
    mime_type: str = "image/jpeg",
    language: str = "en",
) -> tuple[list[dict], str, bool]:
    """Unified entry point: (medications, detected_type, requires_review)."""
    detected = (
        await detect_image_type(image_bytes, mime_type) if image_type == "auto" else image_type
    )
    if detected == "blister_pack":
        medications = await parse_blister_pack(image_bytes, mime_type)
    elif detected == "prescription":
        medications = await parse_prescription(image_bytes, mime_type)
    else:
        raise ValueError(
            "That does not look like a prescription or a medicine pack. Please photograph "
            "the strip or the doctor's note."
        )
    requires_review = any(m.get("requires_review") for m in medications)
    return medications, detected, requires_review
