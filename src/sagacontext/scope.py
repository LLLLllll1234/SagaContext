from __future__ import annotations
import hashlib, subprocess
from pathlib import Path
from .models import Scope
from .store import Store

def resolve(cwd: Path, store: Store) -> dict:
    rp = cwd.resolve()
    row = store.db.execute("SELECT * FROM repo_keys WHERE realpath=?", (str(rp),)).fetchone()
    if row: return {"repo_key": row["repo_key"], "kind": row["kind"], "git_root": row["git_root"], "branch": _branch(row["git_root"])}
    try: root = subprocess.check_output(["git", "-C", str(rp), "rev-parse", "--show-toplevel"], text=True, stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError): root = ""
    if root:
        try: first = subprocess.check_output(["git", "-C", root, "rev-list", "--max-parents=0", "HEAD"], text=True, stderr=subprocess.DEVNULL).splitlines()[0]
        except (subprocess.CalledProcessError, IndexError): first = root
        key, kind = hashlib.sha1(first.encode()).hexdigest()[:8], "first_commit"
    else: key, kind, root = hashlib.sha1(str(rp).encode()).hexdigest()[:8], "path", None
    store.db.execute("INSERT OR REPLACE INTO repo_keys VALUES (?,?,?,?)", (str(rp), key, kind, root)); store.db.commit()
    return {"repo_key": key, "kind": kind, "git_root": root, "branch": _branch(root)}

def _branch(root: str | None) -> str | None:
    if not root: return None
    try: return subprocess.check_output(["git", "-C", root, "branch", "--show-current"], text=True, stderr=subprocess.DEVNULL).strip() or None
    except subprocess.CalledProcessError: return None

def repo_scope(repo_key: str) -> Scope: return Scope(kind="repo", repo_key=repo_key)
