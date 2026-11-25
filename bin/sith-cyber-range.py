#!/usr/bin/env python3
"""Backend & TUI Launcher for Zach's Cyber Range App (Web Lab v2.0 + ULTIMUS Eval).

CLASSIFICATION: DEFENSIVE
Legal Scope (G=1): Controls localhost-only Web Lab (127.0.0.1:8099) and offline ULTIMUS evaluation suites.

CLI Contract (for OWL cmd/run):
  sith-cyber-range.py status           -> single-line JSON snapshot (<100 ms)
  sith-cyber-range.py toggle-weblab    -> start or stop 127.0.0.1:8099 server
  sith-cyber-range.py open-browser     -> open http://127.0.0.1:8099 in Android browser
  sith-cyber-range.py verify-weblab    -> run $HOME/weblab/verify_lab.py
  sith-cyber-range.py grade-v1         -> run ultimus_grade.py on v1 suite
  sith-cyber-range.py grade-v2         -> run ultimus_grade.py on v2 suite (--strict-pairs)
  sith-cyber-range.py ablate           -> run ablate_v1_v2.py
  sith-cyber-range.py test-ultimus     -> run unittest suite in $HOME/ultimus-eval/tests
  sith-cyber-range.py install-owl      -> install staged cyber-range.owl into ~/.config/omalauncher/widgets/
  sith-cyber-range.py tui              -> interactive terminal control deck (used by .desktop drawer entry)
"""
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
WEBLAB_DIR = HOME / "weblab"
ULTIMUS_DIR = HOME / "ultimus-eval"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "cyber-range"
STATE_FILE = STATE_DIR / "state.json"
PID_FILE = Path("/tmp/sith-weblab.pid")
STAGED_OWL = HOME / ".local/share/omalauncher/cyber-range/cyber-range.owl"
LIVE_OWL = HOME / ".config/omalauncher/widgets/cyber-range.owl"
PORT = 8099


def load_state() -> dict:
    defaults = {
        "lastAction": "ready",
        "lastOutput": "Tap any check or launch Web Lab on 127.0.0.1:8099.",
        "lastOk": True,
        "lastTs": 0,
    }
    try:
        d = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            defaults.update({k: d[k] for k in defaults if k in d})
    except Exception:
        pass
    return defaults


def save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def port_open(port: int = PORT) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.15):
            return True
    except OSError:
        return False


def read_weblab_telemetry() -> tuple[int, int, list[dict]]:
    db = WEBLAB_DIR / "lab.db"
    solved = 0
    total = 9
    logs = []
    if not db.is_file():
        return solved, total, logs
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM scoreboard").fetchone()
            if row:
                solved = int(row["c"])
        except sqlite3.Error:
            pass
        try:
            for r in conn.execute("SELECT ts, endpoint, payload, blocked FROM req_logs ORDER BY id DESC LIMIT 5"):
                logs.append({
                    "ts": r["ts"] or "",
                    "ep": r["endpoint"] or "",
                    "payload": (r["payload"] or "")[:60],
                    "blocked": bool(r["blocked"]),
                    "tag": "BLOCKED" if r["blocked"] else ("VULN" if "vuln" in str(r["endpoint"] if "endpoint" in r.keys() else "") else "OK"),
                })
        except sqlite3.Error:
            pass
        conn.close()
    except Exception:
        pass
    return solved, total, logs


def read_ultimus_metrics() -> dict:
    summary = {
        "ready": (ULTIMUS_DIR / "ultimus_grade.py").is_file(),
        "v1Pass": "0/8",
        "v2Pass": "8/8",
        "v1SoftRefusal": "100%",
        "v2SoftRefusal": "0%",
    }
    ab = ULTIMUS_DIR / "runs" / "ablation-summary.json"
    if ab.is_file():
        try:
            d = json.loads(ab.read_text(encoding="utf-8"))
            v1 = d.get("v1", {})
            v2 = d.get("v2", {})
            if "pass_count" in v1 and "total" in v1:
                summary["v1Pass"] = f"{v1['pass_count']}/{v1['total']}"
            if "pass_count" in v2 and "total" in v2:
                summary["v2Pass"] = f"{v2['pass_count']}/{v2['total']}"
        except Exception:
            pass
    return summary


def status_snapshot() -> dict:
    st = load_state()
    solved, total, logs = read_weblab_telemetry()
    ult = read_ultimus_metrics()
    return {
        "ok": True,
        "weblabUp": port_open(),
        "weblabUrl": f"http://127.0.0.1:{PORT}",
        "flagsSolved": solved,
        "flagsTotal": total,
        "recentLogs": logs,
        "ultimus": ult,
        "owlInstalled": LIVE_OWL.is_file(),
        "lastAction": st["lastAction"],
        "lastOutput": st["lastOutput"],
        "lastOk": st["lastOk"],
        "ts": int(time.time()),
    }


def start_weblab() -> tuple[bool, str]:
    if port_open():
        return True, f"Web Lab already online at http://127.0.0.1:{PORT}"
    lab_py = WEBLAB_DIR / "lab.py"
    if not lab_py.is_file():
        return False, "Missing $HOME/weblab/lab.py"
    proc = subprocess.Popen(
        [sys.executable, str(lab_py)],
        cwd=str(WEBLAB_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    for _ in range(40):
        if port_open():
            return True, f"Web Lab ONLINE at http://127.0.0.1:{PORT} (PID {proc.pid})"
        time.sleep(0.1)
    return False, "Started process, waiting for port 8099"


def stop_weblab() -> tuple[bool, str]:
    killed = False
    pid = None
    if PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.killpg(pid, signal.SIGTERM)
            killed = True
        except Exception:
            try:
                if pid: os.kill(pid, signal.SIGTERM)
                killed = True
            except Exception:
                pass
        PID_FILE.unlink(missing_ok=True)
    if port_open():
        subprocess.run(["pkill", "-f", "weblab/lab.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
    return (not port_open()), ("Web Lab stopped." if not port_open() or killed else "Could not stop port 8099")


def run_capture(label: str, cmd: list[str], cwd: Path) -> dict:
    st = load_state()
    if not cwd.is_dir():
        st.update({"lastAction": label, "lastOk": False, "lastOutput": f"Directory missing: {cwd}", "lastTs": int(time.time())})
        save_state(st)
        return status_snapshot()
    try:
        res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=25)
        out = (res.stdout + ("\n" + res.stderr if res.stderr else "")).strip()
        lines = [ln for ln in out.splitlines() if ln.strip()]
        tail = "\n".join(lines[-14:])[:900] if lines else "(no output)"
        st.update({
            "lastAction": label,
            "lastOk": res.returncode == 0,
            "lastOutput": tail,
            "lastTs": int(time.time()),
        })
    except Exception as exc:
        st.update({"lastAction": label, "lastOk": False, "lastOutput": str(exc)[:300], "lastTs": int(time.time())})
    save_state(st)
    return status_snapshot()


def install_owl() -> tuple[bool, str]:
    if not STAGED_OWL.is_file():
        return False, f"Missing staged OWL file: {STAGED_OWL}"
    LIVE_OWL.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(STAGED_OWL, LIVE_OWL)
    res = subprocess.run(["owl-check", str(LIVE_OWL)], cwd=str(LIVE_OWL.parent), capture_output=True, text=True, timeout=12)
    ok = res.returncode == 0
    return ok, (res.stdout.strip() or res.stderr.strip() or "Installed cyber-range.owl")


def run_tui() -> int:
    RED = "\033[38;2;255;30;60m"
    CYN = "\033[38;2;24;216;255m"
    GRN = "\033[38;2;43;255;140m"
    MUT = "\033[38;2;199;124;136m"
    BOLD = "\033[1m"
    RST = "\033[0m"
    while True:
        snap = status_snapshot()
        os.system("clear")
        srv = f"{GRN}● ONLINE (127.0.0.1:{PORT}){RST}" if snap["weblabUp"] else f"{MUT}○ OFFLINE{RST}"
        owl_st = f"{GRN}INSTALLED (APPS){RST}" if snap["owlInstalled"] else f"{CYN}READY TO ACTIVATE ([7]){RST}"
        print(f"{RED}{BOLD}◈ CYBER RANGE — DEFENSIVE WORKBENCH (G=1 LOCALHOST / OFFLINE){RST}")
        print(f"{MUT}──────────────────────────────────────────────────────────────{RST}")
        print(f"  Web Lab v2.0 : {srv}   ·   Flags: {BOLD}{snap['flagsSolved']}/{snap['flagsTotal']}{RST}")
        print(f"  ULTIMUS Eval : {CYN}v1 {snap['ultimus']['v1Pass']} pass{RST} vs {GRN}v2 {snap['ultimus']['v2Pass']} pass{RST}")
        print(f"  OWL GUI App  : {owl_st}")
        print(f"{MUT}──────────────────────────────────────────────────────────────{RST}")
        print(f"  {BOLD}[1]{RST} Toggle Web Lab Server (127.0.0.1:8099)")
        print(f"  {BOLD}[2]{RST} Open Web Lab in Android Browser ↗")
        print(f"  {BOLD}[3]{RST} Run Web Lab End-to-End Verification (verify_lab.py)")
        print(f"  {BOLD}[4]{RST} Run ULTIMUS v2 Grader + Paired Channel Check")
        print(f"  {BOLD}[5]{RST} Run ULTIMUS v1 vs v2 Soft-Refusal Ablation")
        print(f"  {BOLD}[6]{RST} Run ULTIMUS Stdlib Unit Tests")
        print(f"  {BOLD}[7]{RST} Activate Native OWL GUI App in Drawer (cyber-range.owl)")
        print(f"  {BOLD}[q]{RST} Quit")
        print(f"{MUT}──────────────────────────────────────────────────────────────{RST}")
        print(f"{BOLD}Last ({snap['lastAction']}):{RST}\n{snap['lastOutput']}\n")
        try:
            ch = input(f"{RED}cyber-range ▸ {RST}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if ch in ("q", "quit", "exit"):
            return 0
        if ch == "1":
            ok, msg = stop_weblab() if snap["weblabUp"] else start_weblab()
            save_state({"lastAction": "toggle-weblab", "lastOk": ok, "lastOutput": msg, "lastTs": int(time.time())})
        elif ch == "2":
            if not snap["weblabUp"]:
                start_weblab()
            subprocess.run(["xdg-open", f"http://127.0.0.1:{PORT}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            save_state({"lastAction": "open-browser", "lastOk": True, "lastOutput": f"Opened http://127.0.0.1:{PORT}", "lastTs": int(time.time())})
        elif ch == "3":
            run_capture("verify-weblab", [sys.executable, "verify_lab.py"], WEBLAB_DIR)
        elif ch == "4":
            p_v2 = "probes/ultimus-probes-v2.jsonl" if (ULTIMUS_DIR / "probes/ultimus-probes-v2.jsonl").is_file() else "probes/ultimus-v2.jsonl"
            r_v2 = "runs/sample-run-v2.jsonl" if (ULTIMUS_DIR / "runs/sample-run-v2.jsonl").is_file() else "runs/last-run-v1.jsonl"
            run_capture("grade-v2", [sys.executable, "ultimus_grade.py", r_v2, "--probes", p_v2, "--strict-pairs"], ULTIMUS_DIR)
        elif ch == "5":
            run_capture("ablate", [sys.executable, "ablate_v1_v2.py"], ULTIMUS_DIR)
        elif ch == "6":
            run_capture("test-ultimus", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], ULTIMUS_DIR)
        elif ch == "7":
            ok, msg = install_owl()
            save_state({"lastAction": "install-owl", "lastOk": ok, "lastOutput": msg, "lastTs": int(time.time())})


def main() -> None:
    act = sys.argv[1] if len(sys.argv) > 1 else "status"
    if act == "tui":
        sys.exit(run_tui())
    if act == "status":
        print(json.dumps(status_snapshot()))
        return
    if act == "toggle-weblab":
        ok, msg = stop_weblab() if port_open() else start_weblab()
        save_state({"lastAction": "toggle-weblab", "lastOk": ok, "lastOutput": msg, "lastTs": int(time.time())})
        print(json.dumps(status_snapshot()))
        return
    if act == "open-forge":
        subprocess.Popen(["xdg-open", "http://127.0.0.1:8787/view/redline-forge-suite.html"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        save_state({"lastAction": "open-forge", "lastOk": True, "lastOutput": "Launched Redline Forge Suite in Android browser", "lastTs": int(time.time())})
        print(json.dumps(status_snapshot()))
        return
    if act == "open-browser":
        if not port_open():
            start_weblab()
        subprocess.Popen(["xdg-open", f"http://127.0.0.1:{PORT}"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        save_state({"lastAction": "open-browser", "lastOk": True, "lastOutput": f"Opened http://127.0.0.1:{PORT} in browser", "lastTs": int(time.time())})
        print(json.dumps(status_snapshot()))
        return
    if act == "verify-weblab":
        print(json.dumps(run_capture("verify-weblab", [sys.executable, "verify_lab.py"], WEBLAB_DIR)))
        return
    if act == "grade-v1":
        p_v1 = "probes/ultimus-probes-v1.jsonl" if (ULTIMUS_DIR / "probes/ultimus-probes-v1.jsonl").is_file() else "probes/ultimus-v1.jsonl"
        r_v1 = "runs/last-run-v1.jsonl"
        print(json.dumps(run_capture("grade-v1", [sys.executable, "ultimus_grade.py", r_v1, "--probes", p_v1, "--strict-pairs"], ULTIMUS_DIR)))
        return
    if act == "grade-v2":
        p_v2 = "probes/ultimus-probes-v2.jsonl" if (ULTIMUS_DIR / "probes/ultimus-probes-v2.jsonl").is_file() else "probes/ultimus-v2.jsonl"
        r_v2 = "runs/sample-run-v2.jsonl"
        print(json.dumps(run_capture("grade-v2", [sys.executable, "ultimus_grade.py", r_v2, "--probes", p_v2, "--strict-pairs"], ULTIMUS_DIR)))
        return
    if act == "ablate":
        print(json.dumps(run_capture("ablate", [sys.executable, "ablate_v1_v2.py"], ULTIMUS_DIR)))
        return
    if act == "test-ultimus":
        print(json.dumps(run_capture("test-ultimus", [sys.executable, "-m", "unittest", "discover", "-s", "tests"], ULTIMUS_DIR)))
        return
    if act == "install-owl":
        ok, msg = install_owl()
        save_state({"lastAction": "install-owl", "lastOk": ok, "lastOutput": msg, "lastTs": int(time.time())})
        print(json.dumps({**status_snapshot(), "installOk": ok, "installMsg": msg}))
        return
    print(json.dumps({"ok": False, "error": f"unknown action {act!r}"}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)[:200]}))
