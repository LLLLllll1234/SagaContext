from __future__ import annotations
import fnmatch
import json
import re
import subprocess
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from .models import Candidate, MemoryRecord

SAFE_COMMANDS = {"cargo", "go", "mypy", "npm", "pnpm", "pytest", "ruff", "uv", "yarn"}

@dataclass(slots=True)
class Rule:
    uri: str
    hint: str
    reason: str
    mode: str = "warn"
    paths: list[str] = field(default_factory=list)
    forbid_paths: list[str] = field(default_factory=list)
    regex: str = ""
    command: list[str] = field(default_factory=list)

@dataclass(slots=True)
class CheckResult:
    decision: str
    reason: str = ""
    uri: str = ""
    violation: bool = False

def compile_rules(memories: list[MemoryRecord]) -> list[Rule]:
    rules = []
    for memory in memories:
        fields = memory.fields
        if fields.get("status", "active") != "active" or float(fields.get("confidence", 0)) < 0.6:
            continue
        hint = str(fields.get("check_hint", "none"))
        if hint not in {"path", "pattern", "command"}: continue
        try:
            raw = fields.get("check_spec") or "{}"
            spec = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        command = spec.get("command", [])
        if isinstance(command, str): command = shlex.split(command)
        if hint == "command" and (not command or Path(command[0]).name not in SAFE_COMMANDS):
            continue
        rule = Rule(memory.uri, hint, str(fields.get("rule") or "memory convention matched"),
                    str(spec.get("mode", "warn")), list(spec.get("paths", [])), list(spec.get("forbid_paths", [])),
                    str(spec.get("regex", "")), list(command))
        if rule.mode not in {"warn", "block"}: rule.mode = "warn"
        rules.append(rule)
    return rules

def check_pattern(text: str, regex: str, reason: str = "memory convention matched") -> CheckResult:
    try: matched = re.search(regex, text) is not None
    except re.error as exc: return CheckResult("warn", f"invalid compliance regex: {exc}")
    return CheckResult("deny" if matched else "allow", reason if matched else "", violation=matched)

def check_path(path: str, rule: Rule) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    included = not rule.paths or any(fnmatch.fnmatch(normalized, pattern) for pattern in rule.paths)
    forbidden = any(fnmatch.fnmatch(normalized, pattern) for pattern in rule.forbid_paths)
    return included and (forbidden if rule.forbid_paths else rule.hint == "path")

def evaluate(rules: list[Rule], path: str, content: str) -> list[CheckResult]:
    results = []
    normalized = path.replace("\\", "/").lstrip("./")
    for rule in rules:
        applies = not rule.paths or any(fnmatch.fnmatch(normalized, pattern) for pattern in rule.paths)
        violated = check_path(path, rule) if rule.hint == "path" else applies and rule.hint == "pattern" and bool(rule.regex) and check_pattern(content, rule.regex).violation
        if violated:
            results.append(CheckResult("deny" if rule.mode == "block" else "warn", rule.reason, rule.uri, True))
    return results

def run_commands(rules: list[Rule], cwd: Path, timeout: float = 30.0) -> list[CheckResult]:
    results = []
    for rule in rules:
        if rule.hint != "command" or not rule.command: continue
        try:
            completed = subprocess.run(rule.command, cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False)
            if completed.returncode:
                detail = (completed.stderr or completed.stdout or rule.reason)[:500]
                results.append(CheckResult("warn", f"{rule.reason}: {detail}", rule.uri, True))
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append(CheckResult("warn", f"{rule.reason}: {exc}", rule.uri, True))
    return results

def violation_candidate(result: CheckResult, path: str, turn_idx: int = 0) -> Candidate:
    return Candidate(level="L1", layer_guess="preference", kind="compliance_violation", turn_idx=turn_idx,
                     text=f"{result.reason} ({result.uri})", files=[path], confidence=0.7)
