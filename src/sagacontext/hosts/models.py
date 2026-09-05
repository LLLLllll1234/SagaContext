from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class HostEventCapability(BaseModel):
    event: str
    status: Literal["observed", "not_observed", "not_probed"]
    payload_keys: list[str] = Field(default_factory=list)


class HostProbeResult(BaseModel):
    status: Literal["passed", "blocked", "failed"]
    blocker: str | None = None
    exit_code: int | None = None
    stderr_present: bool
    agent_received_injected_context: bool


class HostCapabilities(BaseModel):
    host_name: str
    host_form: str
    executable_version: str
    adapter_version: str
    config_fingerprint: str
    verified_events: list[HostEventCapability]
    injection_modes: list[str] = Field(default_factory=list)
    timeout_behavior: dict[str, str | int | bool]
    transcript_support: dict[str, str | int | bool]
    probe_date: date
    official_references: list[str] = Field(default_factory=list)
    runtime_feature_flags: dict[str, bool] = Field(default_factory=dict)
    source_declared_events: list[str] = Field(default_factory=list)
    source_revision: str | None = None
    probe_result: HostProbeResult
    payload_shapes: list[dict[str, object]] = Field(default_factory=list)

    def observed_events(self) -> set[str]:
        return {item.event for item in self.verified_events if item.status == "observed"}
