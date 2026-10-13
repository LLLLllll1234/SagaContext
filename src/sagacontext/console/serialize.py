"""Conservative output sanitization, after scope checks and DTO selection."""
from __future__ import annotations

import re
from pydantic import JsonValue

HIDDEN = "[已隐藏]"
_FIELDS = {"apikey", "authorization", "endpoint", "transcriptpath", "raw", "config",
           "password", "secret", "token", "credential", "credentials", "locator",
           "sourcelocator", "workspaceroot", "realpath", "gitcommondir"}
_PATTERNS = (
    re.compile(r"-----BEGIN[^\n]*-----[\s\S]*?(?:-----END[^\n]*-----|$)"),
    re.compile(r"(?:https?|viking)://[^\s<>\"']+", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:sk-|ghp_|github_pat_)[\w-]{8,}"),
    re.compile(r"\bBearer\s+[^\s,;]+", re.I),
    re.compile(r"(?:api[_ -]?key|password|secret|token)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", re.I),
)


def safe_text(value: str) -> str:
    for pattern in _PATTERNS:
        value = pattern.sub(HIDDEN, value)
    return value


# Body precedence follows memfile.render, then the generic text fields seen in fixtures,
# then the structured fields the Judge contract writes (command, fix, path, format, ...).
SUMMARY_FIELDS = ("rule", "content", "decision", "goal", "symptom", "topic", "text", "summary")
DETAIL_FIELDS = ("command", "fix", "path", "format", "value", "applies_when")
SUMMARY_LIMIT = 160


def _line(value: JsonValue) -> str | None:
    if isinstance(value, str) and value.strip() and value != HIDDEN:
        return " ".join(value.split())
    return None


def payload_summary(payload: JsonValue) -> str | None:
    """One readable line from an already-sanitized payload, or None when it has no body.

    Judge payloads are a ``key`` plus structured fields, so ``{key, command}`` reads as
    ``key：command``; ``key`` alone is the last resort.
    """
    if not isinstance(payload, dict):
        return None
    text = next((t for t in map(_line, (payload.get(k) for k in SUMMARY_FIELDS)) if t), None)
    if text is None:
        detail = next((t for t in map(_line, (payload.get(k) for k in DETAIL_FIELDS)) if t), None)
        key = _line(payload.get("key"))
        text = f"{key}：{detail}" if key and detail else detail or key
    if text is None:
        return None
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT - 1] + "…"


def safe_payload(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {safe_text(key): HIDDEN if re.sub(r"[^a-z]", "", key.lower()) in _FIELDS
                else safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_payload(item) for item in value]
    if isinstance(value, str):
        return safe_text(value)
    return value
