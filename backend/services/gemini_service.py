"""
Medication Orchestra — Gemini Vision Service
Parses prescription photos AND medicine blister pack images using Gemini 2.5 Flash.

Auth: Google Cloud ADC (Application Default Credentials) — vertexai=True, NO API keys.
Project: project-f9540f8f-d01e-47d3-a36
"""

import asyncio
import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


async def _retry_on_quota(coro_factory, max_retries: int = 3, base_delay: float = 2.0):
    """
    Retry an async callable with exponential backoff on Vertex AI 429/RESOURCE_EXHAUSTED.
    Delays: 2s → 4s → 8s. Does NOT retry on non-quota errors.
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            return await coro_factory()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                last_exc = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)  # 2s, 4s, 8s
                    logger.warning(
                        f"Vertex AI 429 — retry {attempt + 1}/{max_retries} in {delay:.0f}s: {err_str[:80]}"
                    )
                    await asyncio.sleep(delay)
                    continue
            raise  # Non-quota error: fail immediately
    raise last_exc

# ---------------------------------------------------------------------------
# ADC client — never use API keys
# ---------------------------------------------------------------------------

client = genai.Client(
    vertexai=True,
    project="project-f9540f8f-d01e-47d3-a36",
    location="us-central1",
)

MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

PRESCRIPTION_EXTRACTION_PROMPT = """
You are a medical prescription parser specialized in Indian medical prescriptions.

Analyze this prescription image and extract ALL medications prescribed.

IMPORTANT INSTRUCTIONS:
1. Read both printed AND handwritten text carefully
2. Identify Indian prescription notations:
   - OD = Once daily → timing: ["08:00"]
   - BD = Twice daily → timing: ["08:00", "20:00"]
   - TDS = Three times daily → timing: ["08:00", "14:00", "20:00"]
   - QID = Four times daily → timing: ["08:00", "12:00", "16:00", "20:00"]
   - SOS = As needed (when required)
   - HS = At bedtime → timing: ["22:00"]
   - AC = Before meals
   - PC = After meals
   - STAT = Take immediately
3. Extract both brand name (e.g. "Dolo 650") and generic if visible (e.g. "Paracetamol")
4. If handwriting is unclear, make your best interpretation and mark confidence as "low"
5. Dosage examples: "1-0-1" means morning-afternoon-evening (BD), "1-1-1" means TDS

Return a JSON array ONLY (no other text):
[
  {
    "brand_name": "Dolo 650",
    "generic_name": "Paracetamol",
    "dosage": "650mg",
    "frequency_raw": "BD",
    "frequency_english": "Twice daily",
    "timing": ["08:00", "20:00"],
    "instruction": "After meals",
    "duration": "5 days",
    "condition": "Fever",
    "confidence": "high",
    "notes": ""
  }
]

If no medications found: []
If not a prescription image: {"error": "Not a prescription image"}
"""

BLISTER_PACK_EXTRACTION_PROMPT = """
You are a pharmaceutical packaging reader specialized in Indian medicine blister packs.

Analyze this medicine blister pack image and extract ALL medication information visible.

IMPORTANT INSTRUCTIONS:
1. Focus on text printed or embossed ON the foil or plastic backing — ignore background
2. Read: brand name (large text), generic/salt name (smaller text below brand), strength/dosage
3. Look for Indian blister pack patterns:
   - Strip count: "10 tablets", "1x10", "2x5", "10's"
   - Expiry format: "EXP: MM/YYYY" or "Use before MM/YY"
   - Batch/Lot: "Batch No:", "B.No:", "Lot:", "MFG Batch:"
   - Manufacturer: company name at edge or back
4. If multiple different medications visible (multiple strip types), return one entry per medication
5. If text is partially obscured by pills, infer from visible characters
6. If a field is not visible or cannot be determined, use an empty string "" — do NOT guess

Return a JSON array ONLY (no other text):
[
  {
    "brand_name": "Dolo 650",
    "generic_name": "Paracetamol",
    "dosage": "650mg",
    "total_tablets": 10,
    "expiry_date": "06/2026",
    "batch_no": "AB1234",
    "manufacturer": "Micro Labs Ltd",
    "frequency_raw": "",
    "frequency_english": "",
    "timing": [],
    "instruction": "",
    "duration": "",
    "condition": "",
    "source_type": "blister_pack",
    "confidence": "high",
    "notes": ""
  }
]

If no medications found: []
If not a blister pack image: {"error": "Not a blister pack image"}
"""

# NOTE: detect_image_type uses plain text response — do NOT add JSON mode here
IMAGE_TYPE_DETECTION_PROMPT = """Classify this image as exactly one of: 'prescription', 'blister_pack', 'other'.
A prescription is a handwritten or printed doctor's note listing medications.
A blister pack is foil/plastic pill packaging with medication name printed on it.
Reply with only the single word, nothing else."""

# ---------------------------------------------------------------------------
# Indian brand → generic mapping
# ---------------------------------------------------------------------------

INDIAN_BRAND_MAP = {
    "dolo": "Paracetamol (Acetaminophen)",
    "dolo 650": "Paracetamol (Acetaminophen) 650mg",
    "crocin": "Paracetamol (Acetaminophen)",
    "calpol": "Paracetamol (Acetaminophen)",
    "combiflam": "Ibuprofen + Paracetamol",
    "brufen": "Ibuprofen",
    "ibugesic": "Ibuprofen",
    "ecosprin": "Aspirin (Acetylsalicylic Acid)",
    "disprin": "Aspirin",
    "azithral": "Azithromycin",
    "zithromax": "Azithromycin",
    "augmentin": "Amoxicillin + Clavulanic Acid",
    "amoxyclav": "Amoxicillin + Clavulanic Acid",
    "metrogyl": "Metronidazole",
    "flagyl": "Metronidazole",
    "pantop": "Pantoprazole",
    "pan": "Pantoprazole",
    "razo": "Rabeprazole",
    "omez": "Omeprazole",
    "telma": "Telmisartan",
    "amlong": "Amlodipine",
    "amlip": "Amlodipine",
    "glycomet": "Metformin",
    "glucophage": "Metformin",
    "glimisave": "Glimepiride",
    "amaryl": "Glimepiride",
    "ecosprin av": "Aspirin + Atorvastatin",
    "atorva": "Atorvastatin",
    "lipitor": "Atorvastatin",
    "ciplar": "Propranolol",
    "lasix": "Furosemide",
    "shelcal": "Calcium + Vitamin D3",
    "neurobion": "Vitamin B Complex",
    "becosules": "Vitamin B Complex",
    "limcee": "Vitamin C (Ascorbic Acid)",
    "clavulin": "Amoxicillin + Clavulanic Acid",
    "taxim": "Cefotaxime",
    "cefixime": "Cefixime",
    "zifi": "Cefixime",
    "sporidex": "Cephalexin",
    "clavam": "Amoxicillin + Clavulanic Acid",
    "zincovit": "Multivitamin and Multimineral Supplement",
    "montecip": "Montelukast Sodium",
    "montecip fx": "Montelukast Sodium + Fexofenadine Hydrochloride",
    "allegra": "Fexofenadine Hydrochloride",
    "cetrizine": "Cetirizine Hydrochloride",
    "zyrtec": "Cetirizine Hydrochloride",
    "levocet": "Levocetirizine",
}


def map_brand_to_generic(brand_name: str) -> str:
    """Map Indian brand name to generic name."""
    key = brand_name.lower().strip()
    # Try exact match
    if key in INDIAN_BRAND_MAP:
        return INDIAN_BRAND_MAP[key]
    # Try prefix match (e.g., "Dolo 650mg" → "dolo")
    for brand, generic in INDIAN_BRAND_MAP.items():
        if key.startswith(brand):
            return generic
    # Return original if no mapping found
    return brand_name


# ---------------------------------------------------------------------------
# Confidence Scoring
# ---------------------------------------------------------------------------

def calculate_confidence_score(med: dict, source_type: str) -> tuple[int, list[str]]:
    """
    Score a medication dict for completeness/confidence (0–100).

    Returns:
        (score, issues) — score int, issues list of human-readable strings

    Thresholds:
        >= 80  → high confidence, save directly
        50–79  → medium, prompt user to review
        < 50   → low, strong warning + force review
    """
    score = 0
    issues = []

    # --- Shared fields (both prescription and blister) ---
    if med.get("brand_name", "").strip():
        score += 20
    else:
        issues.append("Medication name not detected")

    generic = med.get("generic_name", "").strip()
    brand = med.get("brand_name", "").strip()
    if generic and generic != brand:
        score += 10
    else:
        issues.append("Generic/composition not identified")

    if med.get("dosage", "").strip():
        score += 15
    else:
        issues.append("Dosage/strength not found")

    # Gemini self-reported confidence
    confidence_field = med.get("confidence", "").lower()
    if confidence_field == "high":
        score += 15
    elif confidence_field == "low":
        score -= 20
        issues.append("Low OCR confidence (unclear image or text)")

    # --- Source-type specific fields ---
    if source_type == "prescription":
        if med.get("frequency_english", "").strip():
            score += 15
        else:
            issues.append("Dose frequency not found")

        if med.get("timing") and len(med["timing"]) > 0:
            score += 10
        else:
            issues.append("Dose timing not extracted")

        if med.get("duration", "").strip():
            score += 5

    elif source_type == "blister_pack":
        if med.get("expiry_date", "").strip():
            score += 15
        else:
            issues.append("Expiry date not found")

        if med.get("batch_no", "").strip():
            score += 10
        else:
            issues.append("Batch number not found")

        if med.get("manufacturer", "").strip():
            score += 10
        else:
            issues.append("Manufacturer not identified")

    # Clamp to [0, 100]
    score = max(0, min(100, score))
    return score, issues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_json_fences(text: str) -> str:
    """Strip markdown code fences from Gemini response (belt-and-suspenders safety)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:] if lines[0].startswith("```") else lines
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _make_image_part(image_bytes: bytes) -> types.Part:
    """Create a Gemini image part from raw bytes."""
    return types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")


def _call_gemini(
    prompt: str,
    image_bytes: bytes,
    temperature: float = 0.1,
    max_tokens: int = 4096,
    json_mode: bool = True,
) -> str:
    """
    Gemini API call with optional JSON-constrained decoding.

    json_mode=True: forces model to emit only valid JSON tokens (fixes truncation).
    json_mode=False: used for plain-text responses (e.g. image type detection).
    """
    config_kwargs: dict = dict(
        temperature=temperature,
        max_output_tokens=max_tokens,
    )
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"

    response = client.models.generate_content(
        model=MODEL,
        contents=[prompt, _make_image_part(image_bytes)],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response.text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def detect_image_type(image_bytes: bytes) -> str:
    """
    Classify image as 'prescription', 'blister_pack', or 'other'.
    Uses plain text (not JSON mode) since response is a single word.
    Defaults to 'prescription' on any error (safer fallback).
    """
    try:
        # json_mode=False — response is a single word, not JSON
        raw = await _retry_on_quota(
            lambda: asyncio.to_thread(
                _call_gemini,
                IMAGE_TYPE_DETECTION_PROMPT,
                image_bytes,
                0.0,
                10,
                False,
            )
        )
        result = raw.strip().lower().replace("'", "").replace('"', "")
        if result in ("prescription", "blister_pack", "other"):
            return result
        # Partial match fallback
        if "blister" in result or "pack" in result:
            return "blister_pack"
        if "prescription" in result or "doctor" in result:
            return "prescription"
        return "prescription"
    except Exception as e:
        logger.warning(f"Image type detection failed: {e} — defaulting to 'prescription'")
        return "prescription"


async def parse_prescription(image_bytes: bytes) -> list[dict]:
    """
    Parse a prescription image and return a list of medication dicts.
    Each dict includes _confidence_score and _review_issues (stripped before Firestore write).
    Raises ValueError if image is not a prescription.
    Returns [] if prescription but no medications found.
    """
    # Higher token budget for prescriptions (more text fields, handwriting)
    raw = await _retry_on_quota(
        lambda: asyncio.to_thread(
            _call_gemini, PRESCRIPTION_EXTRACTION_PROMPT, image_bytes, 0.1, 8192
        )
    )
    cleaned = _strip_json_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse prescription Gemini JSON (pos {e.pos}): {cleaned[:300]}")
        return []

    if isinstance(result, dict) and "error" in result:
        raise ValueError(result["error"])

    if not isinstance(result, list):
        return []

    for med in result:
        # Apply brand→generic mapping
        if not med.get("generic_name") or med.get("generic_name") == med.get("brand_name"):
            med["generic_name"] = map_brand_to_generic(med.get("brand_name", ""))
        med["source_type"] = "prescription"

        # Attach confidence score (internal — stripped before Firestore write)
        score, issues = calculate_confidence_score(med, "prescription")
        med["_confidence_score"] = score
        med["_review_issues"] = issues

    return result


async def parse_blister_pack(image_bytes: bytes) -> list[dict]:
    """
    Parse a blister pack image and return a list of medication dicts.
    Each dict includes _confidence_score and _review_issues (stripped before Firestore write).
    Raises ValueError if image is not a blister pack.
    """
    raw = await _retry_on_quota(
        lambda: asyncio.to_thread(
            _call_gemini, BLISTER_PACK_EXTRACTION_PROMPT, image_bytes, 0.1, 4096
        )
    )
    cleaned = _strip_json_fences(raw)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse blister pack Gemini JSON (pos {e.pos}): {cleaned[:300]}")
        return []

    if isinstance(result, dict) and "error" in result:
        raise ValueError(result["error"])

    if not isinstance(result, list):
        return []

    for med in result:
        # Apply brand→generic mapping
        if not med.get("generic_name") or med.get("generic_name") == med.get("brand_name"):
            med["generic_name"] = map_brand_to_generic(med.get("brand_name", ""))
        med["source_type"] = "blister_pack"

        # Attach confidence score (internal — stripped before Firestore write)
        score, issues = calculate_confidence_score(med, "blister_pack")
        med["_confidence_score"] = score
        med["_review_issues"] = issues

    return result


async def parse_medication_image(image_bytes: bytes, image_type: str = "auto") -> tuple[list[dict], str, bool]:
    """
    Unified entry point. Auto-detects or routes by explicit image_type.

    Args:
        image_bytes: Raw JPEG bytes of the image
        image_type: 'auto' | 'prescription' | 'blister_pack'

    Returns:
        (medications, detected_type, requires_review)
        requires_review=True when ANY medication has _confidence_score < 80
    """
    if image_type == "auto":
        detected = await detect_image_type(image_bytes)
    else:
        detected = image_type

    if detected == "blister_pack":
        medications = await parse_blister_pack(image_bytes)
    elif detected == "prescription":
        medications = await parse_prescription(image_bytes)
    else:
        # 'other' — not a medical image
        raise ValueError("Image does not appear to be a prescription or medicine pack.")

    requires_review = any(m.get("_confidence_score", 100) < 80 for m in medications)
    return medications, detected, requires_review
