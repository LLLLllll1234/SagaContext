from __future__ import annotations
import hashlib
from pathlib import Path
from .models import Candidate
from .store import Store

def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def detect(host: str, session_id: str, cwd: Path, store: Store) -> list[Candidate]:
    candidates = []
    edits = store.tool_edits(host, session_id)
    for edit in edits:
        path = Path(edit["path"])
        if not path.is_absolute(): path = cwd / path
        current = file_sha(path) if path.exists() and path.is_file() else ""
        if current != edit["sha_after"]:
            candidates.append(Candidate(level="L1", layer_guess="preference", kind="revert_and_redo",
                                        turn_idx=edit["turn_idx"], text=f"Agent edit was changed or removed: {edit['path']}",
                                        files=[edit["path"]], confidence=0.5))
    store.mark_tool_edits_checked([edit["id"] for edit in edits])
    return candidates
