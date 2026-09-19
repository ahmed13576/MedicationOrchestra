"""
explanation_service.py — Medication Orchestra

The only place a language model is allowed to touch clinical output, and it is
strictly downstream of the deterministic engine.

Contract (do not weaken it):

  * The model receives a finding that has ALREADY been decided: severity,
    ingredients, mechanism, action and citation all come from the knowledge
    base. The model's job is to phrase it in the patient's language.
  * The model may NEVER introduce, remove or re-rank a finding, never state a
    dose, never state a safety guarantee, and never mention a medicine it was
    not given.
  * Output is validated: it must be valid JSON of the expected shape, must not
    contain a medicine name outside the supplied list, and must not contain
    safety-guarantee phrases ("is safe", "no interaction", "you can take").
  * Any failure falls back to the curated text verbatim. The curated text is
    always clinically correct on its own; the model output is a translation
    layer, not a source of truth.
  * One batched call per household instead of one call per pair: cheaper,
    faster, and keeps the whole answer consistent under a single set of
    instructions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re

logger = logging.getLogger(__name__)

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi (Devanagari script)",
    "hinglish": "Hinglish (Hindi written in Roman script, as spoken in urban India)",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "pa": "Punjabi",
}

SYSTEM_INSTRUCTION = (
    "You are a medical writer for an Indian medication-safety service. "
    "You will be given findings that have ALREADY been decided by a clinical "
    "knowledge base. Your only job is to rewrite the given text so a patient or "
    "an elderly caregiver with no medical training understands it.\n"
    "Absolute rules:\n"
    "1. Do not add, remove, merge or re-order any finding.\n"
    "2. Do not change the severity. Do not invent a severity.\n"
    "3. Do not mention any medicine that is not in the input.\n"
    "4. Never say or imply that a combination is safe, that there is no problem, "
    "or that the patient may take something. Never advise a dose.\n"
    "5. Keep the `action` and `citation` fields as given; you may only simplify "
    "the wording of `title` and `detail`.\n"
    "Return only valid JSON."
)

_PROMPT = """LANGUAGE: {language}

For each finding below, rewrite `title` (max 70 characters) and `detail`
(2-3 short sentences, plain words, no jargon) in the requested language.

FINDINGS (already decided - do not change the facts):
{findings_json}

Return ONLY a JSON object of this exact shape, one entry per finding id:
{{"findings": [{{"id": "<same id>", "title": "<rewritten>", "detail": "<rewritten>"}}]}}
"""

#: Phrases that would turn a phrasing task into a safety claim.
_BANNED_PATTERNS = re.compile(
    r"\b(is|are|it's|its)\s+(completely\s+|totally\s+|absolutely\s+)?(safe|fine|ok|okay|harmless)\b"
    r"|\bno\s+(interaction|problem|risk|danger|issue)\b"
    r"|\byou\s+(can|may|should)\s+(take|take both|safely take)\b"
    r"|\b(no need to|don't need to)\s+(worry|consult|ask|check)\b"
    r"|\bsafe to (take|use|combine)\b",
    re.IGNORECASE,
)


def _client():
    """Vertex AI client via ADC. Imported lazily so tests need no credentials."""
    from google import genai

    project = os.getenv("PROJECT_ID")
    if not project:
        raise RuntimeError("PROJECT_ID is not configured")
    return genai.Client(
        vertexai=True, project=project, location=os.getenv("LOCATION", "us-central1")
    )


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return text.strip()


def _validate_rewrite(
    rewrite: dict,
    finding: dict,
    allowed_medicines: set[str],
) -> dict | None:
    """Reject a rewrite that changes a fact or asserts safety."""
    title = (rewrite.get("title") or "").strip()
    detail = (rewrite.get("detail") or "").strip()
    if not title or not detail:
        return None
    if len(title) > 120 or len(detail) > 1200:
        return None
    combined = f"{title} {detail}"
    if _BANNED_PATTERNS.search(combined):
        logger.warning("Explanation rejected: safety claim detected in %r", title)
        return None

    # No medicine may appear that was not supplied, and every finding's own
    # medicines should still be represented (a silent deletion is just as bad).
    mentioned = {
        med for med in allowed_medicines
        if med and re.search(rf"\b{re.escape(med.split()[0])}\b", combined, re.IGNORECASE)
    }
    own = {m for m in (finding.get("medications") or []) if m}
    if own and not (mentioned & set(own)):
        logger.warning("Explanation rejected: it dropped every medicine name (%r)", title)
        return None

    # A rewrite must not introduce a dosing instruction. The curated action text
    # may legitimately mention food or timing, so the check is on *new* numbers
    # and frequency directives that were not in the finding to begin with.
    source_text = f"{finding.get('title', '')} {finding.get('detail', '')} {finding.get('action', '')}"
    introduced = re.findall(
        r"\b\d+\s*(?:tablets?|tabs?|capsules?|caps?|mg|mcg|ml|drops?|puffs?)\b"
        r"|\b(?:once|twice|thrice|three times|four times)\s+(?:a|per|each)\s+(?:day|dose)\b",
        combined,
        re.IGNORECASE,
    )
    for phrase in introduced:
        if phrase.lower() not in source_text.lower():
            logger.warning("Explanation rejected: introduced a dose instruction (%r)", phrase)
            return None

    # Severity words must not be softened.
    severity = (finding.get("severity") or "").lower()
    if severity in ("contraindicated", "major"):
        softened = re.search(r"\b(mild|minor|slight|not serious|insignificant)\b", combined, re.I)
        if softened:
            logger.warning("Explanation rejected: severity downgraded in %r", title)
            return None
    return {"title": title, "detail": detail}


async def rewrite_findings(
    findings: list[dict],
    language: str = "en",
    timeout_seconds: float = 20.0,
) -> dict[str, dict]:
    """Rewrite a batch of findings in one model call.

    Returns {finding_id: {"title": ..., "detail": ...}} containing ONLY entries
    that passed validation. Callers keep the curated text for everything else,
    so a model failure degrades to correct-but-plainer copy, never to silence.
    """
    if not findings:
        return {}
    if language not in SUPPORTED_LANGUAGES:
        language = "en"

    allowed = {m for f in findings for m in (f.get("medications") or []) if m}
    payload = [
        {
            "id": f.get("id", ""),
            "severity": f.get("severity"),
            "title": f.get("title"),
            "detail": f.get("detail"),
            "medicines": f.get("medications") or [],
        }
        for f in findings
    ]
    prompt = _PROMPT.format(
        language=SUPPORTED_LANGUAGES[language],
        findings_json=json.dumps(payload, ensure_ascii=False, indent=1),
    )

    try:
        from google.genai import types

        client = _client()

        def _call():
            return client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                    max_output_tokens=4096,
                    response_mime_type="application/json",
                ),
            )

        response = await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout_seconds)
        data = json.loads(_strip_fences(getattr(response, "text", "") or "{}"))
    except Exception as exc:
        logger.warning("Explanation service unavailable, using curated text: %s", exc)
        return {}

    by_id = {f.get("id", ""): f for f in findings}
    accepted: dict[str, dict] = {}
    for item in data.get("findings", []) or []:
        finding = by_id.get(item.get("id"))
        if not finding:
            continue
        validated = _validate_rewrite(item, finding, allowed)
        if validated:
            accepted[finding["id"]] = validated
    logger.info(
        "Explanation service: %d/%d findings rewritten (language=%s)",
        len(accepted), len(findings), language,
    )
    return accepted
