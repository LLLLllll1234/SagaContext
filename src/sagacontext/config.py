from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass(slots=True)
class Config:
    state_path: Path
    ledger_path: Path | None = None
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
        defaults = cls(state_path=Path("state.db"))
        root = Path(os.getenv("SAGACONTEXT_HOME", Path.home() / ".sagacontext"))
        path = path or root / "config.toml"
        values: dict[str, str] = {}
        if path.exists():
            import tomllib
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            values = {k: str(v) for k, v in data.get("daemon", {}).items()}
            values.update({"dev_root": str(data.get("openviking", {}).get("dev_root", defaults.dev_root)),
                           "ov_base_url": str(data.get("openviking", {}).get("base_url", defaults.ov_base_url)),
                           "ov_api_key": str(data.get("openviking", {}).get("api_key", ""))})
        llm = data.get("llm", {}) if path.exists() else {}
        return cls(state_path=root / "state.db", ledger_path=root / "ledger-v3.db", host=values.get("host", defaults.host),
                   port=int(values.get("port", defaults.port)),
                   hook_timeout_ms=int(values.get("hook_timeout_ms", defaults.hook_timeout_ms)),
                   recall_budget_tokens=int(data.get("recall", {}).get("session_start_budget_tokens", defaults.recall_budget_tokens)) if path.exists() else defaults.recall_budget_tokens,
                   dev_root=values.get("dev_root", defaults.dev_root), ov_base_url=values.get("ov_base_url", defaults.ov_base_url),
                   ov_api_key=values.get("ov_api_key", defaults.ov_api_key),
                   llm_base_url=str(llm.get("base_url", "")), llm_api_key=str(llm.get("api_key", "")), llm_model=str(llm.get("model", "")))
