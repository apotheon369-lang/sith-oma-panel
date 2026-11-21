#!/usr/bin/env python3
"""Backend helper for the Sith Lyfe Secret Control Panel in 35-synth-pads.owl."""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys

WIDGETS_DIR = os.path.expanduser("~/.config/omalauncher/widgets")
REMOVED_DIR = os.path.join(WIDGETS_DIR, ".removed")

PROTECTED_PKGS = {
    "base",
    "base-devel",
    "bash",
    "glibc",
    "pacman",
    "filesystem",
    "archlinuxarm-keyring",
    "coreutils",
    "foot",
    "python",
}

PROTECTED_OWL = {
    "35-synth-pads.owl",
    "drawer.owl",
    "keybar.owl",
}

PLUGIN_GROUPS = [
    ("vst3-plugins", "VST3"),
    ("clap-plugins", "CLAP"),
    ("lv2-plugins", "LV2"),
    ("ladspa-plugins", "LADSPA"),
]

PLUGIN_DIRS = [
    ("/usr/lib/vst3", "VST3", True),
    ("/usr/local/lib/vst3", "VST3", True),
    (os.path.expanduser("~/.vst3"), "VST3", False),
    ("/usr/lib/clap", "CLAP", True),
    (os.path.expanduser("~/.clap"), "CLAP", False),
    ("/usr/lib/lv2", "LV2", True),
    (os.path.expanduser("~/.lv2"), "LV2", False),
    ("/usr/lib/ladspa", "LADSPA", True),
    (os.path.expanduser("~/.ladspa"), "LADSPA", False),
]

PKG_RE = re.compile(r"^[a-zA-Z0-9@._+-]+$")
OWL_HEADER_RE = re.compile(r'^\s*(widget|face|app|panel|keybar)\s+"([^"]+)"')


def run_cmd(args, timeout=180):
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def parse_size_bytes(size_str):
    parts = size_str.strip().split()
    if len(parts) < 2:
        return 0
    try:
        val = float(parts[0])
    except ValueError:
        return 0
    unit = parts[1].lower()
    mult = {
        "b": 1,
        "kib": 1024,
        "kb": 1000,
        "mib": 1024**2,
        "mb": 1000**2,
        "gib": 1024**3,
        "gb": 1000**3,
    }.get(unit, 1)
    return int(val * mult)


def fmt_bytes(n):
    if n >= 1024**3:
        return f"{n / (1024**3):.1f}G"
    if n >= 1024**2:
        return f"{n / (1024**2):.1f}M"
    if n >= 1024:
        return f"{n / 1024:.0f}K"
    return f"{n}B"


def parse_pacman_info(text):
    items = []
    cur = {}
    for line in text.splitlines():
        if not line.strip():
            if cur.get("Name"):
                items.append(cur)
                cur = {}
            continue
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            cur[k.strip()] = v.strip()
    if cur.get("Name"):
        items.append(cur)
    return items


def scan_owl_dir(directory, active):
    out = []
    if not os.path.isdir(directory):
        return out
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".owl") or fname.startswith("."):
            continue
        fpath = os.path.join(directory, fname)
        if not os.path.isfile(fpath):
            continue
        kind = "widget"
        title = fname[:-4]
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for _ in range(15):
                    line = f.readline()
                    if not line:
                        break
                    m = OWL_HEADER_RE.match(line)
                    if m:
                        kind = m.group(1)
                        title = m.group(2)
                        break
            sz = os.path.getsize(fpath)
        except OSError:
            sz = 0
        out.append({
            "file": fname,
            "name": title,
            "kind": kind,
            "active": active,
            "size": fmt_bytes(sz),
            "protected": active and fname in PROTECTED_OWL,
        })
    return out


def scan_desktop_apps():
    apps = []
    seen = set()
    dirs = [
        (os.path.expanduser("~/.local/share/applications"), False),
        ("/usr/share/applications", True),
    ]
    paths_for_qo = []
    raw_entries = []
    for d, is_sys in dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".desktop"):
                continue
            fpath = os.path.join(d, fname)
            if not os.path.isfile(fpath):
                continue
            name = None
            exec_cmd = ""
            no_display = False
            hidden = False
            in_main = False
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        s = line.strip()
                        if s.startswith("["):
                            in_main = s == "[Desktop Entry]"
                            continue
                        if not in_main or "=" not in s:
                            continue
                        k, v = s.split("=", 1)
                        if k == "Name" and name is None:
                            name = v.strip()
                        elif k == "Exec" and not exec_cmd:
                            exec_cmd = re.sub(r"\s+%[fFuUdDnNickvm]", "", v.strip())
                        elif k == "NoDisplay" and v.strip().lower() == "true":
                            no_display = True
                        elif k == "Hidden" and v.strip().lower() == "true":
                            hidden = True
            except OSError:
                continue
            if hidden or no_display or not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            raw_entries.append((name, exec_cmd, fpath, fname, is_sys))
            if is_sys:
                paths_for_qo.append(fpath)

    owners = {}
    if paths_for_qo:
        res = run_cmd(["pacman", "-Qo"] + paths_for_qo, timeout=10)
        for line in res.stdout.splitlines():
            m = re.match(r"^(.+) is owned by ([^\s]+)\s+", line)
            if m:
                owners[m.group(1)] = m.group(2)

    for name, exec_cmd, fpath, fname, is_sys in raw_entries:
        pkg = owners.get(fpath, "")
        apps.append({
            "id": fname,
            "name": name,
            "exec": exec_cmd,
            "pkg": pkg,
            "path": fpath,
            "sys": is_sys,
            "core": pkg in PROTECTED_PKGS if pkg else False,
        })
    return apps


def cmd_scan():
    group_names = [g for g, _ in PLUGIN_GROUPS]
    fmt_by_group = dict(PLUGIN_GROUPS)
    fmt_rank = {"VST3": 0, "CLAP": 1, "LV2": 2, "LADSPA": 3}

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        f_all = ex.submit(run_cmd, ["pacman", "-Q"], 10)
        f_exp = ex.submit(run_cmd, ["pacman", "-Qei"], 15)
        f_orph = ex.submit(run_cmd, ["pacman", "-Qdtq"], 10)
        f_sg = ex.submit(run_cmd, ["pacman", "-Sg"] + group_names, 10)
        f_desk = ex.submit(scan_desktop_apps)
        q_all = f_all.result()
        q_exp = f_exp.result()
        q_orph = f_orph.result()
        sg = f_sg.result()
        linux_apps = f_desk.result()

    installed_vers = {}
    for line in q_all.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            installed_vers[parts[0]] = parts[1]

    linux_pkgs = []
    exp_map = {}
    for info in parse_pacman_info(q_exp.stdout):
        name = info.get("Name", "")
        if not name:
            continue
        size_str = info.get("Installed Size", "0 B")
        b = parse_size_bytes(size_str)
        pkg_obj = {
            "name": name,
            "ver": info.get("Version", ""),
            "size": fmt_bytes(b),
            "bytes": b,
            "desc": info.get("Description", ""),
            "core": name in PROTECTED_PKGS,
        }
        linux_pkgs.append(pkg_obj)
        exp_map[name] = pkg_obj
    linux_pkgs.sort(key=lambda x: (-x["bytes"], x["name"]))

    orphans = [l.strip() for l in q_orph.stdout.splitlines() if l.strip()]

    plugin_map = {}
    for line in sg.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        grp, pkg = parts[0], parts[1]
        fmt = fmt_by_group.get(grp, "AUDIO")
        if pkg not in plugin_map:
            is_inst = pkg in installed_vers
            plugin_map[pkg] = {
                "id": pkg,
                "name": pkg,
                "fmt": fmt,
                "installed": is_inst,
                "ver": installed_vers.get(pkg, ""),
                "size": exp_map[pkg]["size"] if pkg in exp_map else "",
                "kind": "pkg",
                "path": "",
            }

    for d, fmt, is_sys in PLUGIN_DIRS:
        if is_sys or not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            full = os.path.join(d, entry)
            pid = f"file:{full}"
            plugin_map[pid] = {
                "id": pid,
                "name": entry,
                "fmt": fmt,
                "installed": True,
                "ver": "local",
                "size": "",
                "kind": "file",
                "path": full,
            }

    audio_plugins = sorted(
        plugin_map.values(),
        key=lambda x: (0 if x["installed"] else 1, fmt_rank.get(x["fmt"], 9), x["name"]),
    )

    owl_active = scan_owl_dir(WIDGETS_DIR, True)
    owl_disabled = scan_owl_dir(REMOVED_DIR, False)
    owl_modules = owl_active + owl_disabled

    plugins_installed = sum(1 for p in audio_plugins if p["installed"])

    data = {
        "summary": {
            "pluginsInstalled": plugins_installed,
            "pluginsTotal": len(audio_plugins),
            "owlActive": len(owl_active),
            "owlDisabled": len(owl_disabled),
            "linuxApps": len(linux_apps),
            "linuxPkgs": len(linux_pkgs),
            "orphans": len(orphans),
        },
        "audioPlugins": audio_plugins,
        "owlModules": owl_modules,
        "linuxApps": linux_apps,
        "linuxPkgs": linux_pkgs,
        "orphans": orphans,
    }
    print(json.dumps(data, separators=(",", ":")))


def reply(ok, msg):
    print(json.dumps({"ok": ok, "msg": msg}))
    sys.exit(0 if ok else 0)


def validate_pkg(pkg):
    if not pkg or not PKG_RE.match(pkg):
        reply(False, "Invalid package name")


def validate_owl(fname):
    if not fname or "/" in fname or "\\" in fname or ".." in fname or not fname.endswith(".owl"):
        reply(False, "Invalid module filename")


def cmd_pkg_install(pkg):
    validate_pkg(pkg)
    res = run_cmd(["pacman", "-S", "--needed", "--noconfirm", pkg], timeout=300)
    if res.returncode == 0:
        reply(True, f"Installed {pkg}")
    err = (res.stderr or res.stdout or "Install failed").strip().splitlines()
    reply(False, err[-1][:120] if err else f"Failed to install {pkg}")


def cmd_pkg_remove(pkg):
    validate_pkg(pkg)
    if pkg in PROTECTED_PKGS:
        reply(False, f"{pkg} is a protected core package")
    res = run_cmd(["pacman", "-Rns", "--noconfirm", pkg], timeout=180)
    if res.returncode != 0:
        # Fallback to -R if -Rns trips on optional/group dep cleanup
        res2 = run_cmd(["pacman", "-R", "--noconfirm", pkg], timeout=180)
        if res2.returncode == 0:
            reply(True, f"Uninstalled {pkg}")
        err = (res2.stderr or res.stderr or "Uninstall failed").strip().splitlines()
        reply(False, err[-1][:120] if err else f"Cannot uninstall {pkg}")
    reply(True, f"Purged {pkg}")


def cmd_purge_orphans():
    q = run_cmd(["pacman", "-Qdtq"], timeout=10)
    orphs = [l.strip() for l in q.stdout.splitlines() if l.strip()]
    if not orphs:
        reply(True, "No orphan packages found")
    res = run_cmd(["pacman", "-Rns", "--noconfirm"] + orphs, timeout=180)
    if res.returncode == 0:
        reply(True, f"Purged {len(orphs)} orphan(s)")
    err = (res.stderr or "Failed").strip().splitlines()
    reply(False, err[-1][:120] if err else "Orphan purge failed")


def cmd_clean_cache():
    res = run_cmd(["pacman", "-Sc", "--noconfirm"], timeout=60)
    if res.returncode == 0:
        reply(True, "Pacman cache purged")
    reply(False, "Cache clean failed")


def cmd_owl_disable(fname):
    validate_owl(fname)
    if fname in PROTECTED_OWL:
        reply(False, f"{fname} is protected")
    src = os.path.join(WIDGETS_DIR, fname)
    if not os.path.isfile(src):
        reply(False, f"{fname} not found")
    os.makedirs(REMOVED_DIR, exist_ok=True)
    dst = os.path.join(REMOVED_DIR, fname)
    shutil.move(src, dst)
    reply(True, f"Disabled {fname}")


def cmd_owl_restore(fname):
    validate_owl(fname)
    src = os.path.join(REMOVED_DIR, fname)
    if not os.path.isfile(src):
        reply(False, f"{fname} not in archive")
    dst = os.path.join(WIDGETS_DIR, fname)
    shutil.move(src, dst)
    reply(True, f"Restored {fname}")


def cmd_owl_purge(fname):
    validate_owl(fname)
    if fname in PROTECTED_OWL:
        reply(False, f"{fname} is protected")
    p_rem = os.path.join(REMOVED_DIR, fname)
    p_act = os.path.join(WIDGETS_DIR, fname)
    target = p_rem if os.path.isfile(p_rem) else (p_act if os.path.isfile(p_act) else None)
    if not target:
        reply(False, f"{fname} not found")
    os.remove(target)
    reply(True, f"Deleted {fname}")


def cmd_plugin_file_remove(path):
    real = os.path.realpath(path)
    allowed_roots = [
        os.path.realpath(os.path.expanduser(p))
        for p in ("~/.vst3", "~/.lv2", "~/.clap", "~/.ladspa")
    ]
    if not any(real.startswith(r + os.sep) for r in allowed_roots):
        reply(False, "Path outside user plugin dirs")
    if os.path.isdir(real):
        shutil.rmtree(real)
    elif os.path.isfile(real):
        os.remove(real)
    else:
        reply(False, "Plugin file not found")
    reply(True, f"Removed {os.path.basename(real)}")


def cmd_desktop_remove(path):
    real = os.path.realpath(path)
    allowed = os.path.realpath(os.path.expanduser("~/.local/share/applications"))
    if not real.startswith(allowed + os.sep) or not real.endswith(".desktop"):
        reply(False, "Only user .desktop shortcuts can be deleted directly")
    if os.path.isfile(real):
        os.remove(real)
        reply(True, f"Removed {os.path.basename(real)}")
    reply(False, "Shortcut not found")


def main():
    if len(sys.argv) < 2:
        reply(False, "Missing action")
    act = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if act == "scan":
        cmd_scan()
    elif act == "pkg-install":
        cmd_pkg_install(arg)
    elif act == "pkg-remove":
        cmd_pkg_remove(arg)
    elif act == "purge-orphans":
        cmd_purge_orphans()
    elif act == "clean-cache":
        cmd_clean_cache()
    elif act == "owl-disable":
        cmd_owl_disable(arg)
    elif act == "owl-restore":
        cmd_owl_restore(arg)
    elif act == "owl-purge":
        cmd_owl_purge(arg)
    elif act == "plugin-file-remove":
        cmd_plugin_file_remove(arg)
    elif act == "desktop-remove":
        cmd_desktop_remove(arg)
    else:
        reply(False, f"Unknown action: {act}")


if __name__ == "__main__":
    main()
