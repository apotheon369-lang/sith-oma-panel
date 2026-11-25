#!/usr/bin/env python3
"""SITH // DARKFLY — red/black themed launcher for the DarkFly5 CLI.

Tool class: OFFENSIVE / DUAL-USE security tooling (installer + launcher).
Use only on systems you own or have written authorization to test
(your lab: 127.0.0.1:8099 Web Lab, CTFs, scoped engagements).
"""
import json, os, shutil, subprocess, sys
from pathlib import Path

DF = os.path.expanduser("~/.venvs/darkfly/bin/DarkFly5")
CAT = Path(__import__("darkfly").__file__).parent / "catalog.json"

# Go-backend tools land in ~/go/bin and pipx shims in ~/.local/bin; make sure
# both are visible so installed tools are detected and runnable from the app.
for _extra in (str(Path.home() / "go" / "bin"), str(Path.home() / ".local" / "bin")):
    if _extra not in os.environ.get("PATH", "").split(os.pathsep) and os.path.isdir(_extra):
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + _extra

R, H, D, M, G, W, B, X = ("\033[38;2;255;30;60m", "\033[38;2;255;90;110m", "\033[38;2;120;20;35m",
                          "\033[38;2;170;120;130m", "\033[38;2;43;255;140m", "\033[38;2;255;235;238m",
                          "\033[1m", "\033[0m")
BG = "\033[48;2;8;2;6m"

# Tools that live only in the AUR (no official pacman package). id -> AUR pkg.
AUR_MAP = {"john": "john-git", "dnsenum": "dnsenum2"}


def copy(text):
    for cmd in (["termux-clipboard-set"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text, text=True, timeout=5); return True
            except Exception:
                pass
    return False


GROUPS = [
    ("RECON",        ["recon", "osint", "dns", "subdomain"]),
    ("SCANNING",     ["scanning", "network", "port"]),
    ("WEB",          ["web", "fuzz", "xss", "sqli", "proxy"]),
    ("PASSWORDS",    ["password", "cracking", "hash", "brute"]),
    ("WIRELESS",     ["wireless", "wifi"]),
    ("EXPLOIT/POST", ["exploit", "post", "ad", "pivot", "c2", "windows"]),
    ("DEFENSE/FORENSICS", ["forensics", "secrets", "cloud", "reversing", "analysis", "defense"]),
    ("REFERENCE",    ["wordlist", "reference", "docs"]),
]
DEFENSIVE = {"truffelhog", "trufflehog", "tshark", "wireshark", "exiftool", "radare2", "scout-suite",
             "hashid", "wafw00f", "seclists", "hacktricks", "gtfobins", "payloadsallthethings", "owasp-zap"}


def load():
    tools = json.loads(CAT.read_text())["tools"]
    for t in tools:
        cats = [c.lower() for c in t.get("categories", [])]
        t["_g"] = next((g for g, keys in GROUPS if cats and any(k in cats[0] for k in keys)), "OTHER")
        t["_cls"] = "DEF" if t["id"] in DEFENSIVE else "OFF"
        t["_have"] = bool(t.get("run") and shutil.which(str(t["run"]).split()[0]))
    return tools


def cols():
    return max(48, min(shutil.get_terminal_size((60, 30)).columns, 100))


def banner():
    w = cols()
    os.system("clear")
    art = [" ▄▀▀▀▀▄ ▄▀▀▄ █▀▀▄ █  ▄▀ █▀▀▀ █    █   █",
           " █    █ █▄▄█ █▄▄▀ █▀▄   █▀▀  █    ▀▄▄▄▀",
           " ▀▄▄▄▄▀ ▀  ▀ ▀  ▀ ▀  ▀ ▀    ▀▀▀▀   ▀  "]
    shades = [R, H, D]
    for i, line in enumerate(art):
        print(B + shades[i] + line[:w] + X)
    print(f"{M} ⛧ SITH // DARKFLY 5.1.2 · curated security arsenal{X}")
    print(f"{D}{'━' * w}{X}")
    print(f"{R}{B} ⚠ OFFENSIVE / DUAL-USE{X}{M} — only targets you own or are authorized to test.{X}")
    print(f"{D}{'━' * w}{X}")


def tag(t):
    have = f"{G}●{X}" if t["_have"] else f"{D}○{X}"
    cls = f"{G}DEF{X}" if t["_cls"] == "DEF" else f"{R}OFF{X}"
    return have, cls


def list_tools(tools, query=""):
    shown, n = [], 0
    for g, _ in GROUPS + [("OTHER", [])]:
        items = [t for t in tools if t["_g"] == g and (query in t["name"].lower() or query in t["id"])]
        if not items:
            continue
        print(f"\n{R}{B} ✠ {g}{X} {D}{'─' * max(2, cols() - len(g) - 5)}{X}")
        for t in items:
            n += 1
            shown.append(t)
            have, cls = tag(t)
            print(f"  {have} {M}{n:>2}{X} {W}{t['name'][:30]:<30}{X} {cls}")
    return shown


def card(t):
    banner()
    have, cls = tag(t)
    w = cols() - 4
    print(f"\n {R}{B}◈ {t['name'].upper()}{X}   {cls}  {have} {'installed' if t['_have'] else 'not installed'}")
    desc = t.get("description", "")
    line = ""
    for word in desc.split():
        if len(line) + len(word) > w:
            print(f"  {W}{line}{X}"); line = ""
        line += word + " "
    print(f"  {W}{line}{X}")
    print(f"\n  {M}run{X} {H}{t.get('run','-')}{X}   {M}size{X} ~{t.get('size_mb_estimate','?')}MB   {M}license{X} {t.get('license','?')}")
    print(f"  {M}home{X} {D}{t.get('homepage','')}{X}")
    aur = AUR_MAP.get(t["id"])
    if aur:
        print(f"\n  {H}⚠ AUR tool{X}{M} — not in Arch's official repos.{X}")
        print(f"  {M}Install with an AUR helper, as a non-root user:{X}")
        print(f"    {G}yay -S {aur}{X}")
    print(f"\n  {R}[i]{X} install  {R}[r]{X} run  {R}[u]{X} uninstall  {R}[enter]{X} back")
    c = input(f"\n {R}⛧ ›{X} ").strip().lower()
    if c == "i" and aur:
        copy(f"yay -S {aur}")
        print(f"  {G}✓ copied:{X} yay -S {aur}  {M}(run it in a normal user shell){X}"); input(f"{M}enter…{X}")
    elif c == "i":
        subprocess.call([DF, "install", t["id"]]); input(f"{M}enter…{X}")
    elif c == "u":
        subprocess.call([DF, "uninstall", t["id"]]); input(f"{M}enter…{X}")
    elif c == "r" and t["_have"]:
        os.system("clear"); subprocess.call(str(t["run"]).split() + ["--help"]); input(f"{M}enter…{X}")


CODEX = Path.home() / ".local" / "share" / "sith-darkfly" / "codex.md"


def _wrap(text, w):
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        indent = len(para) - len(para.lstrip(" -"))
        pad = " " * indent
        line = pad
        for word in para.strip().split():
            if len(line) + len(word) + 1 > w and line.strip():
                out.append(line); line = pad + "  "
            line += word + " "
        out.append(line.rstrip())
    return out


def codex():
    if not CODEX.exists():
        input(f"{R}codex missing{X} "); return
    raw = CODEX.read_text()
    secs, cur = [], None
    for ln in raw.splitlines():
        if ln.startswith("# "):
            cur = [ln[2:].strip(), []]; secs.append(cur)
        elif cur is not None:
            cur[1].append(ln)
    while True:
        banner()
        print(f"\n {R}{B}✠ RED TEAM CODEX{X}{M} — the arsenal is nothing without the mind.{X}\n")
        for i, (title, _) in enumerate(secs, 1):
            print(f"  {R}{i:>2}{X} {W}{title}{X}")
        print(f"\n{D}{'━' * cols()}{X}")
        c = input(f" {M}# to read · a=all · q=back{X}\n {R}⛧ ›{X} ").strip().lower()
        if c in ("q", "", "0"):
            return
        picks = range(1, len(secs) + 1) if c == "a" else ([int(c)] if c.isdigit() and 1 <= int(c) <= len(secs) else [])
        for idx in picks:
            title, body = secs[idx - 1]
            banner()
            print(f"\n {R}{B}✠ {title}{X}")
            print(f"{D}{'─' * cols()}{X}")
            for ln in body:
                w = cols() - 2
                ln = ln.replace("**", "")
                if ln.startswith("- ") or ln.startswith("  "):
                    for wl in _wrap(ln, w):
                        print(f" {W}{wl}{X}")
                elif ln.strip():
                    for wl in _wrap(ln, w):
                        print(f" {H}{B}{wl}{X}")
                else:
                    print()
            input(f"\n{D}{'─' * cols()}{X}\n {M}enter — next ›{X} ")


# Heavy / GUI / huge-wordlist tools held out of "arm all" by default.
HEAVY = {"metasploit-framework", "burpsuite", "zap", "caido", "wireshark", "searchsploit",
         "seclists", "payloadsallthethings", "rockyou", "hacktricks",
         "gtfobins", "wpscan"}


def arm_all(tools, include_heavy=False):
    targets = [t for t in tools if not t["_have"] and t["id"] not in AUR_MAP
               and (include_heavy or t["id"] not in HEAVY)]
    banner()
    skipped_aur = [t for t in tools if t["id"] in AUR_MAP and not t["_have"]]
    skipped_heavy = [t for t in tools if t["id"] in HEAVY and not include_heavy]
    print(f"\n {R}{B}✠ ARM ALL{X}{M} — forging the arsenal.{X}")
    print(f"  {G}{len(targets)}{X} to install   {D}·{X}  {M}{len(skipped_heavy)} heavy held{X}  {D}·{X}  {M}{len(skipped_aur)} AUR (manual){X}")
    if not targets:
        print(f"\n  {G}Nothing to do — all armed.{X}"); input(f"{M}enter…{X}"); return
    print(f"{D}{'─' * cols()}{X}")
    ok, fail = [], []
    for i, t in enumerate(targets, 1):
        print(f"  {M}[{i:>2}/{len(targets)}]{X} {W}{t['name'][:28]:<28}{X} {D}{t['install']['backend']}{X} … ", end="", flush=True)
        r = subprocess.run([DF, "install", "--yes", t["id"]], capture_output=True, text=True)
        if r.returncode == 0:
            print(f"{G}✓{X}"); ok.append(t["id"])
        else:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            print(f"{R}✗{X} {D}{tail[-1][:40] if tail else ''}{X}"); fail.append(t["id"])
    print(f"{D}{'─' * cols()}{X}")
    print(f"  {G}✓ {len(ok)} armed{X}   {R}✗ {len(fail)} failed{X}")
    if fail:
        print(f"  {M}failed:{X} {', '.join(fail)}")
    if skipped_aur:
        print(f"  {M}AUR (run in a non-root shell):{X}")
        for t in skipped_aur:
            print(f"    {G}yay -S {AUR_MAP[t['id']]}{X}  {D}# {t['name']}{X}")
    input(f"\n{M}enter…{X}")


def main():
    if len(sys.argv) > 1:  # passthrough: sith-darkfly.py list / doctor / ...
        os.execv(DF, [DF, *sys.argv[1:]])
    query = ""
    while True:
        tools = load()
        banner()
        shown = list_tools(tools, query)
        have = sum(t["_have"] for t in tools)
        print(f"\n{D}{'━' * cols()}{X}")
        print(f" {G}●{X}{M} {have}/{len(tools)} armed{X}   {R}#{X} open  {R}/word{X} search  {R}A{X} arm all  {R}c{X} codex  {R}d{X} doctor  {R}v{X} verify  {R}q{X} quit")
        c = input(f" {R}⛧ ›{X} ").strip()
        if c in ("q", "0", "exit"):
            break
        if c == "A":
            arm_all(tools); continue
        if c == "AA":
            arm_all(tools, include_heavy=True); continue
        if c.lower() == "c":
            codex(); continue
        if c.startswith("/"):
            query = c[1:].lower().strip(); continue
        if c == "d":
            subprocess.call([DF, "doctor"]); input(f"{M}enter…{X}"); continue
        if c == "v":
            subprocess.call([DF, "verify"]); input(f"{M}enter…{X}"); continue
        if c.isdigit() and 1 <= int(c) <= len(shown):
            card(shown[int(c) - 1]); continue
        query = ""


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print(X)
