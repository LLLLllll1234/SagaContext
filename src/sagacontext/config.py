from __future__ import annotations

import os
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(slots=True)
class Config:
    state_path: Path
    ledger_path: Path
    host: str = "127.0.0.1"
    port: int = 37780
    hook_timeout_ms: int = 800
    recall_budget_tokens: int = 2000
    prompt_budget_tokens: int = 600
    dev_root: str = "viking://~/memories/dev"
    ov_base_url: str = "http://127.0.0.1:1933"
    ov_api_key: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    rollout_mode: Literal["off", "shadow", "guarded"] = "off"
    rollout_workspaces: tuple[str, ...] = ()
    rollout_host: str = "codex"
    rollout_host_version: str = "codex-cli 0.153.4"
    rollout_generation: str = "g1"
    rollout_backend_namespace: str = ""
    rollout_stop_file: Path | None = None
    rollout_max_sessions: int = 10
    rollout_max_candidates: int = 20
    rollout_window_hours: int = 24
    rollout_approver: str = ""
    rollout_key_id: str = ""
    rollout_token_digest: str = ""
    rollout_previous_key_id: str = ""
    rollout_previous_token_digest: str = ""
    rollout_previous_rotated_at: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        root = Path(os.environ.get("SAGACONTEXT_HOME", Path.home() / ".sagacontext")).expanduser()
        defaults = cls(state_path=root / "state.db", ledger_path=root / "ledger-v3.db")
        path = path or root / "config.toml"
        data: dict[str, object] = {}
        if path.exists():
            import tomllib

            with path.open("rb") as fh:
                data = tomllib.load(fh)
        daemon = data.get("daemon", {}) if isinstance(data.get("daemon", {}), dict) else {}
        recall = data.get("recall", {}) if isinstance(data.get("recall", {}), dict) else {}
        openviking = data.get("openviking", {}) if isinstance(data.get("openviking", {}), dict) else {}
        llm = data.get("llm", {}) if isinstance(data.get("llm", {}), dict) else {}
        rollout = data.get("rollout", {}) if isinstance(data.get("rollout", {}), dict) else {}
        llm_base_url = os.environ.get("SAGACONTEXT_LLM_BASE_URL", llm.get("base_url", defaults.llm_base_url))
        llm_api_key = os.environ.get("SAGACONTEXT_LLM_API_KEY", llm.get("api_key", defaults.llm_api_key))
        llm_model = os.environ.get("SAGACONTEXT_LLM_MODEL", llm.get("model", defaults.llm_model))
        ov_key_file = os.environ.get("SAGACONTEXT_OV_API_KEY_FILE", openviking.get("api_key_file", ""))
        ov_key = os.environ.get("SAGACONTEXT_OV_API_KEY", openviking.get("api_key", defaults.ov_api_key))
        if not ov_key and ov_key_file:
            ov_key = Path(str(ov_key_file)).expanduser().read_text(encoding="utf-8").strip()
        requested_mode = str(
            os.environ.get("SAGACONTEXT_MODE", rollout.get("mode", defaults.rollout_mode))
        )
        mode = requested_mode if requested_mode in {"off", "shadow", "guarded"} else "off"
        workspace_value = os.environ.get("SAGACONTEXT_WORKSPACE", rollout.get("workspace", ""))
        if isinstance(workspace_value, str):
            workspaces = (workspace_value,) if workspace_value else ()
        else:
            workspaces = tuple(str(item) for item in workspace_value)
        stop_value = os.environ.get("SAGACONTEXT_STOP_FILE", rollout.get("stop_file", ""))
        token_digest = _configured_token_digest(
            env_name="SAGACONTEXT_OPERATOR_TOKEN",
            file_value=os.environ.get(
                "SAGACONTEXT_OPERATOR_TOKEN_FILE", rollout.get("token_file", "")
            ),
            configured_digest=rollout.get("token_digest", ""),
        )
        previous_token_digest = _configured_token_digest(
            env_name="SAGACONTEXT_PREVIOUS_OPERATOR_TOKEN",
            file_value=os.environ.get(
                "SAGACONTEXT_PREVIOUS_OPERATOR_TOKEN_FILE",
                rollout.get("previous_token_file", ""),
            ),
            configured_digest=rollout.get("previous_token_digest", ""),
        )
        return cls(
            state_path=root / "state.db",
            ledger_path=root / "ledger-v3.db",
            host=str(daemon.get("host", defaults.host)),
            port=int(daemon.get("port", defaults.port)),
            hook_timeout_ms=int(daemon.get("hook_timeout_ms", defaults.hook_timeout_ms)),
            recall_budget_tokens=int(
                recall.get("session_start_budget_tokens", defaults.recall_budget_tokens)
            ),
            prompt_budget_tokens=int(recall.get("prompt_budget_tokens", defaults.prompt_budget_tokens)),
            dev_root=str(openviking.get("dev_root", defaults.dev_root)),
            ov_base_url=str(openviking.get("base_url", defaults.ov_base_url)),
            ov_api_key=str(ov_key),
            llm_base_url=str(llm_base_url),
            llm_api_key=str(llm_api_key),
            llm_model=str(llm_model),
            rollout_mode=str(mode),
            rollout_workspaces=workspaces,
            rollout_host=str(rollout.get("host", defaults.rollout_host)),
            rollout_host_version=str(rollout.get("host_version", defaults.rollout_host_version)),
            rollout_generation=str(rollout.get("generation", defaults.rollout_generation)),
            rollout_backend_namespace=str(rollout.get("backend_namespace", defaults.rollout_backend_namespace)),
            rollout_stop_file=Path(str(stop_value)).expanduser() if stop_value else root / "STOP",
            rollout_max_sessions=int(rollout.get("max_sessions", defaults.rollout_max_sessions)),
            rollout_max_candidates=int(rollout.get("max_candidates", defaults.rollout_max_candidates)),
            rollout_window_hours=int(rollout.get("window_hours", defaults.rollout_window_hours)),
            rollout_approver=str(rollout.get("approver", defaults.rollout_approver)),
            rollout_key_id=str(rollout.get("key_id", defaults.rollout_key_id)),
            rollout_token_digest=token_digest,
            rollout_previous_key_id=str(
                rollout.get("previous_key_id", defaults.rollout_previous_key_id)
            ),
            rollout_previous_token_digest=previous_token_digest,
            rollout_previous_rotated_at=str(
                rollout.get("previous_rotated_at", defaults.rollout_previous_rotated_at)
            ),
        )


def _configured_token_digest(
    *, env_name: str, file_value: object, configured_digest: object
) -> str:
    token = os.environ.get(env_name)
    if token is None and file_value:
        token = Path(str(file_value)).expanduser().read_text(encoding="utf-8").strip()
    if token is not None:
        return hashlib.sha256(token.encode()).hexdigest()
    return str(configured_digest)
