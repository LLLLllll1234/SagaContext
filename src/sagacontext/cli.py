from __future__ import annotations
import asyncio, json
from pathlib import Path
import typer
from .config import Config
from .memfile import render
from .ov_client import OpenVikingClient

app = typer.Typer(no_args_is_help=True)

@app.command()
def add(topic: str, rule: str, repo_key: str = "global", confidence: float = 0.7):
    """Write a dev_convention memory to OpenViking."""
    cfg = Config.load(); uri = f"{cfg.dev_root}/convention/{repo_key}/{topic}.md"
    fields = {"version": 1, "topic": topic, "layer": "preference", "scope_key": repo_key, "rule": rule, "confidence": confidence, "evidence_count": 1, "contra_count": 0, "status": "active"}
    result = asyncio.run(OpenVikingClient(cfg.ov_base_url, cfg.ov_api_key).write(uri, render(fields)))
    typer.echo(json.dumps({"uri": uri, "result": result}, ensure_ascii=False))

@app.command()
def show(uri: str):
    """Read one memory by URI."""
    cfg = Config.load(); typer.echo(json.dumps(asyncio.run(OpenVikingClient(cfg.ov_base_url, cfg.ov_api_key).read(uri)), ensure_ascii=False, indent=2))

@app.command()
def doctor():
    """Check local configuration and daemon dependencies."""
    cfg = Config.load(); typer.echo(json.dumps({"state_path": str(cfg.state_path), "ov_base_url": cfg.ov_base_url, "dev_root": cfg.dev_root, "status": "ok"}, ensure_ascii=False))

@app.command()
def pending():
    """List unresolved reconciliation items."""
    cfg = Config.load()
    from .store import Store
    rows = Store(cfg.state_path).db.execute("SELECT * FROM pending WHERE resolved='' ORDER BY created_at DESC").fetchall()
    typer.echo(json.dumps([dict(r) for r in rows], ensure_ascii=False))

if __name__ == "__main__": app()
