#!/usr/bin/env python3
"""Sith assume-breach audit (defensive, read-only on the system).
n=0 flip of "we're secure": assume something already got in and look for evidence.
Stdlib only, no network. Writes only its own state file (a baseline of what you accepted).

Usage:
  sith-breach-audit.py status   JSON for the panel (default); flags NEW items vs baseline
  sith-breach-audit.py accept   record current findings as known-good (baseline)
  sith-breach-audit.py report   human-readable text
"""
import hashlib, json, os, stat, sys, time
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "sith-breach-audit" / "baseline.json"
RECENT_DAYS = 7
SECRET_HINTS = ("token", "key", "secret", "pass", "cred", ".env")
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}

def listening_tcp():
    ports = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            for line in Path(f).read_text().splitlines()[1:]:
                parts = line.split()
                if len(parts) > 3 and parts[3] == "0A":
                    ports.add(int(parts[1].rsplit(":", 1)[1], 16))
        except (OSError, ValueError, IndexError):
            pass
    return sorted(ports)

def walk(root, limit=2000):
    n = 0
    if not root.is_dir():
        return
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            n += 1
            if n > limit:
                return
            yield Path(dp) / fn

def findings():
    """Each finding has a stable id so the baseline can recognise it next time."""
    out = []
    now = time.time()
    bin_dir = HOME / ".local/bin"
    cfg_dir = HOME / ".config"
    for p in walk(bin_dir):
        try:
            st = p.stat()
        except OSError:
            continue
        if now - st.st_mtime < RECENT_DAYS * 86400:
            out.append({"id": f"recent:{p}", "kind": "recent-exec", "path": str(p),
                        "note": f"changed {int((now - st.st_mtime) // 3600)}h ago"})
        if st.st_mode & stat.S_IWOTH:
            out.append({"id": f"ww:{p}", "kind": "world-writable", "path": str(p),
                        "note": "other users can change this"})
    for p in walk(cfg_dir) if cfg_dir.is_dir() else []:
        if any(h in p.name.lower() for h in SECRET_HINTS):
            try:
                mode = p.stat().st_mode
            except OSError:
                continue
            if mode & (stat.S_IRGRP | stat.S_IROTH):
                out.append({"id": f"secret:{p}", "kind": "secret-readable", "path": str(p),
                            "note": f"mode {oct(mode & 0o777)}: others can read"})
    ak = HOME / ".ssh/authorized_keys"
    if ak.exists():
        try:
            n = sum(1 for l in ak.read_text().splitlines() if l.strip() and not l.startswith("#"))
            out.append({"id": "ssh:authorized_keys", "kind": "ssh-trust", "path": str(ak),
                        "note": f"{n} key(s) can log in here"})
        except OSError:
            pass
    # 5. Persistence: files that run every login. Each gets a hash in its id, so an edit shows as NEW.
    watch = [HOME / n for n in (".bashrc", ".bash_profile", ".profile", ".zshrc")]
    for d in (HOME / ".config/systemd/user", HOME / ".config/autostart"):
        if d.is_dir():
            watch += sorted(d.iterdir())
    for p in watch:
        if not p.is_file():
            continue
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        out.append({"id": f"persist:{p}:{digest}", "kind": "persistence", "path": str(p),
                    "note": f"runs at login · sha256 {digest}"})
    for port in listening_tcp():
        out.append({"id": f"port:{port}", "kind": "listening", "path": f"tcp/{port}",
                    "note": "accepts connections on this device"})
    return out

def load_baseline():
    try:
        d = json.loads(STATE.read_text())
        return set(d.get("ids", [])) if isinstance(d, dict) else set()
    except Exception:
        return None  # no baseline yet

def save_baseline(ids):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ids": sorted(ids), "accepted": int(time.time())}))
    os.replace(tmp, STATE)

def status():
    fs = findings()
    base = load_baseline()
    for f in fs:
        f["new"] = base is None or f["id"] not in base
    return {"ok": True, "count": len(fs), "new": sum(f["new"] for f in fs),
            "baselined": base is not None, "findings": fs[:40], "ts": int(time.time())}

def main(argv):
    act = argv[1] if len(argv) > 1 else "status"
    if act == "status":
        return status()
    if act == "accept":
        save_baseline({f["id"] for f in findings()})
        return {**status(), "accepted_now": True}
    if act == "report":
        s = status()
        print(f"ASSUME-BREACH AUDIT · {s['count']} item(s), {s['new']} new since baseline")
        for f in s["findings"]:
            tag = "NEW" if f["new"] else "known"
            print(f"  [{tag}] {f['kind']}  {f['path']}  — {f['note']}")
        return None
    return {"ok": False, "error": f"unknown action {act!r}"}

if __name__ == "__main__":
    try:
        out = main(sys.argv)
        if out is not None:
            print(json.dumps(out))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:200], "count": 0, "new": 0,
                          "baselined": False, "findings": []}))
