#!/usr/bin/env python3
"""Sith Command Deck — single-signal backend: battery.
Usage: sith-battery.py status   (read-only; the only action)
Contract: one JSON object on stdout, exit 0, never crashes."""
import glob, json, sys, time
from pathlib import Path

def read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None

def sysfs_battery():
    """Linux power_supply class: find the first entry whose type is Battery."""
    for d in sorted(glob.glob("/sys/class/power_supply/*")):
        if read(f"{d}/type") != "Battery":
            continue
        cap = read(f"{d}/capacity")
        if cap is None:
            continue
        try:
            pct = int(cap)
        except ValueError:
            continue
        status = (read(f"{d}/status") or "unknown").lower()
        return {"source": "sysfs", "pct": max(0, min(100, pct)),
                "charging": status in ("charging", "full")}
    return None

def main(argv):
    act = argv[1] if len(argv) > 1 else "status"
    if act != "status":
        return {"ok": False, "error": f"unknown action {act!r}"}
    b = sysfs_battery()
    if b is None:
        # Stable keys, typed: pct -1 means "unknown", never a missing key.
        return {"ok": False, "error": "no battery source readable",
                "pct": -1, "charging": False, "source": "none",
                "ts": int(time.time())}
    return {"ok": True, **b, "ts": int(time.time())}

if __name__ == "__main__":
    try:
        out = main(sys.argv)
    except Exception as e:                     # never crash: OWL's `json` needs valid JSON
        out = {"ok": False, "error": str(e)[:200], "pct": -1,
               "charging": False, "source": "none", "ts": int(time.time())}
    print(json.dumps(out))
