#!/usr/bin/env python3
"""Deck image helper for 25-sith-command-deck.owl.

  deck-img.py receive               loop in the deck's Terminal strip: pickImages() pastes
                                    photo paths here; each is saved to the inbox
                                    (~/.local/state/deck-img.json is what the widget reads)
  deck-img.py crop SRC ASPECT Z OX OY  -> JSON {ok, path}  cropped copy for a card
  deck-img.py clear                 empties the inbox list (files of placed images are kept)

ASPECT: free|1:1|4:3|3:4|16:9|9:16 (free = whole image, no crop).
Z >= 1 is zoom over a "cover" fit; OX/OY are the image offset as a fraction of the frame.
"""
import json, os, shlex, sys, time

OUT = os.path.expanduser("~/.local/share/omalauncher/deck-images")
STATE = os.path.expanduser("~/.local/state/deck-img.json")
KEEP = 24


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"items": [], "seq": 0}


def save(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def ingest(p):
    from PIL import Image, ImageOps
    os.makedirs(OUT, exist_ok=True)
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((2400, 2400))
        dst = os.path.join(OUT, f"src-{int(time.time() * 1000)}.jpg")
        im.save(dst, "JPEG", quality=90)
        return {"path": dst, "w": im.size[0], "h": im.size[1]}


def handle(paths, st):
    n = 0
    for p in paths:
        if not os.path.isfile(p) or p in st.setdefault("seen", []):
            continue
        st["seen"] = (st["seen"] + [p])[-200:]
        try:
            it = ingest(p)
        except Exception as e:
            print(f"\r\n✕ {os.path.basename(p)}: {e}", flush=True)
            continue
        st["items"] = ([it] + st.get("items", []))[:KEEP]
        n += 1
    if n:
        st["seq"] = st.get("seq", 0) + 1
        save(st)
        print(f"\r\n\033[31m✓ {n} photo(s) ready\033[0m — pick one above", flush=True)


def receive():
    # Raw tty: pickImages() pastes paths with no Enter, so read keystrokes as they come and
    # also watch /tmp, where the picker drops the files.
    import select, termios, tty, glob
    print("\033[31m◈ IMAGE INBOX\033[0m  ready — tap PICK PHOTOS", flush=True)
    fd = sys.stdin.fileno()
    old = None
    try:
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        pass
    start = time.time()
    buf, last = "", 0.0
    try:
        while True:
            r, _, _ = select.select([fd], [], [], 0.6)
            if r:
                ch = os.read(fd, 4096).decode("utf-8", "replace")
                if not ch:
                    break
                buf += ch
                last = time.time()
            st = load()
            if buf and (time.time() - last > 0.5 or "\n" in buf or "\r" in buf):
                try:
                    paths = shlex.split(buf.replace("\r", " ").replace("\n", " "))
                except ValueError:
                    paths = buf.split()
                buf = ""
                handle(paths, st)
            fresh = [f for f in glob.glob("/tmp/**/*", recursive=True)
                     if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"))
                     and os.path.isfile(f) and os.path.getmtime(f) > start
                     and time.time() - os.path.getmtime(f) > 1.0]
            if fresh:
                handle(sorted(fresh), st)
    finally:
        if old:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def crop(src, aspect, z, ox, oy):
    from PIL import Image
    os.makedirs(OUT, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        iw, ih = im.size
        if aspect != "free":
            a, b = (float(x) for x in aspect.split(":"))
            s = max(a / iw, b / ih) * max(1.0, z)
            left = a / 2 - iw * s / 2 + ox * a
            top = b / 2 - ih * s / 2 + oy * b
            cw, ch = min(iw, a / s), min(ih, b / s)
            x0 = min(max(0, -left / s), iw - cw)
            y0 = min(max(0, -top / s), ih - ch)
            im = im.crop((round(x0), round(y0), round(x0 + cw), round(y0 + ch)))
        im.thumbnail((1600, 1600))
        dst = os.path.join(OUT, f"img-{int(time.time() * 1000)}.jpg")
        im.save(dst, "JPEG", quality=88)
    print(json.dumps({"ok": True, "path": dst, "w": im.size[0], "h": im.size[1]}))


def main():
    a = sys.argv[1:]
    try:
        if a == ["receive"]:
            receive()
        elif a == ["clear"]:
            st = load(); st["items"] = []; st["seq"] = st.get("seq", 0) + 1; save(st)
            print(json.dumps({"ok": True}))
        elif len(a) == 6 and a[0] == "crop":
            # W=1: allowlist every argument before it touches anything
            src, aspect = os.path.realpath(a[1]), a[2]
            if not src.startswith(os.path.realpath(OUT) + os.sep):
                raise ValueError("source must be an inbox image")
            if aspect not in ("free", "1:1", "4:3", "3:4", "16:9", "9:16"):
                raise ValueError("bad aspect")
            z, ox, oy = (float(v) for v in a[3:6])
            if not (1.0 <= z <= 6.0 and -1.0 <= ox <= 1.0 and -1.0 <= oy <= 1.0):
                raise ValueError("crop values out of range")
            crop(src, aspect, z, ox, oy)
        else:
            print(json.dumps({"ok": False, "error": "usage: receive | clear | crop SRC ASPECT Z OX OY"}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
