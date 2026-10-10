"""Read-only deployment facts for the local console.

This module deliberately only inspects objects already held by the daemon. It
does not construct application services, read the ledger, or probe dependencies.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request


def deployment_status(request: Request) -> dict[str, object]:
    """Return configuration facts that are safe to expose to the loopback UI."""
    config = request.app.state.runtime.config
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        scheduler_status = "disabled"
    elif getattr(scheduler, "error_class", None):
        scheduler_status = "error"
    elif getattr(getattr(scheduler, "thread", None), "is_alive", lambda: False)():
        scheduler_status = "running"
    else:
        scheduler_status = "unknown"

    static_root = Path(__file__).parent / "_static"
    return {
        "product": "sagacontext",
        "version": "1.0.0",
        "instance_id": getattr(
            request.app.state,
            "instance_id",
            "fixture" if getattr(request.app.state, "console_fixture", False) else "uninitialized",
        ),
        "rollout_mode": config.rollout_mode,
        "worker_enabled": config.rollout_worker_enabled,
        "scheduler": scheduler_status,
        "stop_active": bool(config.rollout_stop_file and config.rollout_stop_file.exists()),
        "openviking_configured": bool(config.ov_api_key),
        "llm_configured": bool(
            config.llm_base_url and config.llm_api_key and config.llm_model
        ),
        "console_assets_available": (static_root / "index.html").is_file(),
    }
