#!/usr/bin/env python3
"""G3-CC interactive smoke: prepare a temporary workspace, then record what one real
interactive session (open claude, send one message, /exit) produced.

  prepare --dir D      create D/workspace with recorder hooks
  run --dir D          open interactive claude there (provider env passed, user hooks not loaded)
  collect --dir D --capture C   summarise D's receipts into C["interactive_smoke"], remove D

Receipts are the same redacted shapes as the -p probe; nothing raw is kept.
"""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import probe_claude_host as probe  # noqa: E402

EXPECTED = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")


def prepare(directory: Path) -> dict:
    if directory.exists():
        raise ValueError("refusing_to_reuse_directory")
    workspace = directory / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / "synthetic.txt").write_text("synthetic probe only\n")
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    config = probe._hooks_config(recorder=HERE / "g3_hook_recorder.py", log=directory / "hook-events.jsonl",
                                 state=directory / "recorder-state.json", probe_id="g3cc-interactive",
                                 scenario="interactive_smoke", started_ns=time.monotonic_ns(), marker="")
    (workspace / ".claude/settings.local.json").write_text(json.dumps(config, indent=2) + "\n")
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} run --dir {shlex.quote(str(directory))}"
    return {"workspace": str(workspace), "run": command}


def run(directory: Path) -> None:
    """Replace this process with interactive Claude Code in the smoke workspace.

    --setting-sources excludes user settings, which is where provider variables live,
    so pass only those (ANTHROPIC_*) through the environment, as the -p probe does.
    Values never reach argv, files or output; execve keeps the terminal attached.
    """
    import os
    workspace = directory / "workspace"
    if not (workspace / ".claude/settings.local.json").exists():
        raise ValueError("workspace_not_prepared")
    env = dict(os.environ)
    env.update({k: v for k, v in probe._child_env(directory / "unused")[0].items() if k.startswith(probe.PROVIDER_ENV_PREFIXES)})
    env["DISABLE_AUTOUPDATER"] = "1"
    os.chdir(workspace)
    os.execve(probe._executable(), [probe._executable(), "--setting-sources", "project,local"], env)


def collect(directory: Path, capture_path: Path) -> dict:
    records = [json.loads(line) for line in (directory / "hook-events.jsonl").read_text().splitlines() if line.strip()] \
        if (directory / "hook-events.jsonl").exists() else []
    counts = Counter(str(r.get("hook_event_name")) for r in records)
    sessions = {r.get("session_ref") for r in records if r.get("session_ref")}
    sources = sorted({(r.get("safe_enums") or {}).get("source", "absent") for r in records if r.get("hook_event_name") == "SessionStart"})
    end_reasons = sorted({(r.get("safe_enums") or {}).get("reason", "absent") for r in records if r.get("hook_event_name") == "SessionEnd"})
    ok = all(counts.get(e, 0) >= 1 for e in EXPECTED) and len(sessions) == 1 and "startup" in sources
    smoke = {"status": "pass" if ok else "not_observed", "event_counts": dict(sorted(counts.items())),
             "session_count": len(sessions), "session_start_sources": sources, "session_end_reasons": end_reasons,
             "payload_shapes": records}
    capture = json.loads(capture_path.read_text())
    capture["interactive_smoke"] = smoke
    capture_path.write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")
    shutil.rmtree(directory)
    return {k: v for k, v in smoke.items() if k != "payload_shapes"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "run", "collect"))
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        print(json.dumps(prepare(args.dir), indent=2))
        return 0
    if args.action == "run":
        run(args.dir)
    if not args.capture:
        parser.error("--capture required")
    result = collect(args.dir, args.capture)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
