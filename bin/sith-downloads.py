#!/usr/bin/env python3
"""Backend for the omalauncher 'downloads' app (~/.config/omalauncher/widgets/downloads.owl).

  sith-downloads.py list [dir|__trash__] -> JSON {ok, dir, rel, up, items[], count, bytes, trashCount, roots[]}
  sith-downloads.py share <path>         -> Android share / open-with / APK installer sheet
  sith-downloads.py files <dir>          -> opens the launcher's Files app at <dir>
  sith-downloads.py trash <path>         -> safely moves <path> into <root>/.trash/ (never permanent rm!)
  sith-downloads.py restore <path>       -> restores an item from <root>/.trash/ back to <root>/

All paths are strictly confined to the download roots via realpath validation.
"""
import json, os, shutil, subprocess, sys, time

ROOTS = [p for p in ("/sdcard/Download", os.path.expanduser("~/Downloads")) if os.path.isdir(p)]

KINDS = {
    "apk": {"apk", "apks", "xapk", "aab"},
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "heic", "svg"},
    "video": {"mp4", "mkv", "webm", "mov", "avi", "3gp"},
    "audio": {"mp3", "wav", "ogg", "m4a", "flac", "opus", "aac"},
    "doc": {"pdf", "md", "txt", "doc", "docx", "odt", "rtf", "epub", "csv", "xlsx", "pptx", "json", "html"},
    "archive": {"zip", "tar", "gz", "tgz", "xz", "7z", "rar", "bz2", "zst"},
    "code": {"py", "sh", "js", "ts", "owl", "kt", "c", "cpp", "rs", "go", "toml", "yaml", "yml"},
}


def kind_of(name, is_dir):
    if is_dir:
        return "folder"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "other"


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def ago(t):
    d = max(0, time.time() - t)
    if d < 60:
        return "now"
    for secs, unit in ((86400 * 365, "y"), (86400 * 30, "mo"), (86400 * 7, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if d >= secs:
            return f"{int(d // secs)}{unit} ago"
    return "now"


def safe(path):
    """Resolve path and verify it lives strictly inside a download root."""
    if not path:
        return None, None
    real = os.path.realpath(path)
    for r in ROOTS:
        rr = os.path.realpath(r)
        if real == rr or real.startswith(rr + os.sep):
            return real, rr
    return None, None


def dir_size(p):
    total = 0
    for base, _, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def count_trash():
    cnt = 0
    for r in ROOTS:
        td = os.path.join(os.path.realpath(r), ".trash")
        if os.path.isdir(td):
            try:
                cnt += len([x for x in os.listdir(td) if not x.startswith(".")])
            except OSError:
                pass
    return cnt


def cmd_list(arg):
    if not ROOTS:
        return {"ok": False, "dir": "", "rel": "", "up": None, "items": [], "count": 0, "bytes": "0 B", "trashCount": 0, "roots": [], "error": "no Download folder reachable"}

    if arg == "__trash__":
        root = os.path.realpath(ROOTS[0])
        td = os.path.join(root, ".trash")
        items, total = [], 0
        if os.path.isdir(td):
            for n in os.listdir(td):
                if n.startswith("."):
                    continue
                p = os.path.join(td, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                is_dir = os.path.isdir(p)
                size = dir_size(p) if is_dir else st.st_size
                total += size
                orig_name = n.split("_", 1)[1] if "_" in n and n.split("_", 1)[0].isdigit() else n
                items.append({
                    "name": orig_name, "path": p, "isDir": is_dir, "inTrash": True,
                    "kind": kind_of(orig_name, is_dir), "size": size, "sz": human(size),
                    "mtime": int(st.st_mtime), "ago": ago(st.st_mtime),
                    "count": len(os.listdir(p)) if is_dir else 0,
                })
        items.sort(key=lambda i: i["mtime"], reverse=True)
        return {
            "ok": True, "dir": td, "root": root, "rel": "Download/.trash", "up": None,
            "items": items, "count": len(items), "bytes": human(total),
            "trashCount": len(items),
            "roots": [{"name": os.path.basename(r), "path": os.path.realpath(r)} for r in ROOTS],
            "error": None,
        }

    d, root = safe(arg) if arg else (None, None)
    if not d or not os.path.isdir(d):
        d, root = os.path.realpath(ROOTS[0]), os.path.realpath(ROOTS[0])
    items, total = [], 0
    try:
        names = os.listdir(d)
        err = None
    except OSError as e:
        names = []
        err = str(e)
    for n in names:
        if n.startswith("."):
            continue
        p = os.path.join(d, n)
        try:
            st = os.stat(p)
        except OSError:
            continue
        is_dir = os.path.isdir(p)
        size = dir_size(p) if is_dir else st.st_size
        total += size
        items.append({
            "name": n, "path": p, "isDir": is_dir, "inTrash": False,
            "kind": kind_of(n, is_dir), "size": size, "sz": human(size),
            "mtime": int(st.st_mtime), "ago": ago(st.st_mtime),
            "count": len(os.listdir(p)) if is_dir else 0,
        })
    items.sort(key=lambda i: i["mtime"], reverse=True)
    rel = os.path.relpath(d, root)
    return {
        "ok": True, "dir": d, "root": root,
        "rel": os.path.basename(root) + ("" if rel == "." else "/" + rel),
        "up": None if d == root else os.path.dirname(d),
        "items": items, "count": len(items), "bytes": human(total),
        "trashCount": count_trash(),
        "roots": [{"name": os.path.basename(r) + (" (linux)" if r.startswith(os.path.expanduser("~")) else ""), "path": os.path.realpath(r)} for r in ROOTS],
        "error": err,
    }


def cmd_trash(target):
    p, root = safe(target)
    if not p or not root or p == root:
        return {"ok": False, "error": "refused: outside Download root"}
    td = os.path.realpath(os.path.join(root, ".trash"))
    if p == td or p.startswith(td + os.sep):
        return {"ok": False, "error": "already in .trash"}
    os.makedirs(td, exist_ok=True)
    dest = os.path.join(td, f"{int(time.time())}_{os.path.basename(p)}")
    shutil.move(p, dest)
    return {"ok": True, "moved": dest, "trashCount": count_trash()}


def cmd_restore(target):
    p, root = safe(target)
    if not p or not root:
        return {"ok": False, "error": "outside Download root"}
    td = os.path.realpath(os.path.join(root, ".trash"))
    if not p.startswith(td + os.sep) or not os.path.exists(p):
        return {"ok": False, "error": "not in .trash"}
    base = os.path.basename(p)
    orig = base.split("_", 1)[1] if "_" in base and base.split("_", 1)[0].isdigit() else base
    dest = os.path.join(root, orig)
    if os.path.exists(dest):
        stem, dot, ext = orig.rpartition(".")
        dest = os.path.join(root, f"{stem or orig}_restored{'.' + ext if dot else ''}")
    shutil.move(p, dest)
    return {"ok": True, "restored": dest, "trashCount": count_trash()}


def main():
    a = sys.argv[1:]
    if not a or a[0] == "list":
        return cmd_list(a[1] if len(a) > 1 else "")
    if a[0] == "share" and len(a) > 1:
        p, _ = safe(a[1])
        if p and os.path.isfile(p):
            subprocess.run(["omalauncher", "share", "--", p], timeout=10)
            return {"ok": True}
        return {"ok": False, "error": "invalid file"}
    if a[0] == "files" and len(a) > 1:
        p, _ = safe(a[1])
        if p:
            subprocess.run(["omarchy-files", p], timeout=10)
            return {"ok": True}
        return {"ok": False, "error": "invalid dir"}
    if a[0] == "trash" and len(a) > 1:
        return cmd_trash(a[1])
    if a[0] == "restore" and len(a) > 1:
        return cmd_restore(a[1])
    return {"ok": False, "error": "unknown command"}


if __name__ == "__main__":
    try:
        out = main()
    except Exception as exc:
        out = {"ok": False, "error": str(exc)[:180]}
    print(json.dumps(out))
