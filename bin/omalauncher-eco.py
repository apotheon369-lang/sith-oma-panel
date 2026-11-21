#!/usr/bin/env python3
"""Toggle Omalauncher Super Fast / Chill Battery Saver Mode."""
import json
import os
import re
import sys
from pathlib import Path

OMA_DIR = Path.home() / ".config/omalauncher"
ECO_FILE = OMA_DIR / "eco-mode.json"
STYLE_FILE = OMA_DIR / "style" / "style.toml"
STYLE_BAK = OMA_DIR / "style" / "style.pre-eco.bak.toml"

ECO_THEME = "infernal-amoled"
ECO_FONT = "JetBrains Mono"


def read_eco():
    if ECO_FILE.exists():
        try:
            d = json.loads(ECO_FILE.read_text())
            if isinstance(d, dict):
                return bool(d.get("enabled", False))
        except Exception:
            pass
    return False


def write_eco(enabled: bool):
    ECO_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "enabled": enabled,
        "theme": ECO_THEME,
        "res": "540x1200" if enabled else "native",
        "animations": not enabled,
    }
    tmp = ECO_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, ECO_FILE)
    return payload


def build_chill_style(orig_text: str) -> str:
    """Create a chill AMOLED battery-saver style.toml preserving home/gestures/terminal layout."""
    text = orig_text

    def set_top_key(src: str, key: str, value_toml: str) -> str:
        pat = re.compile(rf'^({re.escape(key)}\s*=\s*).*$', re.MULTILINE)
        if pat.search(src):
            return pat.sub(rf'\g<1>{value_toml}', src, count=1)
        return f'{key} = {value_toml}\n' + src

    def set_section_key(src: str, section: str, key: str, value_toml: str) -> str:
        sec_pat = re.compile(rf'(^\[{re.escape(section)}\][^\[]*)', re.MULTILINE)
        m = sec_pat.search(src)
        if not m:
            return src.rstrip() + f'\n\n[{section}]\n{key} = {value_toml}\n'
        block = m.group(1)
        k_pat = re.compile(rf'^({re.escape(key)}\s*=\s*).*$', re.MULTILINE)
        if k_pat.search(block):
            new_block = k_pat.sub(rf'\g<1>{value_toml}', block, count=1)
        else:
            new_block = block.rstrip() + f'\n{key} = {value_toml}\n\n'
        return src[:m.start()] + new_block + src[m.end():]

    text = set_top_key(text, "theme", f'"{ECO_THEME}"')
    text = set_top_key(text, "font", f'"{ECO_FONT}"')

    text = set_section_key(text, "widgets", "opacity", "1")
    text = set_section_key(text, "widgets", "frost", "false")
    text = set_section_key(text, "widgets", "border-width", "1")
    text = set_section_key(text, "widgets", "border", '"#2a0308"')
    text = set_section_key(text, "widgets", "border-focused", '"#8a0018"')

    text = set_section_key(text, "status-bar", "opacity", "1")
    text = set_section_key(text, "status-bar", "border", '"#140204"')
    text = set_section_key(text, "status-bar", "border-width", "1")

    text = set_section_key(text, "navigation-bar", "opacity", "1")
    text = set_section_key(text, "navigation-bar", "border", '"#140204"')
    text = set_section_key(text, "navigation-bar", "border-width", "1")

    return text


def apply_mode(enable: bool):
    currently_on = read_eco()
    if enable:
        if STYLE_FILE.exists():
            cur_style = STYLE_FILE.read_text()
            # Only snapshot backup if we're transitioning from normal mode or no backup exists
            if not currently_on or not STYLE_BAK.exists():
                if 'border = "#2a0308"' not in cur_style:
                    STYLE_BAK.write_text(cur_style)
            chill_style = build_chill_style(cur_style)
            tmp = STYLE_FILE.with_suffix(".toml.tmp")
            tmp.write_text(chill_style)
            os.replace(tmp, STYLE_FILE)
        return write_eco(True)
    else:
        if STYLE_BAK.exists():
            restored = STYLE_BAK.read_text()
            tmp = STYLE_FILE.with_suffix(".toml.tmp")
            tmp.write_text(restored)
            os.replace(tmp, STYLE_FILE)
        return write_eco(False)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "status":
        en = read_eco()
        print(json.dumps({
            "enabled": en,
            "theme": ECO_THEME,
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
        print(f"Usage: {sys.argv[0]} [status|on|off|toggle]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
