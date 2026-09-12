"""Safe installer primitives for SagaContext V1."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import hashlib
import plistlib
import urllib.error
import urllib.request
import uuid

HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd")
MARKER = " # sagacontext-managed-v1"

def atomic_write(path: Path, data: bytes, *, backup: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup and path.exists():
        shutil.copy2(path, path.with_name(path.name + ".sagacontext-backup"))
    fd, name = tempfile.mkstemp(prefix=".sagacontext-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def merged_hooks(target: Path, repo: Path, home: Path, *, remove: bool = False) -> bytes:
    try:
        original = json.loads(target.read_text()) if target.exists() else {}
    except (ValueError, UnicodeError):
        raise ValueError("hooks.json is not valid JSON") from None
    if not isinstance(original, dict) or not isinstance(original.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain an object named hooks")
    result = copy.deepcopy(original); events = result.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = events.get(event, [])
        if not isinstance(groups, list): raise ValueError("hook event must be an array")
        kept = []
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("hook group must contain a hooks array")
            handlers = [h for h in group["hooks"] if not (isinstance(h, dict) and h.get("type") == "command" and str(h.get("command", "")).endswith(MARKER))]
            if handlers: kept.append({**group, "hooks": handlers})
        if not remove:
            command = "env " + shlex.quote("SAGACONTEXT_HOME=" + str(home)) + " " + shlex.join([str(repo / "bin/sagactl-hook"), "codex", event]) + MARKER
            kept.append({"hooks": [{"type": "command", "command": command, "timeout": 10}]})
        if kept: events[event] = kept
        else: events.pop(event, None)
    return (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()

def hook_config(repo: Path, home: Path | None = None) -> dict[str, object]:
    """Compatibility view used by setup tests and callers inspecting a plan."""
    home = home or Path.home() / ".sagacontext"
    return json.loads(merged_hooks(Path("/nonexistent"), repo, home).decode())

def install_hooks(workspace: Path, repo: Path, home: Path | None = None, *, remove: bool = False, dry_run: bool = False) -> Path:
    home = home or Path.home() / ".sagacontext"
    target = workspace / ".codex" / "hooks.json"
    if remove and not target.exists():
        print("hooks: no file to update", target)
        return target
    data = merged_hooks(target, repo, home, remove=remove)
    if dry_run: print("hooks: would update", target); return target
    backup = target.with_name(target.name + ".sagacontext-backup")
    if target.exists() and not backup.exists(): shutil.copy2(target, backup)
    atomic_write(target, data); return target

def deployment_status(port: int) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/console/v1/deployment", timeout=1) as response:
            value = json.loads(response.read(65536))
        return value if isinstance(value, dict) and value.get("product") == "sagacontext" else None
    except (OSError, ValueError, urllib.error.URLError): return None

def process_identity(pid: int) -> str | None:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], text=True, capture_output=True, check=False)
    return result.stdout.strip() or None

def state(home: Path) -> dict | None:
    path = home / "setup-process.json"
    if not path.exists(): return None
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError):
        return None
    return value if isinstance(value, dict) else None

def owned_process(home: Path) -> dict | None:
    value = state(home)
    if not value or not isinstance(value.get("pid"), int) or value.get("pid", 0) <= 0:
        return None
    identity = value.get("identity")
    return value if isinstance(identity, str) and process_identity(value["pid"]) == identity else None

def setup_lock(home: Path):
    import fcntl
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = (home / "setup.lock").open("a")
    try: fcntl.flock(stream, fcntl.LOCK_EX)
    except Exception: stream.close(); raise
    return stream

def daemon_env(repo: Path, home: Path, instance_id: str) -> dict[str, str]:
    env = os.environ.copy(); env.update(SAGACONTEXT_HOME=str(home), SAGACONTEXT_MODE="off", SAGACONTEXT_SETUP_INSTANCE_ID=instance_id, PYTHONPATH=str(repo / "src")); return env

def start_daemon(repo: Path, home: Path, port: int) -> None:
    instance = uuid.uuid4().hex; log = (home / "setup-daemon.log").open("ab")
    process = subprocess.Popen([str(repo / ".venv/bin/python"), "-m", "sagacontext.daemon"], cwd=repo, env=daemon_env(repo, home, instance), stdout=log, stderr=log, start_new_session=True)
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None: break
            value = deployment_status(port)
            if value and value.get("instance_id") == instance:
                identity = process_identity(process.pid); atomic_write(home / "setup-process.json", (json.dumps({"pid": process.pid, "identity": identity, "instance_id": instance}) + "\n").encode()); print("daemon: running"); return
            time.sleep(.2)
        raise ValueError("daemon startup failed; inspect setup-daemon.log")
    except BaseException:
        if process.poll() is None: process.terminate(); process.wait(timeout=5)
        raise
    finally: log.close()

def stop_daemon(home: Path) -> None:
    value = owned_process(home)
    if not value: print("daemon: no matching managed process"); return
    os.kill(int(value["pid"]), signal.SIGTERM); deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process_identity(int(value["pid"])) != value["identity"]: (home / "setup-process.json").unlink(missing_ok=True); print("daemon: stopped"); return
        time.sleep(.2)
    raise ValueError("managed daemon did not stop")

def service_spec(repo: Path, home: Path, *, platform: str | None = None) -> tuple[Path, bytes, list[list[str]], list[list[str]]]:
    platform = platform or __import__('sys').platform
    label = "local.sagacontext." + hashlib.sha256(str(home).encode()).hexdigest()[:12]
    command = [str(repo / ".venv/bin/python"), "-m", "sagacontext.daemon"]
    if platform == "darwin":
        path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
        data = plistlib.dumps({
            "Label": label,
            "ProgramArguments": command,
            "EnvironmentVariables": {
                "SAGACONTEXT_HOME": str(home),
                "SAGACONTEXT_MODE": "off",
                "PYTHONPATH": str(repo / "src"),
            },
            "RunAtLoad": True,
            "WorkingDirectory": str(repo),
            "StandardOutPath": str(home / "service.log"),
            "StandardErrorPath": str(home / "service.log"),
        })
        domain = f"gui/{__import__('os').getuid()}"
        return path, data, [["launchctl", "bootstrap", domain, str(path)]], [["launchctl", "bootout", domain + "/" + label]]
    if platform != "linux": raise ValueError("user services require macOS or Linux")
    def quote(v: str) -> str: return '"' + v.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    name = label + ".service"; path = Path.home() / ".config/systemd/user" / name
    data = ("[Unit]\nDescription=SagaContext V1 local console\n[Service]\nType=simple\n"
            + "Environment=\"SAGACONTEXT_HOME=" + str(home).replace('\\', '\\\\').replace('"', '\\"') + "\"\n"
            + "Environment=SAGACONTEXT_MODE=off\n"
            + "Environment=\"PYTHONPATH=" + str(repo / 'src').replace('\\', '\\\\').replace('"', '\\"') + "\"\n"
            + "ExecStart=" + " ".join(quote(x) for x in command) + "\n[Install]\nWantedBy=default.target\n").encode()
    return path, data, [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", name]], [["systemctl", "--user", "disable", "--now", name]]

def manage_service(repo: Path, home: Path, *, remove: bool, dry_run: bool = False) -> None:
    path, data, install_commands, remove_commands = service_spec(repo, home)
    if dry_run: print("service: would remove" if remove else "service: would install"); return
    if remove:
        for command in remove_commands:
            subprocess.run(command, check=False, capture_output=True)
        path.unlink(missing_ok=True); print("service: removed"); return
    if path.exists() and path.read_bytes() != data: raise ValueError("service file exists with different contents")
    if not path.exists(): atomic_write(path, data)
    for command in install_commands: subprocess.run(command, check=True, capture_output=True)
    print("service: installed and started")
