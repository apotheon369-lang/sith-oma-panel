#!/usr/bin/env python3
"""Toggle Omalauncher Super Fast / Chill Battery Saver Mode — unified with owl-hub & hub.json."""
import json
import os
import subprocess
import sys
from pathlib import Path

OMA_DIR = Path(os.path.expanduser("~/.config/omalauncher"))
HUB_FILE = OMA_DIR / "hub.json"
ECO_FILE = OMA_DIR / "eco-mode.json"
OWL_HUB = Path(os.path.expanduser("~/.local/bin/owl-hub"))


def read_hub():
    try:
        d = json.loads(HUB_FILE.read_text())
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def read_eco() -> bool:
    h = read_hub()
    if "eco" in h:
        return bool(h.get("eco", False))
    try:
        d = json.loads(ECO_FILE.read_text())
        if isinstance(d, dict):
            return bool(d.get("enabled", False))
    except Exception:
        pass
    return False


def apply_mode(enable: bool):
    if OWL_HUB.is_file():
        subprocess.run(
            [str(OWL_HUB), "eco", "true" if enable else "false"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    h = read_hub()
    mode = h.get("mode", "infernal")
    omarchy = f"{mode}-amoled" if mode in ("infernal", "future", "mask", "skeletor") else "infernal-amoled"
    payload = {
        "ok": True,
        "enabled": enable,
        "mode": mode,
        "theme": omarchy,
        "res": "540x1200" if enable else "native",
        "animations": not enable,
    }
    tmp = ECO_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, ECO_FILE)
    return payload


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        en = read_eco()
        h = read_hub()
        mode = h.get("mode", "infernal")
        print(json.dumps({
            "ok": True,
            "enabled": en,
            "mode": mode,
            "theme": f"{mode}-amoled",
            "res": "540x1200" if en else "native",
            "animations": not en,
        }))
    elif cmd == "on":
        print(json.dumps(apply_mode(True)))
    elif cmd == "off":
        print(json.dumps(apply_mode(False)))
    elif cmd == "toggle":
        print(json.dumps(apply_mode(not read_eco())))
    else:
        print(json.dumps({"ok": False, "error": f"unknown action {cmd!r}"}))


if __name__ == "__main__":
    main()
