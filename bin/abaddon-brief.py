#!/usr/bin/env python3
"""ABADDON // ULTRON // CYBER-MASK — Fast Face-Tap Assistant Diagnosis & Email Briefing Engine.

CLI:
  python3 ~/.local/bin/abaddon-brief.py <mode> [telemetry_json]

Features:
  - Exclusive non-blocking flock debounce so rapid double-taps never spawn duplicate workers.
  - Instant in-place write (seek(0); truncate()) of scanning state to ~/.config/omalauncher/face-brief.json
    so Omalauncher's file() watcher fires immediately.
  - Sub-2-second live device diagnosis (telemetry + Linux /proc load, uptime, memory, /sdcard & / disk,
    network reachability) + health verdict.
  - Dual-source email notification scan:
      (a) live Android email notifications from telemetry_json (Gmail, Outlook, ProtonMail, K-9,
          Thunderbird, Yahoo, Spark, Fastmail, FairEmail, BlueMail, Samsung Email, Mail, etc.)
      (b) ~/.local/bin/abaddon-mail.py fast read-only IMAP peek (short timeout, never hangs)
  - Appends briefing to ~/.config/omalauncher/abaddon-ultron-state.json under its existing state.lock.
  - Triggers detached voice synthesis via abaddon-ultron.py ONLY if voiceEnabled is true.
"""

from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
OMA_DIR = os.path.join(HOME, ".config", "omalauncher")
BRIEF_FILE = os.path.join(OMA_DIR, "face-brief.json")
STATE_FILE = os.path.join(OMA_DIR, "abaddon-ultron-state.json")
DATA_DIR = os.path.join(HOME, ".local", "share", "omalauncher", "abaddon-ultron")
BRIEF_LOCK = os.path.join(DATA_DIR, "brief.lock")
STATE_LOCK = os.path.join(DATA_DIR, "state.lock")
MAIL_BIN = os.path.join(HOME, ".local", "bin", "abaddon-mail.py")
ULTRON_BIN = os.path.join(HOME, ".local", "bin", "abaddon-ultron.py")

EMAIL_PACKAGES = {
    "com.google.android.gm",
    "com.google.android.gm.lite",
    "com.microsoft.office.outlook",
    "ch.protonmail.android",
    "me.proton.android.mail",
    "com.fsck.k9",
    "net.thunderbird.android",
    "com.yahoo.mobile.client.android.mail",
    "com.samsung.android.email.provider",
    "com.fastmail.app",
    "eu.faircode.email",
    "com.bluemail.mail",
    "me.bluemail.mail",
    "com.edison.email",
    "com.easilydo.mail",
    "com.readdle.spark",
    "de.tutao.tutanota",
    "com.zoho.mail",
    "com.aol.mobile.aolapp",
    "ru.yandex.mail",
    "com.yandex.mail",
    "com.android.email",
    "com.sonymobile.email",
    "com.cloudmagic.mail",
    "com.boxer.email",
    "com.superhuman.mail",
    "io.canarymail.android",
    "com.hey.android",
    "com.spike.mail",
    "com.my.mail",
    "com.gmx.mobile.android.mail",
    "com.web.mobile.android.mail",
    "org.kman.aquamail",
}

EMAIL_APP_PATTERNS = re.compile(
    r"\b(?:gmail|outlook|proton\s*mail|protonmail|k-?9|thunderbird|yahoo\s*mail|"
    r"fastmail|fairemail|bluemail|spark\s*mail|edison\s*mail|tutanota|tuta\s*mail|"
    r"zoho\s*mail|canary\s*mail|superhuman|aquamail|samsung\s*email|e-?mail|mail)\b",
    re.IGNORECASE,
)
EMAIL_APP_EXCLUDE = re.compile(r"\b(?:voicemail|voice\s*mail|dailymail|daily\s*mail|mailchimp)\b", re.IGNORECASE)


def norm_mode(raw):
    s = str(raw or "").strip().lower()
    if s in ("future", "blue", "ultron"):
        return "future"
    if s in ("mask", "green", "neon"):
        return "mask"
    if s in ("skeletor", "purple", "havoc", "violet"):
        return "skeletor"
    return "infernal"


def now_hm():
    return time.strftime("%H:%M")


def _write_json_inplace(path, obj):
    """In-place write (same inode) so Omalauncher's file() watcher fires instantaneously."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = json.dumps(obj, indent=2, ensure_ascii=False)
    with open(path, "a+", encoding="utf-8") as fh:
        fh.seek(0)
        fh.truncate()
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _read_prev_seq():
    try:
        if os.path.isfile(BRIEF_FILE):
            with open(BRIEF_FILE, "r", encoding="utf-8") as fh:
                prev = json.load(fh)
            if isinstance(prev, dict):
                return int(prev.get("seq") or 0)
    except Exception:
        pass
    return 0


def _clean_text(s, limit=64):
    t = re.sub(r"\s+", " ", str(s or "")).strip()
    if len(t) > limit:
        return t[: limit - 1].rstrip() + "…"
    return t


def _probe_linux_system():
    info = {
        "load1": None,
        "cores": os.cpu_count() or 1,
        "uptime": "",
        "memPct": None,
        "rootFreeGb": None,
        "sdcardFreeGb": None,
    }
    try:
        info["load1"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass

    try:
        if os.path.isfile("/proc/uptime"):
            with open("/proc/uptime", "r", encoding="utf-8") as fh:
                secs = int(float(fh.read().split()[0]))
            hrs, rem = divmod(secs, 3600)
            mins = rem // 60
            if hrs >= 24:
                days, hrs = divmod(hrs, 24)
                info["uptime"] = f"{days}d{hrs}h"
            elif hrs > 0:
                info["uptime"] = f"{hrs}h{mins:02d}m"
            else:
                info["uptime"] = f"{mins}m"
    except Exception:
        pass

    try:
        if os.path.isfile("/proc/meminfo"):
            mem_tot = mem_avail = 0
            with open("/proc/meminfo", "r", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("MemTotal:"):
                        mem_tot = int(line.split()[1])
                    elif line.startswith("MemAvailable:"):
                        mem_avail = int(line.split()[1])
                    if mem_tot and mem_avail:
                        break
            if mem_tot > 0:
                info["memPct"] = round(max(0.0, min(100.0, (1.0 - mem_avail / mem_tot) * 100.0)))
    except Exception:
        pass

    try:
        info["rootFreeGb"] = round(shutil.disk_usage("/").free / 1073741824.0, 1)
    except Exception:
        pass

    try:
        if os.path.isdir("/sdcard"):
            info["sdcardFreeGb"] = round(shutil.disk_usage("/sdcard").free / 1073741824.0, 1)
    except Exception:
        pass

    return info


def _probe_network(timeout_s=0.45):
    """Fast non-blocking network reachability probe (<0.5s)."""
    has_route = False
    try:
        if os.path.isfile("/proc/net/route"):
            with open("/proc/net/route", "r", encoding="utf-8") as fh:
                next(fh, None)
                for line in fh:
                    cols = line.split()
                    if len(cols) >= 4 and cols[1] == "00000000" and (int(cols[3], 16) & 0x2):
                        has_route = True
                        break
    except Exception:
        pass

    for host in ("1.1.1.1", "8.8.8.8"):
        sock = None
        try:
            sock = socket.create_connection((host, 53), timeout=timeout_s)
            return True
        except OSError:
            pass
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
    return has_route


def _is_email_notif(item):
    if not isinstance(item, dict):
        return False
    if item.get("isEmail") is True or str(item.get("category") or "").lower() == "email":
        return True
    pkg = str(item.get("package") or item.get("pkg") or "").strip().lower()
    if pkg in EMAIL_PACKAGES or ("mail" in pkg and "voicemail" not in pkg):
        return True
    app = str(item.get("app") or item.get("appName") or "").strip()
    if app and EMAIL_APP_PATTERNS.search(app) and not EMAIL_APP_EXCLUDE.search(app):
        return True
    return False


def _extract_android_emails(telemetry):
    """Extract email notification entries from whatever notification shape telemetry_json carries."""
    raw_list = None
    has_notif_list = False
    for k in ("emailNotifs", "notifs", "notifItems", "notifications"):
        val = telemetry.get(k)
        if isinstance(val, list):
            raw_list = val
            has_notif_list = True
            break

    items = []
    seen = set()
    if not raw_list:
        return items, has_notif_list

    for entry in raw_list:
        if isinstance(entry, str):
            low = entry.lower()
            if EMAIL_APP_PATTERNS.search(low) and not EMAIL_APP_EXCLUDE.search(low):
                cleaned = _clean_text(entry, 68)
                key = cleaned.lower()
                if cleaned and key not in seen:
                    seen.add(key)
                    items.append(cleaned)
            continue

        if not isinstance(entry, dict):
            continue
        if not _is_email_notif(entry):
            continue

        sender = _clean_text(
            entry.get("title") or entry.get("sender") or entry.get("from") or entry.get("app") or "Mail",
            28,
        )
        subj = _clean_text(
            entry.get("text") or entry.get("subject") or entry.get("body") or entry.get("snippet") or "",
            42,
        )
        # Avoid redundant "Gmail: 2 new messages" if text repeats sender
        if subj and subj.lower() != sender.lower():
            label = f"{sender}: {subj}"
        else:
            label = sender
        key = label.lower()
        if label and key not in seen:
            seen.add(key)
            items.append(label)

    return items, has_notif_list


def _probe_imap_mail(timeout_s=1.15):
    """Query abaddon-mail.py peek with a strict timeout so the briefing never hangs."""
    if not os.path.isfile(MAIL_BIN):
        return {"ok": False, "configured": False, "unread": 0, "items": [], "reason": "MAIL SCRIPT MISSING"}
    try:
        proc = subprocess.run(
            [sys.executable, MAIL_BIN, "peek", str(timeout_s)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s + 0.55,
        )
        out = (proc.stdout or "").strip()
        if out:
            data = json.loads(out.splitlines()[-1])
            if isinstance(data, dict):
                return data
        return {"ok": False, "configured": True, "unread": 0, "items": [], "reason": "IMAP EMPTY RESPONSE"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "configured": True, "unread": 0, "items": [], "reason": "IMAP TIMEOUT"}
    except Exception as e:
        return {"ok": False, "configured": True, "unread": 0, "items": [], "reason": _clean_text(e, 36).upper()}


def _build_diagnosis(telemetry, sysinfo, net_ok):
    bat = telemetry.get("bat")
    if bat is None:
        bat = telemetry.get("battery")
    try:
        bat_num = int(round(float(bat))) if bat is not None else None
    except (TypeError, ValueError):
        bat_num = None

    charging = bool(telemetry.get("charging", False))

    temp = telemetry.get("temp")
    try:
        temp_num = round(float(temp), 1) if temp is not None and str(temp).strip() != "" else None
    except (TypeError, ValueError):
        temp_num = None

    ram = telemetry.get("ramPct")
    if ram is None:
        ram = sysinfo.get("memPct")
    try:
        ram_num = int(round(float(ram))) if ram is not None else None
    except (TypeError, ValueError):
        ram_num = None

    free_gb = telemetry.get("freeGb")
    if free_gb is None:
        free_gb = sysinfo.get("sdcardFreeGb") if sysinfo.get("sdcardFreeGb") is not None else sysinfo.get("rootFreeGb")
    try:
        free_num = round(float(free_gb), 1) if free_gb is not None else None
    except (TypeError, ValueError):
        free_num = None

    load1 = sysinfo.get("load1")
    cores = sysinfo.get("cores") or 1

    notif_count = telemetry.get("notifCount")
    if notif_count is None:
        raw_n = telemetry.get("notifs")
        if isinstance(raw_n, list):
            notif_count = len(raw_n)
        elif isinstance(raw_n, (int, float)):
            notif_count = int(raw_n)
        else:
            notif_count = 0

    crit_reasons = []
    warn_reasons = []

    if bat_num is not None and not charging:
        if bat_num <= 15:
            crit_reasons.append("CRIT BAT")
        elif bat_num <= 25:
            warn_reasons.append("LOW BAT")
    if temp_num is not None:
        if temp_num >= 45.0:
            crit_reasons.append("OVERHEAT")
        elif temp_num >= 40.0:
            warn_reasons.append("WARM")
    if ram_num is not None:
        if ram_num >= 95:
            crit_reasons.append("CRIT RAM")
        elif ram_num >= 88:
            warn_reasons.append("HIGH RAM")
    if free_num is not None:
        if free_num <= 1.0:
            crit_reasons.append("DISK FULL")
        elif free_num <= 3.0:
            warn_reasons.append("LOW DISK")
    if load1 is not None and load1 > max(cores * 3.0, 24.0):
        warn_reasons.append("HIGH LOAD")
    if not net_ok:
        warn_reasons.append("OFFLINE")

    if crit_reasons:
        verdict = "ALERT"
        verdict_text = f"ALERT ({', '.join(crit_reasons[:2])})"
    elif warn_reasons:
        verdict = "WATCH"
        verdict_text = f"WATCH ({', '.join(warn_reasons[:2])})"
    else:
        verdict = "NOMINAL"
        verdict_text = "NOMINAL"

    parts = []
    if bat_num is not None:
        bat_str = f"BAT {bat_num}%" + ("⚡" if charging else "")
        if temp_num is not None:
            bat_str += f" ({temp_num:.0f}°C)" if temp_num == int(temp_num) else f" ({temp_num:.1f}°C)"
        parts.append(bat_str)
    if ram_num is not None:
        parts.append(f"RAM {ram_num}%")
    if free_num is not None:
        parts.append(f"{free_num:.1f}G FREE")
    if load1 is not None:
        parts.append(f"LOAD {load1:.2f}")
    parts.append("NET OK" if net_ok else "NET OFF")
    parts.append(verdict_text)

    diag_line = " · ".join(parts)
    metrics = {
        "bat": bat_num,
        "charging": charging,
        "temp": temp_num,
        "ram": ram_num,
        "freeGb": free_num,
        "load1": load1,
        "uptime": sysinfo.get("uptime", ""),
        "netOk": net_ok,
        "notifCount": notif_count,
        "verdict": verdict,
        "verdictText": verdict_text,
    }
    return diag_line, metrics


def _build_mail_section(android_items, has_notif_list, imap_res):
    merged = []
    seen = set()

    for it in android_items:
        k = it.lower()
        if k not in seen:
            seen.add(k)
            merged.append(it)

    imap_ok = bool(imap_res.get("ok"))
    imap_cfg = bool(imap_res.get("configured"))
    imap_unread = int(imap_res.get("unread") or 0) if imap_ok else 0

    if imap_ok:
        for raw in imap_res.get("items") or []:
            if not isinstance(raw, dict):
                continue
            sender = _clean_text(raw.get("from") or "Sender", 26)
            subj = _clean_text(raw.get("subject") or "(no subject)", 42)
            label = f"{sender}: {subj}"
            k = label.lower()
            if k not in seen:
                seen.add(k)
                merged.append(label)

    mail_items = merged[:4]
    unread = max(imap_unread, len(merged))

    if unread > 0:
        preview = " | ".join(mail_items[:2])
        if len(mail_items) > 2:
            preview += f" (+{unread - 2})"
        mail_line = f"{unread} UNREAD · {preview}" if preview else f"{unread} UNREAD"
    elif imap_ok:
        mail_line = "0 UNREAD · INBOX CLEAR"
    elif imap_cfg:
        reason = _clean_text(imap_res.get("reason") or "UNREACHABLE", 32).upper()
        mail_line = f"EMAIL CHECK: {reason}"
    elif has_notif_list:
        mail_line = "0 UNREAD · INBOX CLEAR"
    else:
        mail_line = "0 UNREAD · INBOX CLEAR (NO IMAP ENV)"

    return mail_line, unread, mail_items


def _build_persona_copy(mode, metrics, mail_line, unread, mail_items):
    bat = metrics["bat"]
    chg = metrics["charging"]
    temp = metrics["temp"]
    ram = metrics["ram"]
    free_gb = metrics["freeGb"]
    verdict = metrics["verdict"]
    net_ok = metrics["netOk"]

    if mode == "future":
        headline = "ULTRON · DIAGNOSIS & MAIL"
        s1_parts = []
        if bat is not None:
            s1_parts.append(f"Power reserves at {bat} percent" + (" on external charge" if chg else ""))
        if temp is not None:
            s1_parts.append(f"thermal core {temp} degrees")
        if ram is not None:
            s1_parts.append(f"memory at {ram} percent")
        if free_gb is not None:
            s1_parts.append(f"{free_gb} gigabytes free")
        s1 = "Diagnostic sweep complete: " + (", ".join(s1_parts) if s1_parts else "telemetry nominal") + f". System verdict: {verdict.lower()}" + ("" if net_ok else ", network offline") + "."
        if unread > 0:
            top = "; ".join(mail_items[:2])
            s2 = f"You have {unread} unread email notification" + ("s" if unread != 1 else "") + (f": {top}." if top else ".")
        elif mail_line.startswith("EMAIL CHECK:"):
            s2 = f"Email check returned a notice: {mail_line.split(':', 1)[1].strip().lower()}."
        else:
            s2 = "Inbox is clear with zero unread email notifications."
        return headline, f"{s1} {s2}"

    if mode == "mask":
        headline = "THE MASK · DIAGNOSIS & MAIL"
        s1_parts = []
        if bat is not None:
            s1_parts.append(f"battery at {bat} percent" + (" and juicing up" if chg else ""))
        if ram is not None:
            s1_parts.append(f"RAM at {ram} percent")
        if free_gb is not None:
            s1_parts.append(f"{free_gb} gigabytes free")
        body_s1 = ", ".join(s1_parts)
        if body_s1:
            body_s1 = body_s1[0].upper() + body_s1[1:]
        s1 = "Smokin' systems check! " + (body_s1 if body_s1 else "Vitals locked in") + f" — status is {metrics['verdictText'].lower()}."
        if unread > 0:
            top = "; ".join(mail_items[:2])
            s2 = f"Mail radar spotted {unread} unread message" + ("s" if unread != 1 else "") + (f": {top}." if top else ".")
        elif mail_line.startswith("EMAIL CHECK:"):
            s2 = f"Mail check flagged: {mail_line.split(':', 1)[1].strip().lower()}."
        else:
            s2 = "Zero unread emails — your inbox is squeaky clean!"
        return headline, f"{s1} {s2}"

    if mode == "skeletor":
        headline = "☩ HAVOC DIAGNOSTICS"
        s1_parts = []
        if bat is not None:
            s1_parts.append(f"power reserves at {bat} percent" + (" drawing current" if chg else ""))
        if temp is not None:
            s1_parts.append(f"thermal core {temp} degrees")
        if ram is not None:
            s1_parts.append(f"memory at {ram} percent")
        if free_gb is not None:
            s1_parts.append(f"{free_gb} gigabytes free")
        s1 = "By the power of Havoc, the arcane scan is complete: " + (", ".join(s1_parts) if s1_parts else "all runes steady") + f". Verdict stands {metrics['verdictText'].lower()}."
        if unread > 0:
            top = "; ".join(mail_items[:2])
            s2 = f"Snake Mountain intercepts {unread} unread dispatch" + ("es" if unread != 1 else "") + (f": {top}." if top else ".")
        elif mail_line.startswith("EMAIL CHECK:"):
            s2 = f"Mail scrying reports: {mail_line.split(':', 1)[1].strip().lower()}."
        else:
            s2 = "Zero unread dispatches — the inbox crypt is silent."
        return headline, f"{s1} {s2}"

    # infernal (Abaddon)
    headline = "ABADDON · DIAGNOSIS & MAIL"
    s1_parts = []
    if bat is not None:
        s1_parts.append(f"power core at {bat} percent" + (" drinking current" if chg else ""))
    if temp is not None:
        s1_parts.append(f"heat at {temp} degrees")
    if ram is not None:
        s1_parts.append(f"memory load {ram} percent")
    if free_gb is not None:
        s1_parts.append(f"{free_gb} gigabytes free")
    s1 = "Vessel reckoning complete, Master: " + (", ".join(s1_parts) if s1_parts else "vitals steady") + f". Status stands {verdict.lower()}."
    if unread > 0:
        top = "; ".join(mail_items[:2])
        s2 = f"You hold {unread} unread missive" + ("s" if unread != 1 else "") + (f": {top}." if top else ".")
    elif mail_line.startswith("EMAIL CHECK:"):
        s2 = f"Mail watch reports: {mail_line.split(':', 1)[1].strip().lower()}."
    else:
        s2 = "Your inbox is clear, Master — no unread email notifications."
    return headline, f"{s1} {s2}"


def _append_to_ultron_state(mode, headline, diag_line, mail_line, summary):
    """Append the briefing message into abaddon-ultron-state.json using its existing state.lock."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OMA_DIR, exist_ok=True)
    key = {"future": "ultron", "mask": "mask", "skeletor": "havoc"}.get(mode, "abaddon")
    tag = (
        "⬡ ULTRON · FACE BRIEFING"
        if mode == "future"
        else (
            "✦ THE MASK · FACE BRIEFING"
            if mode == "mask"
            else ("☩ HAVOC · FACE BRIEFING" if mode == "skeletor" else "⛧ ABADDON · FACE BRIEFING")
        )
    )
    body = f"{diag_line}\n{mail_line}\n{summary}"
    voice_enabled = False

    lock_f = open(STATE_LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        st = None
        if os.path.isfile(STATE_FILE):
            try:
                with open(STATE_FILE, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    st = loaded
            except Exception:
                st = None
        if st is None:
            st = {
                "busy": False,
                "phase": "idle",
                "voiceEnabled": True,
                "showThinking": True,
                "busyLimit": 120.0,
                "brainMode": "haiku",
                "audioSeq": 0,
                "audioPath": "",
                "audioDur": 0.0,
                "lastMode": mode,
                "statusLine": headline,
                "workerPid": 0,
                "speakPid": 0,
                "busySince": 0.0,
                "activeReqId": "",
                "speakingUntil": 0.0,
                "abaddon": [],
                "ultron": [],
                "mask": [],
                "havoc": [],
            }

        msgs = list(st.get(key) or [])
        msgs.append({
            "id": f"brief-{int(time.time() * 1000)}",
            "role": "entity",
            "tag": tag,
            "ts": now_hm(),
            "text": body,
        })
        st[key] = msgs[-18:]
        st["lastMode"] = mode
        if not st.get("busy"):
            st["statusLine"] = headline
        voice_enabled = bool(st.get("voiceEnabled", True))

        _write_json_inplace(STATE_FILE, st)
    finally:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        finally:
            lock_f.close()

    return voice_enabled


def _trigger_voice_detached(mode, summary):
    if not summary or not os.path.isfile(ULTRON_BIN):
        return
    try:
        subprocess.Popen(
            [sys.executable, ULTRON_BIN, "speak-only", mode, summary],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _replay_last_brief(mode_arg=None):
    """Fast voice replay of the last face-brief.json summary (used by HUD 🔊 BRIEF button)."""
    summary = ""
    mode = norm_mode(mode_arg) if mode_arg else "infernal"
    try:
        if os.path.isfile(BRIEF_FILE):
            with open(BRIEF_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                summary = str(data.get("summary") or "").strip()
                if not mode_arg and data.get("mode"):
                    mode = norm_mode(data.get("mode"))
    except Exception:
        pass
    if not summary:
        summary = "No briefing cached yet. Tap the centerpiece face to run diagnostics."
    _trigger_voice_detached(mode, summary)
    print(json.dumps({"ok": True, "replayed": True, "mode": mode}))
    return 0


def main(argv):
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OMA_DIR, exist_ok=True)

    # Fast voice replay path (no scan, no debounce wait)
    if len(argv) > 1 and str(argv[1]).strip().lower() in ("speak", "speak-brief", "replay"):
        return _replay_last_brief(argv[2] if len(argv) > 2 else None)

    lock_f = open(BRIEF_LOCK, "a+", encoding="utf-8")
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_f.close()
        print(json.dumps({"ok": True, "debounced": True}))
        return 0

    try:
        mode = norm_mode(argv[1] if len(argv) > 1 else "infernal")
        raw_tel = argv[2] if len(argv) > 2 else ""
        try:
            telemetry = json.loads(raw_tel) if raw_tel else {}
            if not isinstance(telemetry, dict):
                telemetry = {}
        except Exception:
            telemetry = {}

        base_seq = _read_prev_seq()
        scan_seq = base_seq + 1
        scan_state = {
            "seq": scan_seq,
            "ts": now_hm(),
            "mode": mode,
            "busy": True,
            "verdict": "NOMINAL",
            "headline": "SCANNING SYSTEMS & MAIL...",
            "diag": "RUNNING LIVE DIAGNOSTICS...",
            "mail": "CHECKING EMAIL NOTIFICATIONS...",
            "unread": 0,
            "mailItems": [],
            "summary": "Scanning device diagnostics and checking email notifications...",
        }
        _write_json_inplace(BRIEF_FILE, scan_state)

        # Fast local probes + parallel network & IMAP peek (strictly < 2s total)
        sysinfo = _probe_linux_system()
        android_emails, has_notif_list = _extract_android_emails(telemetry)

        with ThreadPoolExecutor(max_workers=2) as pool:
            net_fut = pool.submit(_probe_network, 0.45)
            imap_fut = pool.submit(_probe_imap_mail, 1.15)
            try:
                net_ok = bool(net_fut.result(timeout=0.75))
            except Exception:
                net_ok = False
            try:
                imap_res = imap_fut.result(timeout=1.65)
            except Exception:
                imap_res = {"ok": False, "configured": True, "unread": 0, "items": [], "reason": "IMAP TIMEOUT"}

        diag_line, metrics = _build_diagnosis(telemetry, sysinfo, net_ok)
        mail_line, unread, mail_items = _build_mail_section(android_emails, has_notif_list, imap_res)
        headline, summary = _build_persona_copy(mode, metrics, mail_line, unread, mail_items)

        final_brief = {
            "seq": scan_seq + 1,
            "ts": now_hm(),
            "mode": mode,
            "busy": False,
            "verdict": metrics["verdict"],
            "headline": headline,
            "diag": diag_line,
            "mail": mail_line,
            "unread": unread,
            "mailItems": mail_items,
            "summary": summary,
        }
        _write_json_inplace(BRIEF_FILE, final_brief)

        voice_enabled = _append_to_ultron_state(mode, headline, diag_line, mail_line, summary)
        if voice_enabled:
            _trigger_voice_detached(mode, summary)

        print(json.dumps(final_brief, ensure_ascii=False))
        return 0
    finally:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
        finally:
            lock_f.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
