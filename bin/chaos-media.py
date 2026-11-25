#!/usr/bin/env python3
import json
import os
import signal
import socket
import sys
import subprocess
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

LIB_DIR = Path.home() / "Music" / "ChaosLibrary"
SCAN_DIRS = [
    LIB_DIR,
    Path.home() / "Music",
    Path.home() / "Downloads",
    Path("/sdcard/Music"),
    Path("/sdcard/Download"),
    Path("/sdcard/Movies"),
    Path("/sdcard/Void Music"),
]
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac"}
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov"}
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS

MPV_SOCK = "/tmp/chaos-mpv.sock"
MPV_PID = "/tmp/chaos-mpv.pid"
STATE_FILE = Path("/tmp/chaos-player-state.json")
CACHE_FILE = Path("/tmp/chaos-library-cache.json")
GDRIVE_STATE = Path("/tmp/chaos-gdrive.json")
GDRIVE_REMOTE = "gdrive:ChaosMusicVault"
GDRIVE_ACCOUNT = os.environ.get("GDRIVE_ACCOUNT", "")
RCLONE_CONF = Path.home() / ".config" / "rclone" / "rclone.conf"
UPLOAD_PID = "/tmp/chaos-upload.pid"
UPLOAD_PORT = 8765


def gdrive_connected():
    try:
        return RCLONE_CONF.exists() and "[gdrive]" in RCLONE_CONF.read_text(errors="ignore")
    except Exception:
        return False


def load_gdrive_state():
    base = {"syncing": False, "lastSync": "", "lastFile": "", "error": ""}
    if GDRIVE_STATE.exists():
        try:
            d = json.loads(GDRIVE_STATE.read_text())
            if isinstance(d, dict):
                base.update(d)
        except Exception:
            pass
    return base


def save_gdrive_state(st):
    try:
        GDRIVE_STATE.write_text(json.dumps(st))
    except Exception:
        pass


def trigger_gdrive_sync(target_path=""):
    if not gdrive_connected():
        return
    import subprocess
    subprocess.Popen(
        [sys.executable, __file__, "gdrive-sync-worker", target_path],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_gdrive_sync_worker(target_path=""):
    if not gdrive_connected():
        return
    import subprocess
    ensure_lib()
    st = load_gdrive_state()
    st["syncing"] = True
    st["error"] = ""
    save_gdrive_state(st)
    src = target_path if (target_path and os.path.exists(target_path)) else str(LIB_DIR)
    try:
        res = subprocess.run(
            ["rclone", "copy", src, GDRIVE_REMOTE, "--create-empty-src-dirs"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        st["syncing"] = False
        if res.returncode == 0:
            st["lastSync"] = time.strftime("%H:%M")
            st["lastFile"] = os.path.basename(src) if target_path else "ALL FILES"
            st["error"] = ""
        else:
            st["error"] = (res.stderr or "Sync failed").strip().splitlines()[-1][:80]
    except Exception as e:
        st["syncing"] = False
        st["error"] = str(e)[:80]
    save_gdrive_state(st)


def run_gdrive_auth():
    import subprocess
    print("==============================================")
    print("  NEON CHAOS // GOOGLE DRIVE SYNC SETUP")
    print(f"  Account : {GDRIVE_ACCOUNT}")
    print(f"  Folder  : {GDRIVE_REMOTE}")
    print("==============================================")
    print("")
    print("Opening Google sign-in in your browser...")
    print(f"Sign in with {GDRIVE_ACCOUNT} and tap Allow.")
    print("")
    res = subprocess.run(["rclone", "config", "create", "gdrive", "drive", "scope=drive"])
    if res.returncode == 0 and gdrive_connected():
        print("")
        print(f"✓ Connected! Syncing Chaos Library to {GDRIVE_REMOTE}...")
        run_gdrive_sync_worker("")
        print("✓ All downloads synced! You can close this window.")
    else:
        print("")
        print("✕ Setup did not complete. Tap LINK GDRIVE again to retry.")
    time.sleep(3)


def ensure_lib():
    if not LIB_DIR.exists():
        LIB_DIR.mkdir(parents=True, exist_ok=True)


def invalidate_cache():
    try:
        if CACHE_FILE.exists():
            CACHE_FILE.unlink()
    except OSError:
        pass


def load_state():
    base = {"loop": False, "shuffle": False, "lastPath": "", "eqBass": 0, "eqMid": 0, "eqTreble": 0}
    if STATE_FILE.exists():
        try:
            loaded = json.loads(STATE_FILE.read_text())
            if isinstance(loaded, dict):
                base.update(loaded)
        except Exception:
            pass
    return base


def build_af(bass, mid, treble):
    b = max(-12, min(12, int(round(float(bass)))))
    m = max(-12, min(12, int(round(float(mid)))))
    t = max(-12, min(12, int(round(float(treble)))))
    if b == 0 and m == 0 and t == 0:
        return ""
    return (
        f"lavfi=[equalizer=f=90:t=q:w=1.0:g={b},"
        f"equalizer=f=1000:t=q:w=1.0:g={m},"
        f"equalizer=f=8000:t=q:w=1.0:g={t}]"
    )


EQ_PRESETS = {
    "flat": (0, 0, 0),
    "bass": (8, 0, -1),
    "vocal": (-3, 5, 2),
    "bright": (-2, 1, 7),
    "warm": (4, 2, -4),
    "loud": (6, -2, 5),
}


def apply_live_eq(st):
    # No-op when mpv isn't running; the EQ is applied via --af on next play.
    if not mpv_alive():
        return
    af = build_af(st.get("eqBass", 0), st.get("eqMid", 0), st.get("eqTreble", 0))
    # Replacing the filter chain reinitialises audio (tiny gap); skip if unchanged.
    cur = mpv_cmd(["get_property", "af"])
    if isinstance(cur, list):
        cur_graph = cur[0].get("params", {}).get("graph", "") if len(cur) == 1 else None
        want_graph = af[len("lavfi=["):-1] if af else ""
        if (not af and not cur) or (af and cur_graph == want_graph):
            return
    mpv_cmd(["set_property", "af", af])


def save_state(st):
    try:
        STATE_FILE.write_text(json.dumps(st))
    except Exception:
        pass


def clean_title(path_str):
    name = Path(path_str).stem
    name = name.replace("_", " ").replace("  ", " ").strip()
    return name or Path(path_str).name


def scan_library():
    ensure_lib()
    seen = set()
    tracks = []
    for base in SCAN_DIRS:
        if not base.exists():
            continue
        try:
            for root, dirs, files in os.walk(base):
                # Skip hidden dirs and thumbnails
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                depth = len(Path(root).relative_to(base).parts)
                if depth > 2:
                    dirs[:] = []
                for fname in files:
                    if fname.startswith("."):
                        continue
                    ext = Path(fname).suffix.lower()
                    if ext not in MEDIA_EXTS:
                        continue
                    full = os.path.join(root, fname)
                    real = os.path.realpath(full)
                    if real in seen:
                        continue
                    seen.add(real)
                    try:
                        st = os.stat(full)
                        size_mb = round(st.st_size / (1024 * 1024), 1)
                        mtime = st.st_mtime
                    except OSError:
                        size_mb = 0.0
                        mtime = 0.0
                    is_lib = str(Path(full)).startswith(str(LIB_DIR))
                    tracks.append({
                        "path": full,
                        "name": clean_title(full),
                        "file": fname,
                        "ext": ext.lstrip(".").upper(),
                        "isVideo": ext in VIDEO_EXTS,
                        "sizeMb": size_mb,
                        "mtime": mtime,
                        "uploaded": is_lib,
                    })
        except PermissionError:
            continue
    tracks.sort(key=lambda t: (-t["mtime"], t["name"].lower()))
    return tracks


def mpv_cmd(command):
    if not os.path.exists(MPV_SOCK):
        return None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.25)
        s.connect(MPV_SOCK)
        payload = (json.dumps({"command": command}) + "\n").encode("utf-8")
        s.sendall(payload)
        data = b""
        while b"\n" not in data:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            return None
        # Skip async event lines; return the first actual command reply.
        while True:
            for line in data.split(b"\n"):
                if not line.strip():
                    continue
                res = json.loads(line.decode("utf-8", errors="replace"))
                if "event" in res:
                    continue
                if res.get("error") == "success":
                    return res.get("data")
                return None
            chunk = s.recv(4096)
            if not chunk:
                return None
            data = chunk
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass


def mpv_alive():
    if not os.path.exists(MPV_PID):
        return False
    try:
        pid = int(Path(MPV_PID).read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def stop_mpv():
    if os.path.exists(MPV_PID):
        try:
            pid = int(Path(MPV_PID).read_text().strip())
            os.kill(pid, signal.SIGTERM)
            for _ in range(10):
                time.sleep(0.03)
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
        except Exception:
            pass
        try:
            os.unlink(MPV_PID)
        except OSError:
            pass
    try:
        if os.path.exists(MPV_SOCK):
            os.unlink(MPV_SOCK)
    except OSError:
        pass


def play_file(path_str, video=False):
    if not path_str or not os.path.exists(path_str):
        return
    stop_mpv()
    st = load_state()
    st["lastPath"] = path_str
    save_state(st)

    cmd = [
        "mpv",
        "--ao=pulse",
        "--really-quiet",
        f"--input-ipc-server={MPV_SOCK}",
        "--keep-open=no",
    ]
    if not video:
        cmd.append("--no-video")
    if st.get("loop"):
        cmd.append("--loop-file=inf")
    af = build_af(st.get("eqBass", 0), st.get("eqMid", 0), st.get("eqTreble", 0))
    if af:
        cmd.append(f"--af={af}")
    cmd.append(path_str)

    env = os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR", "/run/user/0")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    Path(MPV_PID).write_text(str(proc.pid))


def step_track(delta):
    tracks = scan_library()
    if not tracks:
        return
    st = load_state()
    cur = mpv_cmd(["get_property", "path"]) or st.get("lastPath", "")
    if st.get("shuffle") and len(tracks) > 1:
        choices = [t["path"] for t in tracks if t["path"] != cur]
        play_file(random.choice(choices))
        return
    idx = -1
    for i, t in enumerate(tracks):
        if t["path"] == cur:
            idx = i
            break
    nxt = 0 if idx < 0 else (idx + delta) % len(tracks)
    play_file(tracks[nxt]["path"])


def upload_server_running():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.15)
        s.connect(("127.0.0.1", UPLOAD_PORT))
        s.close()
        return True
    except Exception:
        return False


def start_upload_server():
    if upload_server_running():
        return
    proc = subprocess.Popen(
        [sys.executable, __file__, "serve-upload"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    Path(UPLOAD_PID).write_text(str(proc.pid))
    for _ in range(20):
        if upload_server_running():
            break
        time.sleep(0.05)


UPLOAD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEON CHAOS // MEDIA UPLOAD</title>
<style>
  :root {
    --bg: #070001;
    --card: #140105;
    --border: rgba(255, 23, 56, 0.45);
    --red: #ff1738;
    --crimson-hi: #ff5c72;
    --text: #fff0f2;
    --muted: #c9727f;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    min-height: 100vh;
    background: radial-gradient(circle at 20% 10%, #2d020d 0%, #070002 55%, #160006 100%);
    color: var(--text);
    font-family: system-ui, -apple-system, sans-serif;
    padding: 20px 16px 40px;
  }
  .wrap { max-width: 540px; margin: 0 auto; }
  .header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 18px;
  }
  .badge {
    font-size: 11px; letter-spacing: 2px; font-weight: 800;
    color: var(--crimson-hi); padding: 5px 12px; border-radius: 99px;
    border: 1px solid var(--border); background: rgba(255, 23, 68, 0.14);
  }
  h1 {
    margin: 8px 0 6px; font-size: 24px; letter-spacing: 1px;
    text-shadow: 0 0 18px rgba(255, 23, 68, 0.55);
  }
  .sub { color: var(--muted); font-size: 13px; margin: 0 0 18px; }
  .drop {
    background: linear-gradient(145deg, rgba(40,2,12,0.92), rgba(14,0,5,0.95));
    border: 1.5px dashed var(--red);
    border-radius: 18px;
    padding: 28px 18px;
    text-align: center;
    box-shadow: 0 0 32px rgba(255, 23, 68, 0.18);
  }
  .btn {
    display: inline-block;
    background: linear-gradient(90deg, #ff0a37, #ff335c);
    color: #140004;
    font-weight: 900;
    letter-spacing: 1.4px;
    font-size: 14px;
    padding: 14px 26px;
    border-radius: 99px;
    border: none;
    cursor: pointer;
    box-shadow: 0 0 20px rgba(255, 23, 68, 0.55);
  }
  input[type=file] { display: none; }
  .hint { color: var(--muted); font-size: 12px; margin-top: 12px; }
  .status {
    margin-top: 16px; padding: 12px 14px; border-radius: 12px;
    background: rgba(255, 23, 68, 0.12); border: 1px solid var(--border);
    font-size: 13px; display: none;
  }
  .bar-bg {
    margin-top: 8px; height: 6px; border-radius: 99px;
    background: #2e030d; overflow: hidden;
  }
  .bar {
    height: 100%; width: 0%; background: linear-gradient(90deg, #ff1744, #ff8099);
    transition: width 0.12s linear;
  }
  .card {
    margin-top: 18px; background: rgba(21, 1, 6, 0.88);
    border: 1px solid var(--border); border-radius: 16px; padding: 14px;
  }
  .card-title {
    font-size: 11px; letter-spacing: 1.8px; color: var(--crimson-hi);
    font-weight: 800; margin-bottom: 10px;
    display: flex; justify-content: space-between;
  }
  .row {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 10px; border-radius: 10px;
    background: rgba(42, 3, 12, 0.65); margin-bottom: 8px;
    border: 1px solid rgba(255, 31, 72, 0.22);
  }
  .ext {
    font-size: 10px; font-weight: 800; padding: 3px 7px; border-radius: 6px;
    background: rgba(255, 23, 68, 0.25); color: #ffb3c1;
    border: 1px solid rgba(255, 46, 85, 0.45);
  }
  .name { flex: 1; font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sz { font-size: 11px; color: var(--muted); font-family: monospace; }
  .del {
    background: rgba(255, 23, 68, 0.18); color: #ff8099;
    border: 1px solid rgba(255, 31, 72, 0.4); border-radius: 8px;
    padding: 4px 8px; font-size: 11px; font-weight: 700; cursor: pointer;
  }
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <span class="badge">⛧ BLOOD CHAOS LIBRARY</span>
    <span class="badge" id="countBadge">0 TRACKS</span>
  </div>
  <h1>UPLOAD MP4 &amp; MP3</h1>
  <p class="sub">Pick songs or videos from your phone. They appear in your Neon Chaos Music Player immediately.</p>

  <div class="drop">
    <label class="btn" for="picker">＋ SELECT MP4 / MP3 FILES</label>
    <input id="picker" type="file" multiple accept=".mp3,.mp4,.m4a,.wav,.flac,.ogg,.opus,.webm,.mkv,audio/*,video/*">
    <div class="hint">Supports MP4, MP3, M4A, WAV, FLAC, OGG, WEBM</div>
    <div class="status" id="statusBox">
      <div id="statusText">Uploading...</div>
      <div class="bar-bg"><div class="bar" id="bar"></div></div>
    </div>
  </div>

  <div class="card">
    <div class="card-title"><span>IN YOUR CHAOS LIBRARY</span><span>TAP ✕ TO REMOVE</span></div>
    <div id="list"></div>
  </div>
</div>

<script>
const picker = document.getElementById('picker');
const statusBox = document.getElementById('statusBox');
const statusText = document.getElementById('statusText');
const bar = document.getElementById('bar');
const listEl = document.getElementById('list');
const countBadge = document.getElementById('countBadge');

async function loadList() {
  const res = await fetch('/api/list');
  const data = await res.json();
  countBadge.textContent = data.length + (data.length === 1 ? ' TRACK' : ' TRACKS');
  if (!data.length) {
    listEl.innerHTML = '<div style="color:#b86c7a;font-size:12px;padding:6px 2px;">No uploaded tracks yet — tap SELECT MP4 / MP3 FILES above.</div>';
    return;
  }
  listEl.innerHTML = data.map(t => `
    <div class="row">
      <span class="ext">${t.ext}</span>
      <span class="name">${t.name.replace(/</g,'&lt;')}</span>
      <span class="sz">${t.sizeMb} MB</span>
      <button class="del" onclick="delTrack(${JSON.stringify(t.file)})">✕</button>
    </div>
  `).join('');
}

async function delTrack(file) {
  await fetch('/api/delete?file=' + encodeURIComponent(file), { method: 'POST' });
  loadList();
}

function uploadOne(file, idx, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/upload?name=' + encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        bar.style.width = pct + '%';
        statusText.textContent = `Uploading (${idx+1}/${total}): ${file.name} — ${pct}%`;
      }
    };
    xhr.onload = () => xhr.status === 200 ? resolve() : reject(new Error(xhr.responseText));
    xhr.onerror = () => reject(new Error('Network error'));
    xhr.send(file);
  });
}

picker.addEventListener('change', async () => {
  const files = Array.from(picker.files || []);
  if (!files.length) return;
  statusBox.style.display = 'block';
  try {
    for (let i = 0; i < files.length; i++) {
      bar.style.width = '0%';
      await uploadOne(files[i], i, files.length);
    }
    bar.style.width = '100%';
    statusText.textContent = `✓ Added ${files.length} file${files.length > 1 ? 's' : ''} to Chaos Library! Return home to play.`;
    picker.value = '';
    loadList();
  } catch (err) {
    statusText.textContent = 'Upload failed: ' + err.message;
  }
});

loadList();
</script>
</body>
</html>
"""


class UploadHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/list":
            tracks = [t for t in scan_library() if t["uploaded"]]
            self._send_json(tracks)
            return
        body = UPLOAD_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        ensure_lib()

        if parsed.path == "/api/delete":
            fname = os.path.basename(qs.get("file", [""])[0])
            target = LIB_DIR / fname
            if fname and target.exists() and target.is_file():
                try:
                    target.unlink()
                except OSError:
                    pass
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/upload":
            raw_name = qs.get("name", ["track.mp3"])[0]
            safe_name = os.path.basename(raw_name).replace("\x00", "").strip()
            if not safe_name:
                safe_name = f"track_{int(time.time())}.mp3"
            ext = Path(safe_name).suffix.lower()
            if ext not in MEDIA_EXTS:
                safe_name += ".mp4"
            dest = LIB_DIR / safe_name
            length = int(self.headers.get("Content-Length", "0"))
            remaining = length
            with open(dest, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(65536, remaining))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            trigger_gdrive_sync(str(dest))
            self._send_json({"ok": True, "file": safe_name, "gdrive": gdrive_connected()})
            return

        self._send_json({"ok": False}, code=404)


def serve_upload():
    ensure_lib()
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer(("127.0.0.1", UPLOAD_PORT), UploadHandler)
    server.serve_forever()


def cmd_status():
    ensure_lib()
    if not upload_server_running():
        start_upload_server()
    tracks = scan_library()
    st = load_state()
    alive = mpv_alive()
    active = False
    playing = False
    paused = False
    cur_path = ""
    title = ""
    ext = ""
    pos = 0.0
    dur = 0.0

    if alive:
        p = mpv_cmd(["get_property", "path"])
        if p:
            active = True
            cur_path = str(p)
            paused = bool(mpv_cmd(["get_property", "pause"]))
            playing = not paused
            title = clean_title(cur_path)
            ext = Path(cur_path).suffix.lstrip(".").upper()
            pos = float(mpv_cmd(["get_property", "time-pos"]) or 0.0)
            dur = float(mpv_cmd(["get_property", "duration"]) or 0.0)
        else:
            alive = False

    mp3_count = sum(1 for t in tracks if not t["isVideo"])
    mp4_count = sum(1 for t in tracks if t["isVideo"])
    gd = load_gdrive_state()

    out = {
        "active": active,
        "playing": playing,
        "paused": paused,
        "path": cur_path,
        "title": title,
        "ext": ext,
        "pos": round(pos, 1),
        "dur": round(dur, 1),
        "loop": bool(st.get("loop", False)),
        "shuffle": bool(st.get("shuffle", False)),
        "eqBass": int(st.get("eqBass", 0)),
        "eqMid": int(st.get("eqMid", 0)),
        "eqTreble": int(st.get("eqTreble", 0)),
        "gdriveConnected": gdrive_connected(),
        "gdriveSyncing": bool(gd.get("syncing", False)),
        "gdriveLastSync": gd.get("lastSync", ""),
        "gdriveError": gd.get("error", ""),
        "gdriveAccount": GDRIVE_ACCOUNT,
        "count": len(tracks),
        "mp3Count": mp3_count,
        "mp4Count": mp4_count,
        "uploadUrl": f"http://127.0.0.1:{UPLOAD_PORT}",
        "tracks": tracks[:60],
    }
    print(json.dumps(out))


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    arg = sys.argv[2] if len(sys.argv) > 2 else ""

    if action == "status":
        cmd_status()
    elif action == "play":
        play_file(arg, video=False)
    elif action == "watch":
        play_file(arg, video=True)
    elif action == "toggle":
        if mpv_alive() and mpv_cmd(["get_property", "path"]):
            mpv_cmd(["cycle", "pause"])
        else:
            st = load_state()
            last = st.get("lastPath", "")
            if last and os.path.exists(last):
                play_file(last)
            else:
                tracks = scan_library()
                if tracks:
                    play_file(tracks[0]["path"])
    elif action == "stop":
        stop_mpv()
    elif action == "next":
        step_track(1)
    elif action == "prev":
        step_track(-1)
    elif action == "seek":
        if mpv_alive():
            try:
                mpv_cmd(["seek", float(arg or "10"), "relative"])
            except Exception:
                pass
    elif action == "loop":
        st = load_state()
        st["loop"] = not bool(st.get("loop", False))
        save_state(st)
        if mpv_alive():
            mpv_cmd(["set_property", "loop-file", "inf" if st["loop"] else "no"])
    elif action == "shuffle":
        st = load_state()
        st["shuffle"] = not bool(st.get("shuffle", False))
        save_state(st)
    elif action == "eq":
        st = load_state()
        try:
            st["eqBass"] = max(-12, min(12, int(round(float(sys.argv[2])))))
            st["eqMid"] = max(-12, min(12, int(round(float(sys.argv[3])))))
            st["eqTreble"] = max(-12, min(12, int(round(float(sys.argv[4])))))
        except Exception:
            pass
        save_state(st)
        apply_live_eq(st)
    elif action == "eq-step":
        st = load_state()
        band = sys.argv[2] if len(sys.argv) > 2 else "bass"
        try:
            delta = int(round(float(sys.argv[3]))) if len(sys.argv) > 3 else 0
        except ValueError:
            delta = 0
        key = {"bass": "eqBass", "mid": "eqMid", "treble": "eqTreble"}.get(band, "eqBass")
        st[key] = max(-12, min(12, int(st.get(key, 0)) + delta))
        save_state(st)
        apply_live_eq(st)
    elif action == "eq-reset":
        st = load_state()
        st["eqBass"] = 0
        st["eqMid"] = 0
        st["eqTreble"] = 0
        save_state(st)
        apply_live_eq(st)
    elif action == "eq-preset":
        vals = EQ_PRESETS.get((arg or "flat").strip().lower())
        if vals is not None:
            st = load_state()
            st["eqBass"], st["eqMid"], st["eqTreble"] = vals
            save_state(st)
            apply_live_eq(st)
    elif action == "delete":
        if arg and str(Path(arg)).startswith(str(LIB_DIR)) and os.path.exists(arg):
            cur = mpv_cmd(["get_property", "path"]) if mpv_alive() else None
            if cur == arg:
                stop_mpv()
            try:
                os.unlink(arg)
            except OSError:
                pass
    elif action == "upload-start":
        start_upload_server()
    elif action == "serve-upload":
        serve_upload()
    elif action == "gdrive-auth":
        run_gdrive_auth()
    elif action == "gdrive-sync":
        trigger_gdrive_sync(arg)
    elif action == "gdrive-sync-worker":
        run_gdrive_sync_worker(arg)


if __name__ == "__main__":
    main()
