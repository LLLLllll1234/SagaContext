from __future__ import annotations
import json
import re
from dataclasses import dataclass
from pathlib import Path

@dataclass(slots=True)
class Turn:
    idx: int
    role: str
    text: str

def read_incremental(path: Path | None, offset: int = 0):
    if isinstance(path, str): path = Path(path)
    if not path or not path.exists(): return [], offset
    turns, position, idx, failures = [], offset, 0, 0
    with path.open("rb") as fh:
        fh.seek(offset)
        for line in fh:
            position += len(line)
            if not line.endswith(b"\n"): position -= len(line); break
            try: item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError): failures += 1; continue
            payload = item.get("payload", item) if item.get("type") == "response_item" else item
            role = payload.get("role") or payload.get("message", {}).get("role")
            message = payload.get("message", payload)
            content = message.get("content", payload.get("text", "")) if isinstance(message, dict) else ""
            if isinstance(content, list):
                content = " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") in {"text", "input_text", "output_text"})
            if role in {"user", "assistant"} and str(content).strip():
                turns.append(Turn(idx=idx, role=role, text=str(content).strip())); idx += 1
    return turns, position

def read_edit_attempts(path: Path | str | None, offset: int = 0):
    if isinstance(path, str): path = Path(path)
    if not path or not path.exists(): return [], offset
    edits, position = [], offset
    with path.open("rb") as fh:
        fh.seek(offset)
        for line in fh:
            position += len(line)
            if not line.endswith(b"\n"): position -= len(line); break
            try: item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError): continue
            payload = item.get("payload", item)
            if payload.get("type") != "function_call" or payload.get("name") not in {"apply_patch", "write_file", "edit_file"}: continue
            arguments = payload.get("arguments", "")
            try: arguments = json.loads(arguments) if isinstance(arguments, str) and arguments.startswith("{") else arguments
            except json.JSONDecodeError: pass
            text = "\n".join(str(arguments.get(key, "")) for key in ("patch", "input", "content")) if isinstance(arguments, dict) else str(arguments)
            paths = re.findall(r"\*\*\* (?:Update|Add) File: ([^\n]+)", text)
            content = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
            edits.extend((path.strip(), content) for path in paths)
    return edits, position
