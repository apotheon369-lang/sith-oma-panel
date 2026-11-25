#!/usr/bin/env python3
"""ABADDON mail — read-only Gmail inbox check over IMAP.

Usage:
  abaddon-mail.py check [infernal|future|mask|skeletor]  # count unread + list newest unread; speak it aloud
  abaddon-mail.py status                                 # verify credentials/connection, print JSON, no speech
  abaddon-mail.py peek [timeout_s]                       # fast read-only JSON peek with short timeout, no speech

Credentials live in ~/.config/omalauncher/mail.env (chmod 600), one per line:
  GMAIL_USER=<your gmail address>
  GMAIL_APP_PASSWORD=<16-character google app password>

Read-only by design: the mailbox is opened with EXAMINE (read-only) and headers
are fetched with BODY.PEEK, so no message is marked as read or changed.
The password is never printed.
"""

import email
import imaplib
import json
import os
import socket
import subprocess
import sys
import time
from email.header import decode_header, make_header
from email.utils import parseaddr

HOME = os.path.expanduser("~")
ENV_FILE = os.path.join(HOME, ".config", "omalauncher", "mail.env")
CACHE_FILE = os.path.join(HOME, ".local", "share", "omalauncher", "abaddon-ultron", "mail-cache.json")
ASSISTANT = os.path.join(HOME, ".local", "bin", "abaddon-ultron.py")
IMAP_HOST = "imap.gmail.com"
MAX_LIST = 5


def load_env():
    creds = {}
    if not os.path.isfile(ENV_FILE):
        return creds
    with open(ENV_FILE, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip().strip('"').strip("'")
    return creds


def decode(value):
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _save_cache(result):
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        payload = {
            "ts": time.time(),
            "unread": int(result.get("unread", 0)),
            "items": list(result.get("items") or [])[:MAX_LIST],
        }
        with open(CACHE_FILE, "a+", encoding="utf-8") as fh:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(payload, indent=2))
            fh.flush()
    except Exception:
        pass


def fetch_unread(timeout=20):
    creds = load_env()
    user = creds.get("GMAIL_USER")
    pw = creds.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        raise RuntimeError(f"no credentials: create {ENV_FILE} with GMAIL_USER and GMAIL_APP_PASSWORD")

    conn = imaplib.IMAP4_SSL(IMAP_HOST, timeout=timeout)
    try:
        conn.login(user, pw)
        typ, _ = conn.select("INBOX", readonly=True)  # EXAMINE: nothing gets changed
        if typ != "OK":
            raise RuntimeError("could not open INBOX")
        typ, data = conn.search(None, "UNSEEN")
        if typ != "OK":
            raise RuntimeError("search failed")
        ids = data[0].split() if data and data[0] else []
        total = len(ids)
        items = []
        for mid in reversed(ids[-MAX_LIST:]):  # newest first
            typ, msg = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if typ != "OK" or not msg or not msg[0]:
                continue
            raw = msg[0][1] if isinstance(msg[0], tuple) else b""
            hdr = email.message_from_bytes(raw)
            decoded_from = decode(hdr.get("From", ""))
            realname, addr = parseaddr(decoded_from)
            sender = (realname or addr or decoded_from).strip()
            items.append({
                "from": sender,
                "addr": addr,
                "subject": decode(hdr.get("Subject", "")) or "(no subject)",
                "date": hdr.get("Date", ""),
            })
        res = {"ok": True, "configured": True, "unread": total, "items": items}
        _save_cache(res)
        return res
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def spoken_summary(result):
    if result["unread"] == 0:
        return "Your inbox is clear, Master. No unread messages."
    parts = [f"You have {result['unread']} unread messages."]
    for it in result["items"]:
        parts.append(f"From {it['from']}: {it['subject']}.")
    return " ".join(parts)


def speak(mode, text):
    if not os.path.isfile(ASSISTANT):
        return
    subprocess.run([sys.executable, ASSISTANT, "speak", mode, text],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "check"
    mode = argv[2] if len(argv) > 2 else "infernal"
    if cmd == "peek":
        try:
            timeout_s = max(0.4, min(float(argv[2]), 15.0)) if len(argv) > 2 else 2.0
        except Exception:
            timeout_s = 2.0
        creds = load_env()
        if not (creds.get("GMAIL_USER") and creds.get("GMAIL_APP_PASSWORD")):
            print(json.dumps({"ok": False, "configured": False, "unread": 0, "items": [], "reason": "IMAP NOT CONFIGURED"}))
            return 0
        try:
            result = fetch_unread(timeout=timeout_s)
            print(json.dumps(result))
            return 0
        except socket.timeout:
            print(json.dumps({"ok": False, "configured": True, "unread": 0, "items": [], "reason": "IMAP TIMEOUT"}))
            return 1
        except imaplib.IMAP4.error as e:
            print(json.dumps({"ok": False, "configured": True, "unread": 0, "items": [], "reason": f"IMAP AUTH: {e}"}))
            return 1
        except Exception as e:
            print(json.dumps({"ok": False, "configured": True, "unread": 0, "items": [], "reason": str(e)}))
            return 1
    if cmd == "status":
        creds = load_env()
        if creds.get("GMAIL_USER") and creds.get("GMAIL_APP_PASSWORD"):
            try:
                result = fetch_unread()
                print(json.dumps({"ok": True, "unread": result["unread"]}))
                return 0
            except Exception as e:
                print(json.dumps({"ok": False, "msg": str(e)}))
                return 1
        print(json.dumps({"ok": False, "msg": f"missing credentials in {ENV_FILE}"}))
        return 1
    try:
        result = fetch_unread()
        text = spoken_summary(result)
        speak(mode, text)
        print(json.dumps({"ok": True, "unread": result["unread"], "items": result["items"], "spoken": text}))
        return 0
    except imaplib.IMAP4.error as e:
        msg = f"mail login failed: {e}"
    except Exception as e:
        msg = str(e)
    speak(mode, "I could not reach your mail, Master.")
    print(json.dumps({"ok": False, "msg": msg}))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
