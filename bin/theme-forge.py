#!/usr/bin/env python3
"""theme-forge.py — Stdlib-only AMOLED Theme Pack Forge & Manager for omalauncher OWL 22.

Subcommands (all print valid JSON to stdout):
  list
  create <name> <tagline> <pri_hex> <sec_hex> <mode_idx> <font_key> <archetype>
  quick <prompt...>
  apply <id>
  delete <id>
"""
from __future__ import annotations

import colorsys
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any

CONFIG_DIR = os.path.expanduser("~/.config/omalauncher")
THEMES_DIR = os.path.join(CONFIG_DIR, "themes")
ICONS_DIR = os.path.join(THEMES_DIR, "icons")
BACKUP_REMOVED_DIR = os.path.join(CONFIG_DIR, "backups", "removed-themes")
HUB_FILE = os.path.join(CONFIG_DIR, "hub.json")

CORE_IDS = ("infernal", "future", "mask", "skeletor")

CORE_THEMES = [
    {
        "id": "infernal",
        "name": "INFERNAL",
        "sub": "SITH CRIMSON",
        "pri": "#ff1e3c",
        "sec": "#3d9bff",
        "hot": "#ff8596",
        "glyph": "⛧",
        "builtin": True,
    },
    {
        "id": "future",
        "name": "FUTURE",
        "sub": "CYBER OS",
        "pri": "#18d8ff",
        "sec": "#ff2fa8",
        "hot": "#9ceeff",
        "glyph": "⬡",
        "builtin": True,
    },
    {
        "id": "mask",
        "name": "MASK",
        "sub": "NEON CHAOS",
        "pri": "#1aff5c",
        "sec": "#ffc21a",
        "hot": "#c6ff3d",
        "glyph": "✦",
        "builtin": True,
    },
    {
        "id": "skeletor",
        "name": "SKELETOR",
        "sub": "HAVOC LORD",
        "pri": "#b44aff",
        "sec": "#a6ff2e",
        "hot": "#eb9eff",
        "glyph": "☩",
        "builtin": True,
    },
]

OMARCHY_MODES = (
    "infernal-amoled",
    "future-amoled",
    "mask-amoled",
    "skeletor-amoled",
)

FONT_META = {
    "sans": ("DejaVu Sans", "sans", "sans"),
    "mono": ("JetBrains Mono", "mono", "mono"),
    "anton": ("Anton", "~/.local/share/fonts/Anton-Regular.ttf", "sans"),
    "bebas": ("Bebas Neue", "~/.local/share/fonts/BebasNeue-Regular.ttf", "sans"),
    "archivo": ("Archivo Black", "~/.local/share/fonts/ArchivoBlack-Regular.ttf", "sans"),
    "blackops": ("Black Ops One", "~/.local/share/fonts/BlackOpsOne-Regular.ttf", "sans"),
    "bowlby": ("Bowlby One SC", "~/.local/share/fonts/BowlbyOneSC-Regular.ttf", "sans"),
    "titan": ("Titan One", "~/.local/share/fonts/TitanOne-Regular.ttf", "sans"),
    "passion": ("Passion One", "~/.local/share/fonts/PassionOne-Black.ttf", "sans"),
    "oswald": ("Oswald", "~/.local/share/fonts/Oswald-Var.ttf", "sans"),
    "russo": ("Russo One", "~/.local/share/fonts/RussoOne-Regular.ttf", "sans"),
    "pirata": ("Pirata One", "~/.local/share/fonts/PirataOne-Regular.ttf", "sans"),
}

ARCHETYPES = ("shield", "visor", "sigil", "skull", "reactor")

ARCHETYPE_GLYPHS = {
    "shield": ("◈", "❖"),
    "visor": ("⬡", "⚡"),
    "sigil": ("✦", "⛧"),
    "skull": ("☩", "☠"),
    "reactor": ("☢", "⚡"),
}

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
SLUG_BAD_RE = re.compile(r"[^a-z0-9]+")

# Keyword tables for intelligent deterministic `quick` parsing
COLOR_KEYWORDS: dict[str, tuple[str, str]] = {
    # Reds / Sith / Fire
    "crimson": ("#ff1e3c", "#3d9bff"),
    "sith": ("#ff1e3c", "#ff9d00"),
    "blood": ("#ff1434", "#ff7b29"),
    "ruby": ("#ff1e56", "#00e5ff"),
    "scarlet": ("#ff2438", "#ffd000"),
    "red": ("#ff223a", "#00d4ff"),
    "inferno": ("#ff2a1f", "#ffb703"),
    "hell": ("#ff1e2d", "#ff8800"),
    "magma": ("#ff3b1f", "#ffcc00"),
    "lava": ("#ff4518", "#ffae00"),
    "ember": ("#ff4d1a", "#00e1ff"),
    # Oranges / Gold / Solar
    "solar": ("#ffaa00", "#ff2a6d"),
    "gold": ("#ffc400", "#2f74ff"),
    "amber": ("#ff9e00", "#00e5ff"),
    "sun": ("#ffb703", "#fb5607"),
    "flame": ("#ff5400", "#ffbd00"),
    "fire": ("#ff4800", "#00f0ff"),
    "orange": ("#ff6b00", "#00e5ff"),
    "bronze": ("#ff8c24", "#38d9a9"),
    "yellow": ("#ffe600", "#ff2a85"),
    # Greens / Toxic / Matrix
    "toxic": ("#39ff14", "#b44aff"),
    "acid": ("#a6ff00", "#ff2ae6"),
    "lime": ("#9eff00", "#00f0ff"),
    "venom": ("#1aff5c", "#9d4edd"),
    "gamma": ("#2bff4a", "#9b30ff"),
    "hulk": ("#2bff4a", "#9b30ff"),
    "matrix": ("#00ff88", "#ff3d8f"),
    "emerald": ("#00ff7f", "#00d4ff"),
    "jade": ("#00e676", "#ffab00"),
    "mint": ("#2effb5", "#ff4791"),
    "green": ("#1aff5c", "#ffc21a"),
    # Cyans / Ice / Cyber
    "cyber": ("#00f0ff", "#ff2ae6"),
    "cyan": ("#00f0ff", "#ff2fa8"),
    "ice": ("#40e8ff", "#ff5ca8"),
    "frost": ("#5ce6ff", "#a78bfa"),
    "arctic": ("#38e8ff", "#ff6b6b"),
    "electric": ("#00d4ff", "#ffe600"),
    "azure": ("#1e90ff", "#00f5d4"),
    "cobalt": ("#2979ff", "#ff2e93"),
    "blue": ("#1f7aff", "#00f0ff"),
    "sapphire": ("#246bfd", "#ff8c00"),
    "plasma": ("#00e5ff", "#b44aff"),
    # Purples / Joker / Arcane
    "joker": ("#b44aff", "#2bff4a"),
    "purple": ("#b44aff", "#a6ff2e"),
    "violet": ("#9d4edd", "#00f5d4"),
    "ultraviolet": ("#a833ff", "#00f0ff"),
    "arcane": ("#b44aff", "#ffc400"),
    "void": ("#8f3dff", "#ff2a6d"),
    "abyss": ("#7b2cbf", "#00e5ff"),
    "shadow": ("#8c52ff", "#ff3366"),
    "phantom": ("#7048e8", "#20c997"),
    # Magentas / Pinks / Synthwave
    "synth": ("#ff2ae6", "#00f0ff"),
    "synthwave": ("#ff2ae6", "#00f0ff"),
    "outrun": ("#ff2a85", "#00f0ff"),
    "vapor": ("#ff4ecd", "#05d9e8"),
    "magenta": ("#ff1fd6", "#00f0ff"),
    "pink": ("#ff3b9a", "#00e5ff"),
    "neon": ("#ff2975", "#00f0ff"),
    "rose": ("#ff3877", "#ffd166"),
    # Silver / Chrome / Steel
    "silver": ("#c4d4e0", "#00e5ff"),
    "chrome": ("#b8d0eb", "#ff2a85"),
    "steel": ("#7aa2c4", "#ff6b35"),
    "ghost": ("#9be7ff", "#ff477e"),
}

FALLBACK_PALETTES = (
    ("#ff2a55", "#00e5ff"),
    ("#00f0ff", "#ff2ae6"),
    ("#2bff6a", "#b44aff"),
    ("#b44aff", "#a6ff2e"),
    ("#ffaa00", "#00e5ff"),
    ("#ff2ae6", "#ffc400"),
    ("#1f7aff", "#ff3877"),
    ("#00ff9d", "#ff5400"),
)

ARCHETYPE_KEYWORDS: dict[str, str] = {
    "shield": "shield",
    "aegis": "shield",
    "armor": "shield",
    "knight": "shield",
    "paladin": "shield",
    "sentinel": "shield",
    "guardian": "shield",
    "samurai": "shield",
    "shogun": "shield",
    "spartan": "shield",
    "titan": "shield",
    "visor": "visor",
    "cyber": "visor",
    "mecha": "visor",
    "pilot": "visor",
    "tron": "visor",
    "outrun": "visor",
    "synth": "visor",
    "robo": "visor",
    "hacker": "visor",
    "ninja": "visor",
    "ronin": "visor",
    "sigil": "sigil",
    "occult": "sigil",
    "arcane": "sigil",
    "magic": "sigil",
    "mystic": "sigil",
    "witch": "sigil",
    "rune": "sigil",
    "sith": "sigil",
    "demon": "sigil",
    "infernal": "sigil",
    "angel": "sigil",
    "astral": "sigil",
    "skull": "skull",
    "joker": "skull",
    "death": "skull",
    "reaper": "skull",
    "bone": "skull",
    "skeleton": "skull",
    "havoc": "skull",
    "chaos": "skull",
    "pirate": "skull",
    "ghost": "skull",
    "venom": "skull",
    "reactor": "reactor",
    "toxic": "reactor",
    "nuclear": "reactor",
    "atomic": "reactor",
    "gamma": "reactor",
    "plasma": "reactor",
    "solar": "reactor",
    "core": "reactor",
    "fusion": "reactor",
    "quantum": "reactor",
    "matrix": "reactor",
}

MODE_KEYWORDS: dict[str, int] = {
    # 0 = Infernal / Forge
    "infernal": 0, "sith": 0, "hell": 0, "fire": 0, "flame": 0, "crimson": 0,
    "blood": 0, "samurai": 0, "demon": 0, "solar": 0, "magma": 0, "lava": 0,
    # 1 = Future / Holo
    "future": 1, "cyber": 1, "holo": 1, "ice": 1, "frost": 1, "visor": 1,
    "matrix": 1, "quantum": 1, "reactor": 1, "chrome": 1, "steel": 1, "synth": 1,
    # 2 = Mask / Twister / Chaos
    "mask": 2, "joker": 2, "chaos": 2, "toxic": 2, "neon": 2, "outrun": 2,
    "acid": 2, "venom": 2, "gamma": 2, "lime": 2, "vapor": 2,
    # 3 = Skeletor / Arcane / Void
    "skeletor": 3, "havoc": 3, "arcane": 3, "void": 3, "abyss": 3, "skull": 3,
    "phantom": 3, "shadow": 3, "purple": 3, "violet": 3, "sigil": 3, "occult": 3,
}

FONT_KEYWORDS: dict[str, str] = {
    "mono": "mono",
    "terminal": "mono",
    "hacker": "mono",
    "matrix": "mono",
    "code": "mono",
    "kernel": "mono",
    "cyber": "russo",
    "future": "russo",
    "synth": "russo",
    "outrun": "russo",
    "neon": "russo",
    "turbo": "russo",
    "visor": "russo",
    "samurai": "blackops",
    "sith": "blackops",
    "war": "blackops",
    "skull": "blackops",
    "armor": "blackops",
    "titan": "blackops",
    "havoc": "blackops",
    "joker": "blackops",
    "toxic": "archivo",
    "smash": "archivo",
    "gamma": "archivo",
}


def emit(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False))


def fail(msg: str) -> int:
    emit({"ok": False, "error": msg})
    return 1


def load_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json_atomic(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_text_atomic(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def hex_to_rgb(hx: str) -> list[int]:
    h = hx.lstrip("#")
    return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]


def rgb_to_hex(rgb: tuple[int, int, int] | list[int]) -> str:
    r = max(0, min(255, int(round(rgb[0]))))
    g = max(0, min(255, int(round(rgb[1]))))
    b = max(0, min(255, int(round(rgb[2]))))
    return f"#{r:02x}{g:02x}{b:02x}"


def srgb_luma(rgb: tuple[int, int, int] | list[int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def mix_rgb(a: list[int] | tuple[int, int, int], b: list[int] | tuple[int, int, int], t: float) -> list[int]:
    t = max(0.0, min(1.0, t))
    return [
        int(round(a[0] + (b[0] - a[0]) * t)),
        int(round(a[1] + (b[1] - a[1]) * t)),
        int(round(a[2] + (b[2] - a[2]) * t)),
    ]


def hsv_to_rgb255(h: float, s: float, v: float) -> list[int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return [int(round(r * 255)), int(round(g * 255)), int(round(b * 255))]


def cap_luma(rgb: list[int], max_luma: float) -> list[int]:
    cur = srgb_luma(rgb)
    if cur <= max_luma or cur <= 0:
        return rgb
    scale = max_luma / cur
    return [max(0, int(math.floor(c * scale))) for c in rgb]


def boost_min_luma(rgb: list[int], min_luma: float) -> list[int]:
    cur = srgb_luma(rgb)
    if cur >= min_luma:
        return rgb
    # Blend toward white until min_luma is reached
    lo, hi = 0.0, 1.0
    best = rgb
    for _ in range(12):
        mid = (lo + hi) * 0.5
        cand = mix_rgb(rgb, (255, 255, 255), mid)
        if srgb_luma(cand) >= min_luma:
            best = cand
            hi = mid
        else:
            lo = mid
    return best


def compute_amoled_palette(pri_hex: str, sec_hex: str) -> dict[str, str]:
    """Compute a 15-color AMOLED palette from primary + secondary neon hexes.

    Guarantees ultra-dark AMOLED surfaces (obs <= 8% luma, card <= 12% luma)
    and high-contrast readable text/highlight tokens.
    """
    pri_rgb = hex_to_rgb(pri_hex)
    sec_rgb = hex_to_rgb(sec_hex)

    # Ensure primary isn't pitch-black if caller supplied a very dark color
    if srgb_luma(pri_rgb) < 0.12:
        h_p, s_p, _ = colorsys.rgb_to_hsv(*(c / 255.0 for c in pri_rgb))
        pri_rgb = hsv_to_rgb255(h_p, max(0.65, s_p), 0.85)
    h_p, s_p, v_p = colorsys.rgb_to_hsv(*(c / 255.0 for c in pri_rgb))

    # Tinted dark AMOLED surfaces strictly capped for OLED contrast
    tint_s = min(0.92, max(0.45, s_p)) if s_p > 0.08 else 0.08
    obs_rgb = cap_luma(hsv_to_rgb255(h_p, tint_s, 0.045), 0.045)
    card_rgb = cap_luma(hsv_to_rgb255(h_p, min(0.88, tint_s), 0.088), 0.085)
    # Keep card subtly brighter than obs even on high-luma hues (yellow/cyan)
    if sum(card_rgb) <= sum(obs_rgb) + 6:
        card_rgb = cap_luma([min(255, c + 6) for c in obs_rgb], 0.11)

    deep_rgb = cap_luma(hsv_to_rgb255(h_p, min(0.88, tint_s), 0.155), 0.15)
    dim_rgb = hsv_to_rgb255(h_p, min(0.90, max(0.35, s_p)), 0.42)
    if srgb_luma(dim_rgb) > 0.36:
        dim_rgb = cap_luma(dim_rgb, 0.32)

    mid_rgb = boost_min_luma(mix_rgb(pri_rgb, (255, 255, 255), 0.18), 0.34)
    hot_rgb = boost_min_luma(mix_rgb(pri_rgb, (255, 255, 255), 0.46), 0.56)

    bone_rgb = mix_rgb((255, 255, 255), pri_rgb, 0.07)
    ink_rgb = mix_rgb((255, 255, 255), pri_rgb, 0.06)
    muted_rgb = boost_min_luma(mix_rgb((150, 160, 175), pri_rgb, 0.36), 0.42)
    if srgb_luma(muted_rgb) > 0.68:
        muted_rgb = cap_luma(muted_rgb, 0.65)

    eco_pri_rgb = mix_rgb(pri_rgb, (36, 36, 36), 0.28)
    eco_hot_rgb = mix_rgb(hot_rgb, (50, 50, 50), 0.24)
    eco_dim_rgb = mix_rgb(dim_rgb, (0, 0, 0), 0.32)
    eco_bone_rgb = mix_rgb(bone_rgb, (45, 45, 45), 0.24)

    return {
        "pri": rgb_to_hex(pri_rgb),
        "mid": rgb_to_hex(mid_rgb),
        "hot": rgb_to_hex(hot_rgb),
        "sec": sec_hex.lower(),
        "bone": rgb_to_hex(bone_rgb),
        "dim": rgb_to_hex(dim_rgb),
        "deep": rgb_to_hex(deep_rgb),
        "card": rgb_to_hex(card_rgb),
        "obs": rgb_to_hex(obs_rgb),
        "ink": rgb_to_hex(ink_rgb),
        "muted": rgb_to_hex(muted_rgb),
        "ecoPri": rgb_to_hex(eco_pri_rgb),
        "ecoHot": rgb_to_hex(eco_hot_rgb),
        "ecoDim": rgb_to_hex(eco_dim_rgb),
        "ecoBone": rgb_to_hex(eco_bone_rgb),
    }


def sanitize_slug(name: str) -> str:
    slug = SLUG_BAD_RE.sub("-", name.strip().lower()).strip("-")
    if not slug:
        digest = hashlib.sha256(name.encode("utf-8", errors="ignore")).hexdigest()[:6]
        slug = f"forge-{digest}"
    slug = slug[:24].strip("-")
    if not slug:
        slug = "forge-theme"
    if slug in CORE_IDS:
        slug = f"{slug}-custom"[:24].strip("-")
    return slug


def resolve_font(font_key: str) -> tuple[str, str, str]:
    fk = (font_key or "russo").strip().lower()
    if fk not in FONT_META:
        fk = "russo"
    family, path, body = FONT_META[fk]
    return fk, path, body


def _svg_shell_open(hex_map: dict[str, str]) -> str:
    obs = hex_map["obs"]
    card = hex_map["card"]
    deep = hex_map["deep"]
    dim = hex_map["dim"]
    pri = hex_map["pri"]
    mid = hex_map["mid"]
    hot = hex_map["hot"]
    sec = hex_map["sec"]
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 256 256' width='256' height='256'>"
        "<defs>"
        "<clipPath id='tileClip'><rect x='2' y='2' width='252' height='252' rx='56'/></clipPath>"
        f"<radialGradient id='bgAura' cx='50%' cy='48%' r='56%'>"
        f"<stop offset='0%' stop-color='{pri}' stop-opacity='0.28'/>"
        f"<stop offset='52%' stop-color='{deep}' stop-opacity='0.55'/>"
        f"<stop offset='100%' stop-color='{obs}' stop-opacity='1'/>"
        "</radialGradient>"
        f"<linearGradient id='plateGrad' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0%' stop-color='{deep}'/>"
        f"<stop offset='55%' stop-color='{card}'/>"
        f"<stop offset='100%' stop-color='{obs}'/>"
        "</linearGradient>"
        f"<linearGradient id='priGrad' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0%' stop-color='{hot}'/>"
        f"<stop offset='50%' stop-color='{pri}'/>"
        f"<stop offset='100%' stop-color='{mid}'/>"
        "</linearGradient>"
        f"<linearGradient id='rimGrad' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0%' stop-color='{pri}'/>"
        f"<stop offset='50%' stop-color='{sec}'/>"
        f"<stop offset='100%' stop-color='{hot}'/>"
        "</linearGradient>"
        "</defs>"
        f"<rect x='1' y='1' width='254' height='254' rx='56' fill='{obs}'/>"
        "<g clip-path='url(#tileClip)'>"
        f"<rect width='256' height='256' fill='{obs}'/>"
        "<rect width='256' height='256' fill='url(#bgAura)'/>"
        f"<g stroke='{dim}' stroke-width='1' opacity='0.42'>"
        "<line x1='0' y1='64' x2='256' y2='64'/>"
        "<line x1='0' y1='128' x2='256' y2='128'/>"
        "<line x1='0' y1='192' x2='256' y2='192'/>"
        "<line x1='64' y1='0' x2='64' y2='256'/>"
        "<line x1='128' y1='0' x2='128' y2='256'/>"
        "<line x1='192' y1='0' x2='192' y2='256'/>"
        "</g>"
    )


def _svg_shell_close(hex_map: dict[str, str]) -> str:
    dim = hex_map["dim"]
    return (
        "</g>"
        "<rect x='3' y='3' width='250' height='250' rx='54' fill='none' stroke='url(#rimGrad)' stroke-width='3'/>"
        f"<rect x='9' y='9' width='238' height='238' rx='48' fill='none' stroke='{dim}' stroke-width='1.4' opacity='0.85'/>"
        "</svg>"
    )


def build_svg_icon(archetype: str, hex_map: dict[str, str]) -> str:
    arch = archetype if archetype in ARCHETYPES else "sigil"
    pri = hex_map["pri"]
    mid = hex_map["mid"]
    hot = hex_map["hot"]
    sec = hex_map["sec"]
    dim = hex_map["dim"]
    deep = hex_map["deep"]
    bone = hex_map["bone"]

    if arch == "shield":
        body = (
            f"<circle cx='128' cy='128' r='88' fill='none' stroke='{dim}' stroke-width='2.5' stroke-dasharray='18 8'/>"
            f"<path d='M128 38 L194 64 L190 132 C188 172 160 202 128 218 C96 202 68 172 66 132 L62 64 Z' "
            f"fill='url(#plateGrad)' stroke='url(#priGrad)' stroke-width='4.5' stroke-linejoin='round'/>"
            f"<path d='M128 52 L180 73 L177 129 C175 161 153 187 128 201 C103 187 81 161 79 129 L76 73 Z' "
            f"fill='none' stroke='{sec}' stroke-width='2' opacity='0.9'/>"
            f"<path d='M84 96 L128 118 L172 96 L168 114 L128 136 L88 114 Z' fill='{pri}' opacity='0.9'/>"
            f"<path d='M128 66 L146 98 L128 186 L110 98 Z' fill='url(#priGrad)' stroke='{bone}' stroke-width='1.6'/>"
            f"<polygon points='128,84 139,102 128,122 117,102' fill='{hot}'/>"
            f"<circle cx='128' cy='102' r='4.5' fill='{bone}'/>"
        )
    elif arch == "visor":
        body = (
            f"<circle cx='128' cy='128' r='86' fill='none' stroke='{sec}' stroke-width='2' opacity='0.45'/>"
            f"<path d='M56 122 L74 68 L108 50 L148 50 L182 68 L200 122 L188 168 L158 202 L128 210 L98 202 L68 168 Z' "
            f"fill='url(#plateGrad)' stroke='{pri}' stroke-width='4' stroke-linejoin='round'/>"
            f"<path d='M76 72 L128 58 L180 72 L168 94 L88 94 Z' fill='{deep}' stroke='{mid}' stroke-width='2'/>"
            "<clipPath id='vClip'><path d='M68 106 L120 112 L128 122 L136 112 L188 106 L178 144 L140 148 L128 138 L116 148 L78 144 Z'/></clipPath>"
            f"<path d='M68 106 L120 112 L128 122 L136 112 L188 106 L178 144 L140 148 L128 138 L116 148 L78 144 Z' "
            f"fill='url(#priGrad)' stroke='{bone}' stroke-width='2.2' stroke-linejoin='round'/>"
            "<g clip-path='url(#vClip)'>"
            f"<polygon points='84,102 104,102 90,152 72,152' fill='{sec}' opacity='0.85'/>"
            f"<polygon points='146,102 162,102 148,152 134,152' fill='{hot}' opacity='0.85'/>"
            f"<rect x='66' y='125' width='124' height='3' fill='{bone}' opacity='0.7'/>"
            "</g>"
            f"<path d='M96 168 L118 178 L138 178 L160 168' fill='none' stroke='{sec}' stroke-width='3' stroke-linecap='round'/>"
            f"<path d='M108 188 L148 188' stroke='{hot}' stroke-width='3' stroke-linecap='round'/>"
        )
    elif arch == "skull":
        body = (
            f"<circle cx='128' cy='128' r='90' fill='none' stroke='{dim}' stroke-width='2.5' stroke-dasharray='12 8'/>"
            f"<path d='M72 136 C64 90 88 48 128 48 C168 48 192 90 184 136 L172 158 L168 192 L148 206 L108 206 L88 192 L84 158 Z' "
            f"fill='url(#plateGrad)' stroke='{pri}' stroke-width='4.5' stroke-linejoin='round'/>"
            f"<path d='M86 96 L128 110 L170 96' fill='none' stroke='{mid}' stroke-width='3.5' stroke-linecap='round'/>"
            f"<polygon points='84,114 118,124 112,146 88,140' fill='{pri}' stroke='{bone}' stroke-width='1.8'/>"
            f"<polygon points='172,114 138,124 144,146 168,140' fill='{pri}' stroke='{bone}' stroke-width='1.8'/>"
            f"<circle cx='101' cy='131' r='6' fill='{sec}'/>"
            f"<circle cx='155' cy='131' r='6' fill='{sec}'/>"
            f"<polygon points='128,142 137,162 119,162' fill='{hot}'/>"
            f"<g stroke='{hot}' stroke-width='3.2' stroke-linecap='round'>"
            "<line x1='106' y1='178' x2='106' y2='198'/>"
            "<line x1='120' y1='176' x2='120' y2='200'/>"
            "<line x1='136' y1='176' x2='136' y2='200'/>"
            "<line x1='150' y1='178' x2='150' y2='198'/>"
            "</g>"
        )
    elif arch == "reactor":
        body = (
            f"<circle cx='128' cy='128' r='88' fill='url(#plateGrad)' stroke='{dim}' stroke-width='4'/>"
            f"<circle cx='128' cy='128' r='78' fill='none' stroke='{pri}' stroke-width='5' stroke-dasharray='32 14'/>"
            f"<circle cx='128' cy='128' r='60' fill='none' stroke='{sec}' stroke-width='3' stroke-dasharray='18 12'/>"
            f"<path d='M128 42 L144 96 L128 110 L112 96 Z' fill='url(#priGrad)'/>"
            f"<path d='M202 171 L148 158 L142 138 L162 132 Z' fill='url(#priGrad)'/>"
            f"<path d='M54 171 L94 132 L114 138 L108 158 Z' fill='url(#priGrad)'/>"
            f"<circle cx='128' cy='128' r='34' fill='{deep}' stroke='{hot}' stroke-width='3.5'/>"
            f"<circle cx='128' cy='128' r='20' fill='{pri}'/>"
            f"<circle cx='128' cy='128' r='10' fill='{bone}'/>"
        )
    else:  # sigil
        body = (
            f"<circle cx='128' cy='128' r='88' fill='url(#plateGrad)' stroke='{pri}' stroke-width='3.5'/>"
            f"<circle cx='128' cy='128' r='74' fill='none' stroke='{sec}' stroke-width='2' stroke-dasharray='14 8'/>"
            f"<polygon points='128,44 198,166 58,166' fill='none' stroke='url(#priGrad)' stroke-width='4.5' stroke-linejoin='round'/>"
            f"<polygon points='128,212 198,90 58,90' fill='none' stroke='{sec}' stroke-width='3.5' stroke-linejoin='round' opacity='0.9'/>"
            f"<polygon points='128,74 146,128 128,182 110,128' fill='{pri}' opacity='0.9'/>"
            f"<polygon points='74,128 128,110 182,128 128,146' fill='{hot}' opacity='0.82'/>"
            f"<circle cx='128' cy='128' r='16' fill='{deep}' stroke='{bone}' stroke-width='2.5'/>"
            f"<circle cx='128' cy='128' r='7' fill='{hot}'/>"
        )

    return _svg_shell_open(hex_map) + body + _svg_shell_close(hex_map)


def build_copy(name: str, tagline: str, archetype: str) -> dict[str, str]:
    up_name = re.sub(r"\s+", " ", name.strip()).upper() or "FORGED"
    up_tag = re.sub(r"\s+", " ", tagline.strip()).upper() or "CUSTOM AMOLED THEME"

    # Compact single-line tokens for narrow pills/badges
    words = [w for w in re.split(r"[^A-Za-z0-9]+", up_name) if w]
    short_tag = (words[0][:8] if words else up_name[:8]) or "FORGE"
    short_sub = (words[1][:8] if len(words) > 1 else "AMOLED")

    glyph, sigil_glyph = ARCHETYPE_GLYPHS.get(archetype, ("◈", "✦"))

    return {
        "crown": f"{sigil_glyph}  {up_name}  {sigil_glyph}",
        "sub": up_tag[:42],
        "ecoSub": f"{short_tag} · SUPER FAST ECO",
        "motto": f"{glyph} {up_name} · {up_tag[:28]} {glyph}",
        "glyph": glyph,
        "sigilGlyph": sigil_glyph,
        "fxTag": short_tag,
        "fxOver": "OVERDRIVE",
        "aiPill": f"{sigil_glyph} {short_tag}",
        "quickDeck": f"{glyph} {short_tag} DECK",
        "briefTitle": f"{sigil_glyph} {short_tag} BRIEF",
        "calTitle": f"{short_tag} CHRONO",
        "calBadge": short_tag,
        "calList": "COMING RITES",
        "calToday": "TODAY'S FOCUS",
        "calWeek": "7-DAY HORIZON",
        "vaultTitle": f"{short_tag} VAULT",
        "vaultMotto": f"{short_tag} · {short_sub} · AMOLED",
        "keybarTag": short_tag,
        "aiMorph": f"{sigil_glyph} {short_tag} · FORGED ⇄",
    }


def build_menu_items(name: str, hex_map: dict[str, str], archetype: str) -> list[dict[str, str]]:
    glyph, sigil = ARCHETYPE_GLYPHS.get(archetype, ("◈", "✦"))
    short = (re.split(r"[^A-Za-z0-9]+", name.strip().upper())[0] or "FORGE")[:10]
    return [
        {
            "id": "forge-shell",
            "label": f"{short} TERMINAL",
            "subtitle": "AMOLED ROOT SHELL",
            "glyph": sigil,
            "accent": hex_map["pri"],
            "action": "terminal",
        },
        {
            "id": "forge-deck",
            "label": f"{short} DECK",
            "subtitle": "OBSIDIAN CARDS & CANVAS",
            "glyph": glyph,
            "accent": hex_map["sec"],
            "action": "deck",
        },
        {
            "id": "forge-brief",
            "label": f"{short} BRIEF",
            "subtitle": "AI & SYSTEM TELEMETRY",
            "glyph": "⚡",
            "accent": hex_map["hot"],
            "action": "brief",
        },
        {
            "id": "forge-fx",
            "label": "OVERDRIVE FX",
            "subtitle": "TOGGLE SHADER & GLOW",
            "glyph": "▶",
            "accent": hex_map["mid"],
            "action": "fx",
        },
    ]


def run_owl_hub_mode(tid: str) -> tuple[bool, str]:
    owl_hub = shutil.which("owl-hub") or os.path.expanduser("~/.local/bin/owl-hub")
    if not os.path.isfile(owl_hub):
        return False, f"owl-hub not found at {owl_hub}"
    proc = subprocess.run([owl_hub, "mode", tid], capture_output=True, text=True, check=False)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, err or out or f"owl-hub exited {proc.returncode}"
    try:
        parsed = json.loads(out)
        if isinstance(parsed, dict) and not parsed.get("ok", True):
            return False, str(parsed.get("msg") or parsed.get("error") or out)
    except Exception:
        pass
    return True, out


def active_mode() -> str:
    hub = load_json(HUB_FILE, {})
    if isinstance(hub, dict):
        m = hub.get("mode")
        if isinstance(m, str) and m:
            return m
    return "infernal"


def cmd_list() -> int:
    custom: list[dict[str, Any]] = []
    if os.path.isdir(THEMES_DIR):
        for fn in sorted(os.listdir(THEMES_DIR)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(THEMES_DIR, fn)
            pack = load_json(path)
            if not isinstance(pack, dict):
                continue
            tid = str(pack.get("id") or os.path.splitext(fn)[0])
            tok = pack.get("tok") if isinstance(pack.get("tok"), dict) else {}
            hx = tok.get("hex") if isinstance(tok.get("hex"), dict) else {}
            cp = tok.get("copy") if isinstance(tok.get("copy"), dict) else {}
            tagline = str(pack.get("tagline") or cp.get("sub") or "")
            try:
                mode_idx = max(0, min(3, int(tok.get("modeIdx", 0))))
            except Exception:
                mode_idx = 0
            custom.append({
                "id": tid,
                "name": str(pack.get("name") or tid.upper()),
                "sub": tagline,
                "tagline": tagline,
                "pri": str(hx.get("pri", "#ff1e3c")),
                "sec": str(hx.get("sec", "#3d9bff")),
                "hot": str(hx.get("hot", "#ff8596")),
                "glyph": str(cp.get("sigilGlyph") or cp.get("glyph") or "◈"),
                "modeIdx": mode_idx,
                "archetype": str(pack.get("archetype") or "sigil"),
                "builtin": False,
            })

    emit({
        "ok": True,
        "active": active_mode(),
        "core": CORE_THEMES,
        "custom": custom,
    })
    return 0


def cmd_create(
    name: str,
    tagline: str,
    pri_hex: str,
    sec_hex: str,
    mode_idx_raw: str | int,
    font_key: str,
    archetype: str,
) -> int:
    name_clean = re.sub(r"\s+", " ", (name or "").strip())
    if not name_clean:
        return fail("name is required")
    tagline_clean = re.sub(r"\s+", " ", (tagline or "").strip()) or "CUSTOM AMOLED THEME"

    pri_hex = (pri_hex or "").strip()
    sec_hex = (sec_hex or "").strip()
    if not HEX_RE.match(pri_hex):
        return fail(f"invalid primary hex color (expected #RRGGBB): {pri_hex!r}")
    if not HEX_RE.match(sec_hex):
        return fail(f"invalid secondary hex color (expected #RRGGBB): {sec_hex!r}")

    try:
        mode_idx = max(0, min(3, int(mode_idx_raw)))
    except Exception:
        return fail(f"invalid mode_idx (expected 0..3): {mode_idx_raw!r}")

    fk, font_path, body_font = resolve_font(font_key)
    arch = (archetype or "sigil").strip().lower()
    if arch not in ARCHETYPES:
        arch = "sigil"

    tid = sanitize_slug(name_clean)
    hex_map = compute_amoled_palette(pri_hex, sec_hex)
    svg_str = build_svg_icon(arch, hex_map)
    copy_map = build_copy(name_clean, tagline_clean, arch)
    pads = [hex_map["dim"], hex_map["mid"], hex_map["pri"], hex_map["sec"], hex_map["hot"]]

    tok: dict[str, Any] = {"modeIdx": mode_idx, "faceId": tid}
    for ck, ch in hex_map.items():
        tok[ck] = hex_to_rgb(ch)
    tok["iconTint"] = hex_to_rgb(hex_map["mid"])
    tok["flair"] = hex_to_rgb(hex_map["hot"])
    tok["hex"] = hex_map
    tok["pads"] = pads
    tok["fontPath"] = font_path
    tok["displayFontPath"] = font_path
    tok["bodyFont"] = body_font
    tok["copy"] = copy_map

    pack = {
        "id": tid,
        "name": name_clean.upper(),
        "tagline": tagline_clean.upper(),
        "accentStyle": f"{arch}-amoled",
        "font": fk,
        "archetype": arch,
        "omarchy": OMARCHY_MODES[mode_idx],
        "faceId": tid,
        "iconTint": hex_map["mid"],
        "flair": hex_map["hot"],
        "menuItems": build_menu_items(name_clean, hex_map, arch),
        "iconSvg": svg_str,
        "tok": tok,
    }

    os.makedirs(THEMES_DIR, exist_ok=True)
    os.makedirs(ICONS_DIR, exist_ok=True)
    svg_path = os.path.join(ICONS_DIR, f"{tid}.svg")
    json_path = os.path.join(THEMES_DIR, f"{tid}.json")

    write_text_atomic(svg_path, svg_str)
    write_json_atomic(json_path, pack)

    ok, msg = run_owl_hub_mode(tid)
    if not ok:
        return fail(f"created {tid} but owl-hub failed: {msg}")

    emit({
        "ok": True,
        "id": tid,
        "msg": f"Forged & applied {name_clean}",
    })
    return 0


def cmd_quick(prompt_parts: list[str]) -> int:
    prompt = re.sub(r"\s+", " ", " ".join(prompt_parts).strip())
    if not prompt:
        return fail("quick prompt cannot be empty")

    tokens = [t for t in re.split(r"[^a-z0-9]+", prompt.lower()) if t]
    digest = hashlib.sha256(prompt.lower().encode("utf-8")).digest()

    # 1. Pick colors (scan matching color keywords; secondary blends second keyword if present)
    matched_colors: list[tuple[str, str]] = []
    for tok in tokens:
        if tok in COLOR_KEYWORDS:
            matched_colors.append(COLOR_KEYWORDS[tok])

    if matched_colors:
        pri_hex, sec_hex = matched_colors[0]
        if len(matched_colors) > 1 and matched_colors[1][0].lower() != pri_hex.lower():
            sec_hex = matched_colors[1][0]
    else:
        pri_hex, sec_hex = FALLBACK_PALETTES[digest[0] % len(FALLBACK_PALETTES)]

    # 2. Pick modeIdx (0..3)
    mode_idx = digest[1] % 4
    for tok in tokens:
        if tok in MODE_KEYWORDS:
            mode_idx = MODE_KEYWORDS[tok]
            break

    # 3. Pick font (blackops / russo / mono / archivo)
    font_choices = ("blackops", "russo", "mono")
    font_key = font_choices[digest[2] % len(font_choices)]
    for tok in tokens:
        if tok in FONT_KEYWORDS:
            font_key = FONT_KEYWORDS[tok]
            break

    # 4. Pick SVG archetype
    archetype = ARCHETYPES[digest[3] % len(ARCHETYPES)]
    for tok in tokens:
        if tok in ARCHETYPE_KEYWORDS:
            archetype = ARCHETYPE_KEYWORDS[tok]
            break

    # 5. Format name & tagline
    raw_words = re.sub(r"[^A-Za-z0-9\s-]", "", prompt).split()
    name = " ".join(raw_words[:3]) if raw_words else "FORGE THEME"
    if len(name) > 24:
        name = name[:24].strip()
    tagline = f"{archetype.upper()} FORGE · {prompt.upper()}"[:48]

    return cmd_create(name, tagline, pri_hex, sec_hex, mode_idx, font_key, archetype)


def _valid_theme_id(tid: str) -> bool:
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return False
    if tid in CORE_IDS:
        return True
    return os.path.isfile(os.path.join(THEMES_DIR, f"{tid}.json"))


def cmd_apply(tid: str) -> int:
    tid = (tid or "").strip()
    if not _valid_theme_id(tid):
        return fail(f"unknown theme id: {tid!r}")
    ok, msg = run_owl_hub_mode(tid)
    if not ok:
        return fail(f"failed to apply {tid}: {msg}")
    emit({"ok": True, "active": tid})
    return 0


def cmd_delete(tid: str) -> int:
    tid = (tid or "").strip()
    if tid in CORE_IDS:
        return fail(f"cannot delete core theme: {tid}")
    if not tid or "/" in tid or "\\" in tid or ".." in tid:
        return fail(f"invalid theme id: {tid!r}")

    json_path = os.path.join(THEMES_DIR, f"{tid}.json")
    svg_path = os.path.join(ICONS_DIR, f"{tid}.svg")
    if not os.path.isfile(json_path):
        return fail(f"theme not found: {tid}")

    os.makedirs(BACKUP_REMOVED_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    dst_json = os.path.join(BACKUP_REMOVED_DIR, f"{tid}.json")
    if os.path.exists(dst_json):
        dst_json = os.path.join(BACKUP_REMOVED_DIR, f"{tid}.{stamp}.json")
    shutil.move(json_path, dst_json)

    if os.path.isfile(svg_path):
        dst_svg = os.path.join(BACKUP_REMOVED_DIR, f"{tid}.svg")
        if os.path.exists(dst_svg):
            dst_svg = os.path.join(BACKUP_REMOVED_DIR, f"{tid}.{stamp}.svg")
        shutil.move(svg_path, dst_svg)

    if active_mode() == tid:
        ok, msg = run_owl_hub_mode("infernal")
        if not ok:
            return fail(f"deleted {tid} but failed to switch back to infernal: {msg}")

    return cmd_list()


def main(argv: list[str]) -> int:
    if not argv:
        return fail("usage: theme-forge.py <list|create|quick|apply|delete> ...")

    sub = argv[0].lower()
    args = argv[1:]

    if sub == "list":
        return cmd_list()
    if sub == "create":
        if len(args) < 7:
            return fail("usage: create <name> <tagline> <pri_hex> <sec_hex> <mode_idx> <font_key> <archetype>")
        return cmd_create(args[0], args[1], args[2], args[3], args[4], args[5], args[6])
    if sub == "quick":
        if not args:
            return fail("usage: quick <prompt...>")
        return cmd_quick(args)
    if sub == "apply":
        if len(args) < 1:
            return fail("usage: apply <id>")
        return cmd_apply(args[0])
    if sub == "delete":
        if len(args) < 1:
            return fail("usage: delete <id>")
        return cmd_delete(args[0])

    return fail(f"unknown subcommand: {sub}")


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        sys.exit(1)
