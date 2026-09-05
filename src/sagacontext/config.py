from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
        llm_base_url = os.environ.get("SAGACONTEXT_LLM_BASE_URL", llm.get("base_url", defaults.llm_base_url))
        llm_api_key = os.environ.get("SAGACONTEXT_LLM_API_KEY", llm.get("api_key", defaults.llm_api_key))
        llm_model = os.environ.get("SAGACONTEXT_LLM_MODEL", llm.get("model", defaults.llm_model))
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
            ov_api_key=str(openviking.get("api_key", defaults.ov_api_key)),
            llm_base_url=str(llm_base_url),
            llm_api_key=str(llm_api_key),
            llm_model=str(llm_model),
        )
