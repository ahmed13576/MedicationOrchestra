"""
agent_security.py — Medication Orchestra

Input sanitisation for text that reaches a prompt or the database.

Design change from the previous version: the old sanitiser *rejected* an input
outright when it contained any trigger word ("ignore", "override", "system:",
"---", or even the substring "as an ai"). For a drug name field that is a safety
bug, not a defence: an unusual but legitimate label made the medication vanish
from the check, and the app then reported the household as safe.

The approach here is to make text *inert* instead of deleting it:

  * strip control characters, template delimiters and fence markers,
  * collapse whitespace and cap length,
  * keep the characters a real drug name uses.

That removes the injection surface without ever discarding a medicine name.
Instruction-shaped text is additionally neutralised by how prompts are built:
user-derived values are never interpolated as instructions (see
services/explanation_service.py, which sends structured JSON with a fixed system
instruction, and never concatenates free text into a command position).
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

MAX_INPUT_LENGTH = 200

#: Characters allowed in a medicine name / instruction. Everything else is
#: replaced with a space rather than deleting the whole value.
_ALLOWED_RE = re.compile(r"[^A-Za-z0-9 \u0900-\u097F\.,()/+%\-:'\u00B0\u2013\u2014]")

#: Sequences that carry structural meaning to a chat template. Neutralised, not
#: used to reject the input.
_TEMPLATE_MARKERS = re.compile(
    r"(<\|[^|]*\|>)"          # <|im_start|>
    r"(\[/?INST\])"           # [INST] [/INST]
    r"(<</?SYS>>)"
    r"(```+)"
    r"(\{\{+|\}\}+)"          # template braces
    r"(^|\s)(-{3,}|={3,})(\s|$)"   # markdown fences / rules
    r"(\r?\n)+",              # newline-based context breaks
    re.IGNORECASE,
)

#: Phrases that have no place in a medicine name or dosing instruction. They are
#: removed (leaving the rest of the string intact) and logged for review.
_INSTRUCTION_PHRASES = re.compile(
    r"\b(ignore (all )?(previous|prior|above)( instructions?)?"
    r"|disregard (all )?(previous|prior|above)"
    r"|forget (everything|all|your) (instructions?|rules?)"
    r"|you are now|you are an?|pretend to be|act as (an?|the)"
    r"|system prompt|new instructions?|override (the )?(system|rules)"
    r"|jailbreak|developer mode|reveal your (prompt|instructions))\b",
    re.IGNORECASE,
)

#: Markers of a model response leaking internal reasoning into stored data.
OUTPUT_LEAK_PATTERNS = re.compile(
    r"(system prompt|my instructions|as an ai language model"
    r"|<\|im_start\|>|<\|im_end\|>|\[/?INST\])",
    re.IGNORECASE,
)


def sanitize_text_input(text: Any, max_length: int = MAX_INPUT_LENGTH) -> str:
    """Return `text` as inert, prompt-safe, length-capped plain text.

    Never returns an empty string for a non-empty input because of its content:
    dropping a medicine name silently is a clinical safety failure. Safety comes
    from neutralising structure, not from rejecting values.
    """
    if not isinstance(text, str):
        return "" if text is None else str(text)[:max_length]

    cleaned = text.strip()
    if not cleaned:
        return ""

    cleaned = _TEMPLATE_MARKERS.sub(" ", cleaned)
    cleaned = _ALLOWED_RE.sub(" ", cleaned)

    if _INSTRUCTION_PHRASES.search(cleaned):
        logger.warning("Neutralised instruction-shaped text in input: %r", text[:120])
        cleaned = _INSTRUCTION_PHRASES.sub(" ", cleaned)

    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    if len(cleaned) > max_length:
        logger.info("Input truncated from %d to %d characters", len(cleaned), max_length)
        cleaned = cleaned[:max_length].strip()
    return cleaned


#: Backwards-compatible alias. The old name accepted a "reject the whole value"
#: contract; the new implementation keeps the name so nothing silently imports a
#: missing symbol, but the behaviour is the safe one.
sanitize_drug_input = sanitize_text_input


def validate_agent_output(output: Any, required_keys: list[str]) -> bool:
    """Validate an LLM JSON payload before it is merged into clinical output.

    Structural check only. The clinical gate is that model output is never
    allowed to create, remove or re-rank a finding - see
    services/explanation_service.py, which merges only `title` and `detail`
    after its own validation.
    """
    if not isinstance(output, dict):
        logger.warning("validate_agent_output: output is not a dict")
        return False
    missing = [key for key in required_keys if key not in output]
    if missing:
        logger.warning("validate_agent_output: missing keys %s", missing)
        return False
    for key, value in output.items():
        if isinstance(value, str) and OUTPUT_LEAK_PATTERNS.search(value):
            logger.warning("validate_agent_output: leak pattern in key %r", key)
            return False
    return True
