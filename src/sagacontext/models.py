from __future__ import annotations
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, Field

class Scope(BaseModel):
    kind: Literal["global", "repo", "glob"]
    repo_key: str | None = None
    pattern: str | None = None
    @property
    def key(self) -> str:
        if self.kind == "global": return "global"
        if self.kind == "repo": return f"repo-{self.repo_key}"
        import hashlib
        digest = hashlib.sha1((self.pattern or "").encode()).hexdigest()[:8]
        return f"glob-{digest}"
    @property
    def specificity(self) -> float:
        return {"glob": 1.2, "repo": 1.0, "global": 0.8}[self.kind]

class MemoryRecord(BaseModel):
    uri: str
    type: str
    fields: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    version: int = 1
    score: float | None = None

class HostEvent(BaseModel):
    host: Literal["claude-code", "codex"]
    event: Literal["session_start", "prompt", "pre_tool", "post_tool", "stop", "session_end", "pre_compact"]
    session_id: str
    cwd: Path
    transcript_path: Path | None = None
    prompt: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

class Candidate(BaseModel):
    level: Literal["L0", "L1"]
    layer_guess: Literal["preference", "project", "task", "user"]
    kind: str
    turn_idx: int = 0
    text: str
    files: list[str] = Field(default_factory=list)
    confidence: float = 0.5
