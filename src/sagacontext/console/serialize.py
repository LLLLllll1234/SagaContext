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


def safe_payload(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {safe_text(key): HIDDEN if re.sub(r"[^a-z]", "", key.lower()) in _FIELDS
                else safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_payload(item) for item in value]
    if isinstance(value, str):
        return safe_text(value)
    return value
