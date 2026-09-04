from __future__ import annotations
import json
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
