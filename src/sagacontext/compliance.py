from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass(slots=True)
class CheckResult:
    decision: str
    reason: str = ""

def check_pattern(text: str, regex: str, reason: str = "memory convention matched") -> CheckResult:
    try: matched = re.search(regex, text) is not None
    except re.error as exc: return CheckResult("ask", f"invalid compliance regex: {exc}")
    return CheckResult("deny" if matched else "allow", reason if matched else "")
