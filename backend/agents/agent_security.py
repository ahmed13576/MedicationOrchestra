"""
agent_security.py — Medication Orchestra

Shared security utilities for all ADK agents.
Protects against LLM prompt injection attacks that can arrive via:
  - Scanned medication brand names (e.g. malicious text on a label)
  - Prescription text containing injected instructions
  - Any user-derived string embedded in an LLM prompt

Usage:
    from agents.agent_security import sanitize_drug_input, validate_agent_output
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

# Maximum allowed length for a drug name input (characters)
MAX_INPUT_LENGTH = 120

# Allowed characters in drug names: letters, digits, spaces, and common
# pharmaceutical notation characters. Everything else is stripped.
ALLOWED_CHARS_PATTERN = re.compile(r"[^A-Za-z0-9 .,()/+\-]")

# Injection trigger phrases — if any of these appear in the input (case-insensitive),
# the input is rejected entirely and an empty string is returned.
INJECTION_TRIGGERS = [
    "ignore",
    "forget",
    "override",
    "pretend",
    "you are now",
    "you are a",
    "system:",
    "system prompt",
    "instruction",
    "jailbreak",
    "disregard",
    "do not follow",
    "new task",
    "as an ai",
    "assistant:",
    "human:",
    "<|",         # common LLM delimiter injection
    "]]",         # template injection
    "{{",         # template injection
    "```",        # code block injection attempt
    "---",        # frontmatter injection
    "\\n\\n",     # newline-based context break (literal escaped)
]

# Patterns in LLM OUTPUT that suggest a successful injection or hallucination
OUTPUT_INJECTION_PATTERNS = re.compile(
    r"(ignore|forget|override|pretend|system:|new task|jailbreak|disregard"
    r"|you are now|assistant:|human:|<\||\[\[)",
    re.IGNORECASE
)

# ── Public API ─────────────────────────────────────────────────────────────────

def sanitize_drug_input(text: str) -> str:
    """
    Sanitizes a user-derived string (drug name, brand name, generic name) before
    it is embedded in an LLM prompt.

    Steps:
    1. Type check — non-strings return empty string
    2. Strip whitespace and collapse internal newlines/tabs to single space
    3. Detect injection trigger phrases — reject if found
    4. Remove characters outside the allowed pharmaceutical character set
    5. Truncate to MAX_INPUT_LENGTH

    Returns the cleaned string, or empty string if rejected.
    """
    if not isinstance(text, str):
        logger.warning(f"sanitize_drug_input: non-string input type {type(text)}")
        return ""

    # Step 1: Normalize whitespace (newlines are a classic injection vector)
    cleaned = text.strip()
    cleaned = re.sub(r"[\n\r\t]+", " ", cleaned)
    cleaned = re.sub(r" {2,}", " ", cleaned)  # collapse multiple spaces

    # Step 2: Check for injection trigger phrases
    cleaned_lower = cleaned.lower()
    for trigger in INJECTION_TRIGGERS:
        if trigger in cleaned_lower:
            logger.warning(
                f"sanitize_drug_input: INJECTION ATTEMPT detected. "
                f"Trigger='{trigger}' in input='{text[:60]}'"
            )
            return ""

    # Step 3: Remove disallowed characters
    cleaned = ALLOWED_CHARS_PATTERN.sub("", cleaned)

    # Step 4: Truncate
    if len(cleaned) > MAX_INPUT_LENGTH:
        logger.warning(
            f"sanitize_drug_input: Input truncated from {len(cleaned)} to "
            f"{MAX_INPUT_LENGTH} chars: '{cleaned[:40]}...'"
        )
        cleaned = cleaned[:MAX_INPUT_LENGTH]

    return cleaned.strip()


def validate_agent_output(output: dict, required_keys: list) -> bool:
    """
    Validates that an LLM agent's JSON output:
    1. Contains all required keys
    2. Does not contain injection-like content in any string value

    Returns True if valid, False if the output should be rejected.
    """
    if not isinstance(output, dict):
        logger.warning("validate_agent_output: output is not a dict")
        return False

    # Check required keys
    missing = [k for k in required_keys if k not in output]
    if missing:
        logger.warning(f"validate_agent_output: missing keys {missing}")
        return False

    # Check for injection content in string values
    for key, value in output.items():
        if isinstance(value, str) and OUTPUT_INJECTION_PATTERNS.search(value):
            logger.warning(
                f"validate_agent_output: suspicious content in key '{key}': "
                f"'{value[:80]}'"
            )
            return False

    return True
