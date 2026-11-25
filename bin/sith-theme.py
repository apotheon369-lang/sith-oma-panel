#!/usr/bin/env python3
"""Sith Kyber Theme & Launcher Customization Engine.

Commands:
  sith-theme.py status
  sith-theme.py set <vader|maul|revan|bane|matrix>
  sith-theme.py cycle
  sith-theme.py speed <0.5|1.0|1.5|2.0>
  sith-theme.py eco <on|off|toggle>
  sith-theme.py health
"""
import json
import os
import sys

OMA_DIR = os.path.join(os.path.expanduser("~"), ".config", "omalauncher")
THEME_FILE = os.path.join(OMA_DIR, "sith-theme.json")
STYLE_FILE = os.path.join(OMA_DIR, "style", "style.toml")
ANIM_FILE = os.path.join(OMA_DIR, "animations.json")
WIDGETS_DIR = os.path.join(OMA_DIR, "widgets")
CHECK_DIR = os.path.join(WIDGETS_DIR, ".check")
ERR_DIR = os.path.join(WIDGETS_DIR, ".errors")
ORDER = ["vader", "maul", "revan", "bane", "matrix"]


def load_theme():
    with open(THEME_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_theme(data):
    tmp = THEME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, THEME_FILE)


def sync_style_toml(accent, dim):
    if not os.path.isfile(STYLE_FILE):
        return
    import re
    with open(STYLE_FILE, "r", encoding="utf-8") as f:
        txt = f.read()
    txt = re.sub(r'(?m)^border-focused\s*=\s*"#[0-9a-fA-F]{6}"', f'border-focused = "{accent}"', txt)
    txt = re.sub(r'(?m)^border\s*=\s*"#[0-9a-fA-F]{6}"', f'border = "{dim}"', txt, count=1)
    with open(STYLE_FILE, "w", encoding="utf-8") as f:
        f.write(txt)


def sync_animations(speed=None, eco=None):
    if not os.path.isfile(ANIM_FILE):
        return
    try:
        with open(ANIM_FILE, "r", encoding="utf-8") as f:
            anim = json.load(f)
    except Exception:
        return
    if speed is not None:
        anim["speed"] = float(speed)
    if eco is not None:
        anim["master"] = not bool(eco)
    tmp = ANIM_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(anim, indent=2) + "\n")
    os.replace(tmp, ANIM_FILE)


def health_info():
    try:
        owls = [name for name in os.listdir(WIDGETS_DIR) if name.endswith(".owl") and not name.startswith(".")]
    except OSError:
        owls = []
    ok_cnt = 0
    for name in owls:
        chk = os.path.join(CHECK_DIR, name + ".txt")
        try:
            with open(chk, "rb") as f:
                if b"result ok" in f.read(512):
                    ok_cnt += 1
        except OSError:
            pass
    try:
        err_cnt = len(os.listdir(ERR_DIR))
    except OSError:
        err_cnt = 0
    toml_errs = int(os.path.isfile(os.path.join(OMA_DIR, "home", "errors.txt"))) + int(
        os.path.isfile(os.path.join(OMA_DIR, "style", "errors.txt"))
    )
    total_errs = err_cnt + toml_errs
    return {
        "total": len(owls),
        "ok_count": ok_cnt,
        "errors": total_errs,
        "clean": total_errs == 0,
    }


def build_out(data, msg=""):
    active = data.get("active", "vader")
    p = data.get("presets", {}).get(active, {})
    return {
        "ok": True,
        "msg": msg,
        "active": active,
        "preset": active,
        "name": p.get("name", "Darth Vader"),
        "label": p.get("label", active.upper()),
        "crystal": p.get("crystal", "Crimson Synthetic Kyber"),
        "accent": p.get("accent", "#ff1e3c"),
        "hot": p.get("hot", "#ff6677"),
        "dim": p.get("dim", "#7a1420"),
        "bg": p.get("bg", "#050001"),
        "speed": float(data.get("speed", 1.5)),
        "eco": bool(data.get("eco", False)),
        "health": health_info(),
    }


def _run_owl_hub(*kv):
    hub_bin = os.path.join(os.path.expanduser("~"), ".local", "bin", "owl-hub")
    if os.path.isfile(hub_bin):
        import subprocess
        try:
            subprocess.run([hub_bin, *kv], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass


def cmd_set(key):
    data = load_theme()
    presets = data.get("presets", {})
    if key not in presets:
        return {"ok": False, "msg": f"Unknown preset: {key}"}
    if key == "skeletor":
        _run_owl_hub("mode", "skeletor")
    else:
        _run_owl_hub("mode", "infernal")
        _run_owl_hub("theme", key)
    return build_out(load_theme(), msg=f"Kyber -> {presets[key]['name']} ({presets[key]['accent']})")


def cmd_cycle():
    data = load_theme()
    cur = data.get("active", "vader")
    nxt = ORDER[(ORDER.index(cur) + 1) % len(ORDER)] if cur in ORDER else ORDER[0]
    return cmd_set(nxt)


def cmd_speed(val_str):
    spd = max(0.5, min(2.0, float(val_str)))
    _run_owl_hub("speed", str(spd))
    return build_out(load_theme(), msg=f"FX Speed -> {spd}x")


def cmd_eco(mode):
    data = load_theme()
    if mode == "toggle":
        eco = not bool(data.get("eco", False))
    else:
        eco = mode in ("on", "true", "1")
    _run_owl_hub("eco", "true" if eco else "false")
    return build_out(load_theme(), msg="Eco Mode ON (AMOLED Battery)" if eco else "Eco Mode OFF (Full FX)")


def main():
    args = sys.argv[1:]
    if not args or args[0] == "status":
        print(json.dumps(build_out(load_theme())))
    elif args[0] == "health":
        print(json.dumps({"ok": True, **health_info()}))
    elif args[0] == "set" and len(args) >= 2:
        print(json.dumps(cmd_set(args[1].lower())))
    elif args[0] == "cycle":
        print(json.dumps(cmd_cycle()))
    elif args[0] == "speed" and len(args) >= 2:
        print(json.dumps(cmd_speed(args[1])))
    elif args[0] == "eco" and len(args) >= 2:
        print(json.dumps(cmd_eco(args[1].lower())))
    else:
        print(json.dumps({"ok": False, "msg": f"Invalid args: {args}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
