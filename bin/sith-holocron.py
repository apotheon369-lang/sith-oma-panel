#!/usr/bin/env python3
"""Fast non-blocking backend for the Sith Holocron OWL widget (<50ms, zero subprocess/sleep)."""
import json
import os

CPU_CACHE = "/tmp/sith-holocron-cpu.json"
HOME = os.environ.get("HOME", os.path.expanduser("~"))
THEME_PATH = os.path.join(HOME, ".config/omalauncher/sith-theme.json")
FLEET_PATH = os.path.join(HOME, ".config/omalauncher/fleet/fleet-369.json")
DEF_THEME = {"preset": "vader", "accent": "#ff1e3c", "hot": "#ff6677"}


def cpu_pct():
    with open("/proc/stat", "r") as f:
        p = [int(x) for x in f.readline().split()[1:]]
    tot = sum(p)
    idle = p[3] + (p[4] if len(p) > 4 else 0)
    cpu = round(100 * (1 - idle / tot)) if tot > 0 else 0
    try:
        with open(CPU_CACHE, "r") as cf:
            prev = json.load(cf)
        dt = tot - int(prev.get("total", 0))
        di = idle - int(prev.get("idle", 0))
        if dt > 0:
            cpu = max(0, min(100, round(100 * (1 - di / dt))))
        elif "cpu" in prev:
            cpu = int(prev["cpu"])
    except Exception:
        pass
    try:
        with open(CPU_CACHE, "w") as cf:
            json.dump({"total": tot, "idle": idle, "cpu": cpu}, cf)
    except Exception:
        pass
    return cpu


def mem():
    tot = avail = free = 0
    with open("/proc/meminfo", "r") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                tot = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1])
                if tot:
                    break
            elif line.startswith("MemFree:"):
                free = int(line.split()[1])
    used = max(0, tot - (avail or free))
    pct = round(100 * used / tot) if tot > 0 else 0
    return pct, "%.1fG" % (used / 1048576)


def disk_free():
    st = os.statvfs("/")
    return "%.1fG" % ((st.f_bavail * st.f_frsize) / 1073741824)


def uptime():
    with open("/proc/uptime", "r") as f:
        s = int(float(f.read().split()[0]))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    return (f"{d}d " if d else "") + f"{h}h {s // 60}m"


def swarm_state():
    try:
        with open(FLEET_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "swarm_on" in data:
            return bool(data["swarm_on"])
    except Exception:
        pass
    return True


def _hex(v):
    return isinstance(v, str) and v.startswith("#") and len(v) in (4, 7, 9)


def theme():
    out = dict(DEF_THEME)
    try:
        with open(THEME_PATH, "r") as f:
            t = json.load(f)
        name = str(t.get("active") or t.get("preset") or "vader")
        presets = t.get("presets", t)
        p = None
        if isinstance(presets, dict):
            p = presets.get(name)
        elif isinstance(presets, list):
            p = next(
                (
                    x
                    for x in presets
                    if isinstance(x, dict)
                    and (x.get("id") == name or x.get("name") == name)
                ),
                None,
            )
        acc = hot = None
        if isinstance(p, dict):
            acc = p.get("accent") or p.get("primary") or p.get("color") or p.get("border")
            hot = p.get("hot") or p.get("glow") or p.get("secondary") or p.get("accent2")
        elif isinstance(p, (list, tuple)) and p:
            acc = p[0]
            hot = p[1] if len(p) > 1 else None
        elif isinstance(p, str):
            acc = p
        if not _hex(acc):
            acc = t.get("accent")
        if not _hex(hot):
            hot = t.get("hot")
        out["preset"] = name
        if _hex(acc):
            out["accent"] = acc
        if _hex(hot):
            out["hot"] = hot
    except Exception:
        pass
    return out


def main():
    try:
        pct, used = mem()
        out = {
            "ok": True,
            "cpu": cpu_pct(),
            "ram_pct": pct,
            "ram_used": used,
            "disk_free": disk_free(),
            "fleet_count": 31,
            "swarm_on": swarm_state(),
            "uptime": uptime(),
        }
    except Exception as e:
        out = {
            "ok": False,
            "error": str(e),
            "cpu": 0,
            "ram_pct": 0,
            "ram_used": "0G",
            "disk_free": "?",
            "fleet_count": 31,
            "swarm_on": False,
            "uptime": "?",
        }
    out.update(theme())
    print(json.dumps(out))


if __name__ == "__main__":
    main()
