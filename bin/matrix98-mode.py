#!/usr/bin/env python3
"""Matrix98 / 8-Bit Arch Linux mode switcher & terminal backend for omalauncher."""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

OMA_DIR = Path.home() / ".config" / "omalauncher"
LAYOUT_PATH = OMA_DIR / "home" / "layout.toml"
BACKUP_DIR = OMA_DIR / "backups"
LAYOUT_BAK = BACKUP_DIR / "layout.pre-matrix98.toml"
UI_MODE_PATH = OMA_DIR / "ui-mode.json"

MATRIX98_LAYOUT = """# 8-Bit Matrix / Windows 98 Arch Linux Mode (activated via 234234)
version = 1
columns = 5

[[page]]
face = "08-matrix98.owl"
"""

DEFAULT_REGULAR_LAYOUT = """version = 1
columns = 5

[[page]]
face = "10-sith-calendar.owl"

[[page]]
face = "00-infernal.owl"

[[page]]
face = "25-sith-command-deck.owl"
"""


def write_ui_mode(mode: str) -> None:
    UI_MODE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_MODE_PATH.write_text(json.dumps({"mode": mode, "ts": int(time.time())}) + "\n")


def cmd_enter() -> dict:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if LAYOUT_PATH.exists():
        cur = LAYOUT_PATH.read_text()
        if "08-matrix98.owl" not in cur:
            shutil.copy2(LAYOUT_PATH, LAYOUT_BAK)
    LAYOUT_PATH.write_text(MATRIX98_LAYOUT)
    write_ui_mode("matrix98")
    return {"ok": True, "mode": "matrix98", "msg": "JACKED IN: 8-BIT MATRIX98"}


def cmd_exit() -> dict:
    if LAYOUT_BAK.exists():
        shutil.copy2(LAYOUT_BAK, LAYOUT_PATH)
    else:
        LAYOUT_PATH.write_text(DEFAULT_REGULAR_LAYOUT)
    write_ui_mode("regular")
    return {"ok": True, "mode": "regular", "msg": "RESTORED: REGULAR SITH MODE"}


def cmd_status() -> dict:
    mode = "regular"
    if UI_MODE_PATH.exists():
        try:
            mode = json.loads(UI_MODE_PATH.read_text()).get("mode", "regular")
        except Exception:
            pass
    return {"ok": True, "mode": mode}


def cmd_sysinfo() -> dict:
    kernel = platform.release()
    arch = platform.machine()
    host = platform.node() or "arch-98"
    uptime_s = 0
    try:
        uptime_s = int(float(Path("/proc/uptime").read_text().split()[0]))
    except Exception:
        pass
    hrs, rem = divmod(uptime_s, 3600)
    mins = rem // 60
    mem_total = mem_avail = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"):
                mem_avail = int(line.split()[1]) // 1024
    except Exception:
        pass
    mem_used = max(0, mem_total - mem_avail)
    mem_pct = int(round((mem_used / mem_total) * 100)) if mem_total > 0 else 0
    load = "0.00"
    try:
        load = Path("/proc/loadavg").read_text().split()[0]
    except Exception:
        pass
    pkgs = 0
    try:
        r = subprocess.run(["pacman", "-Qq"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0:
            pkgs = len([x for x in r.stdout.splitlines() if x.strip()])
    except Exception:
        pass
    return {
        "ok": True,
        "host": host,
        "kernel": kernel,
        "arch": arch,
        "uptime": f"{hrs}h {mins:02d}m",
        "memUsed": mem_used,
        "memTotal": mem_total,
        "memPct": mem_pct,
        "load": load,
        "pkgs": pkgs,
    }


def cmd_term(cmdline: str) -> dict:
    cmdline = cmdline.strip()
    if not cmdline:
        return {"ok": True, "code": 0, "out": "", "cmd": ""}
    try:
        r = subprocess.run(
            ["/bin/bash", "-lc", cmdline],
            cwd=str(Path.home()),
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "TERM": "dumb", "COLUMNS": "60"},
        )
        out = (r.stdout or "") + (r.stderr or "")
        out = out.strip("\n")
        if len(out) > 2400:
            out = out[:2400] + "\n... [truncated]"
        if not out:
            out = "(ok, no output)" if r.returncode == 0 else f"(exit {r.returncode})"
        return {"ok": r.returncode == 0, "code": r.returncode, "out": out, "cmd": cmdline}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "out": "ERROR: command timed out (10s)", "cmd": cmdline}
    except Exception as e:
        return {"ok": False, "code": 1, "out": f"ERROR: {e}", "cmd": cmdline}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps(cmd_status()))
        return
    sub = sys.argv[1]
    if sub == "enter":
        res = cmd_enter()
    elif sub == "exit":
        res = cmd_exit()
    elif sub == "status":
        res = cmd_status()
    elif sub == "sysinfo":
        res = cmd_sysinfo()
    elif sub == "term":
        res = cmd_term(" ".join(sys.argv[2:]))
    elif sub == "pkg-search":
        q = " ".join(sys.argv[2:]).strip()
        if not q:
            res = {"ok": False, "code": 1, "out": "Enter a package name to search (e.g. nmap, htop, neovim)", "cmd": "pacman -Ss"}
        else:
            res = cmd_term(f"pacman -Ss -- {q} | head -n 36")
    else:
        res = {"ok": False, "msg": f"unknown subcommand: {sub}"}
    print(json.dumps(res))


if __name__ == "__main__":
    main()
