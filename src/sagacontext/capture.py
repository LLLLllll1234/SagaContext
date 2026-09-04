from __future__ import annotations
import re
from .models import Candidate

RULES = [
    ("zh_negation", "preference", "explicit_negation", 0.6, re.compile(r"(?:不要|别|不用|不准|禁止)\s*(?:再)?\s*(?P<what>.{2,60}?)(?:[，。；,!！]|$)")),
    ("en_negation", "preference", "explicit_negation", 0.6, re.compile(r"\b(?:don't|do not|never|stop)\s+(?:using\s+|use\s+)?(?P<what>[^.,;!]{2,60})", re.I)),
    ("zh_always", "preference", "explicit_instruction", 0.7, re.compile(r"(?:以后|今后|所有|一律|统一)\s*(?P<what>.{2,80})")),
    ("en_always", "preference", "explicit_instruction", 0.7, re.compile(r"\b(?:always|from now on)\s+(?P<what>[^.!?]{2,80})", re.I)),
    ("zh_decision", "project", "decision_stmt", 0.6, re.compile(r"(?:我们决定|决定|改用|换成|统一用)\s*(?P<what>.{2,60}?)(?:因为|，|,|。|$)")),
    ("en_decision", "project", "decision_stmt", 0.6, re.compile(r"\b(?:we (?:decided|chose|switched) to|let's (?:use|go with))\s+(?P<what>[^.,;]{2,60})", re.I)),
    ("task_stmt", "task", "task_stmt", 0.5, re.compile(r"^(?:帮我|请|我要|我想|implement|add|fix|refactor|migrate|实现|修复|重构|迁移)\b\s*(?P<what>.{4,120})", re.I)),
]

def detect(text: str, turn_idx: int = 0) -> list[Candidate]:
    out = []
    for _, layer, kind, confidence, pattern in RULES:
        match = pattern.search(text.strip())
        if match:
            value = match.groupdict().get("what", "").strip(" ，,。.!！")
            if value:
                out.append(Candidate(level="L0", layer_guess=layer, kind=kind, turn_idx=turn_idx, text=value, confidence=confidence))
    return out
