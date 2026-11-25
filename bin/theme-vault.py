#!/usr/bin/env python3
"""Theme Vault backend for omalauncher — lists and applies custom theme packs cleanly.

Commands:
  theme-vault.py list
  theme-vault.py apply <psycho-neo|pluto-faust|chad-radical>
  theme-vault.py restore
"""
import json
import os
import re
import shutil
import sys

OMA_DIR = os.path.join(os.path.expanduser("~"), ".config", "omalauncher")
THEMES_DIR = os.path.join(OMA_DIR, "themes")
HUB_FILE = os.path.join(OMA_DIR, "hub.json")
HUB_BAK = os.path.join(OMA_DIR, "backups", "hub.json.pre-vault.bak")
STYLE_FILE = os.path.join(OMA_DIR, "style", "style.toml")
ORDER = ["psycho-neo", "pluto-faust", "chad-radical"]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def sync_style(accent, dim):
    if not os.path.isfile(STYLE_FILE):
        return
    try:
        with open(STYLE_FILE, "r", encoding="utf-8") as f:
            txt = f.read()
        txt = re.sub(r'(?m)^border-focused\s*=\s*"#[0-9a-fA-F]{6}"', f'border-focused = "{accent}"', txt)
        txt = re.sub(r'(?m)^border\s*=\s*"#[0-9a-fA-F]{6}"', f'border = "{dim}"', txt, count=1)
        with open(STYLE_FILE, "w", encoding="utf-8") as f:
            f.write(txt)
    except Exception:
        pass


def cmd_list():
    active = "sith"
    if os.path.isfile(HUB_FILE):
        try:
            hub = load_json(HUB_FILE)
            active = hub.get("vaultTheme", hub.get("mode", "sith"))
        except Exception:
            pass

    themes = []
    for tid in ORDER:
        p = os.path.join(THEMES_DIR, f"{tid}.json")
        if not os.path.isfile(p):
            continue
        try:
            d = load_json(p)
            hx = d.get("tok", {}).get("hex", {})
            cp = d.get("tok", {}).get("copy", {})
            items = d.get("menuItems", [])
            clean_items = []
            for it in items[:4]:
                if isinstance(it, dict):
                    clean_items.append({
                        "id": str(it.get("id", "")),
                        "label": str(it.get("label", it.get("title", "ACTION"))),
                        "sub": str(it.get("sub", it.get("desc", it.get("subtitle", ""))))
                    })
                else:
                    clean_items.append({"id": str(it), "label": str(it), "sub": ""})
            themes.append({
                "id": d.get("id", tid),
                "name": d.get("name", tid.upper()),
                "tagline": d.get("tagline", ""),
                "motto": cp.get("motto", ""),
                "pri": hx.get("pri", "#ff1e3c"),
                "sec": hx.get("sec", "#00f0ff"),
                "hot": hx.get("hot", "#ff8596"),
                "obs": hx.get("obs", "#050002"),
                "card": hx.get("card", "#0c0104"),
                "ink": hx.get("ink", "#ffffff"),
                "muted": hx.get("muted", "#aaaaaa"),
                "bodyFont": d.get("tok", {}).get("bodyFont", "sans"),
                "menuItems": clean_items
            })
        except Exception:
            continue

    return {"active": active, "themes": themes}


def cmd_apply(tid):
    if tid not in ORDER:
        return {"ok": False, "error": "unknown theme"}
    p = os.path.join(THEMES_DIR, f"{tid}.json")
    if not os.path.isfile(p):
        return {"ok": False, "error": "missing theme file"}

    os.makedirs(os.path.dirname(HUB_BAK), exist_ok=True)
    if os.path.isfile(HUB_FILE) and not os.path.isfile(HUB_BAK):
        shutil.copy2(HUB_FILE, HUB_BAK)

    pack = load_json(p)
    hub = load_json(HUB_FILE) if os.path.isfile(HUB_FILE) else {}
    hub["vaultTheme"] = tid
    hub["tok"] = pack["tok"]
    save_json_atomic(HUB_FILE, hub)

    hx = pack.get("tok", {}).get("hex", {})
    sync_style(hx.get("pri", "#ff1e3c"), hx.get("dim", "#5c0614"))
    out = cmd_list()
    out["ok"] = True
    out["applied"] = tid
    return out


def cmd_restore():
    if os.path.isfile(HUB_BAK):
        shutil.copy2(HUB_BAK, HUB_FILE)
        try:
            hub = load_json(HUB_FILE)
            hx = hub.get("tok", {}).get("hex", {})
            sync_style(hx.get("pri", "#ff1e3c"), hx.get("dim", "#6b0918"))
        except Exception:
            pass
    out = cmd_list()
    out["ok"] = True
    out["restored"] = True
    return out


def main():
    args = sys.argv[1:]
    if not args or args[0] == "list":
        res = cmd_list()
    elif args[0] == "apply" and len(args) >= 2:
        res = cmd_apply(args[1])
    elif args[0] == "restore":
        res = cmd_restore()
    else:
        res = {"ok": False, "error": "usage: list | apply <id> | restore"}
    print(json.dumps(res))


if __name__ == "__main__":
    main()
