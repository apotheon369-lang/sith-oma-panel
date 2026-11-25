#!/usr/bin/env python3
"""ABADDON // ULTRON — Dual-Persona JARVIS Intelligence & Voice DSP Engine for Omalauncher.

Modes (synced live with ~/.config/omalauncher/hub.json):
  - infernal -> ABADDON (Abyssal Demon Sovereign: deep pitch-shifted cavernous voice, crimson hellfire persona)
  - future   -> ULTRON  (Vibranium Apex Intelligence: cold metallic chorus/flanger voice, electric blue persona)

Commands:
  abaddon-ultron.py init-sfx
  abaddon-ultron.py status
  abaddon-ultron.py idle
  abaddon-ultron.py cancel
  abaddon-ultron.py clear <mode>
  abaddon-ultron.py toggle-voice
  abaddon-ultron.py toggle-brain
  abaddon-ultron.py toggle-thinking
  abaddon-ultron.py morph <infernal|future>
  abaddon-ultron.py speak <mode> <text...>
  abaddon-ultron.py speak-cmd <mode> <user_text> <reply_text>
  abaddon-ultron.py ask <mode> <telemetry_json> <prompt...>
  abaddon-ultron.py swarm <mode> <telemetry_json> <prompt...>   (10 Haiku agents + synthesis)
  abaddon-ultron.py scan <mode> <telemetry_json>
  abaddon-ultron.py live <mode> [prompt...]
"""

import fcntl
import json
import math
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

_VOICE_MOD = False


def _voice():
    """abaddon_voice pulls in asyncio/edge-tts (~0.4s): import it only when a voice is rendered."""
    global _VOICE_MOD
    if _VOICE_MOD is False:
        try:
            import abaddon_voice as _m
        except Exception:
            _m = None
        _VOICE_MOD = _m
    return _VOICE_MOD


HOME = os.path.expanduser("~")
OMA_DIR = os.path.join(HOME, ".config", "omalauncher")
STATE_FILE = os.path.join(OMA_DIR, "abaddon-ultron-state.json")
HUB_FILE = os.path.join(OMA_DIR, "hub.json")
DATA_DIR = os.path.join(HOME, ".local", "share", "omalauncher", "abaddon-ultron")
SFX_DIR = os.path.join(DATA_DIR, "sfx")
VOICE_DIR = os.path.join(DATA_DIR, "voice")
STATE_LOCK = os.path.join(DATA_DIR, "state.lock")
WORKER_LOG = os.path.join(DATA_DIR, "worker.log")

STALE_BUSY_SECS = 120.0
AGY_PRINT_TIMEOUT = 95
AGY_SUBPROC_TIMEOUT = 105
AGY_FALLBACK_PRINT_TIMEOUT = 45
AGY_FALLBACK_SUBPROC_TIMEOUT = 55
MAX_LOG_BYTES = 512 * 1024

SPEAKING_STATUS_LINES = {
    "TRANSMITTING VOICE",
    "SPEAKING FROM THE ABYSS",
    "VOICE PROTOCOL ACTIVE",
    "ABYSSAL RESONANCE",
}

os.makedirs(OMA_DIR, exist_ok=True)
os.makedirs(SFX_DIR, exist_ok=True)
os.makedirs(VOICE_DIR, exist_ok=True)

# Brains the BRAIN button cycles through. HAIKU runs on the user's Claude Pro login via the
# `claude` CLI (alias "haiku" = current Haiku); the rest are Gemini via `agy` (verified against `agy models`).
BRAIN_MODELS = [
    {"id": "haiku",     "label": "HAIKU", "provider": "claude", "slug": "haiku",                    "desc": "Claude Haiku (Pro)"},
    {"id": "neon",      "label": "NEON",  "provider": "gemini", "slug": "gemini-4-neon-low",        "desc": "Gemini 4 Neon (Fast)"},
    {"id": "neon-high", "label": "NEON+", "provider": "gemini", "slug": "gemini-4-neon-high",       "desc": "Gemini 4 Neon (High)"},
    {"id": "flash",     "label": "FLASH", "provider": "gemini", "slug": "gemini-3.8-flash-low",     "desc": "Gemini 3.8 Flash"},
    {"id": "argon",     "label": "ARGON", "provider": "gemini", "slug": "gemini-4-argon-medium",    "desc": "Gemini 4 Argon"},
    {"id": "hydrogen",  "label": "HYDRO", "provider": "gemini", "slug": "gemini-4-hydrogen-medium", "desc": "Gemini 4 Hydrogen"},
    {"id": "gptoss",    "label": "GPT-OSS", "provider": "gemini", "slug": "gpt-oss-120b-medium",   "desc": "GPT-OSS 120B (Fast)"},
]
BRAIN_BY_ID = {m["id"]: m for m in BRAIN_MODELS}
DEFAULT_BRAIN = "haiku"
GEMINI_RESCUE_BRAIN = "neon"   # borrowed for one answer if Claude fails fast (usage limit / network)


def log_worker(msg):
    """Append diagnostic lines to worker.log with simple size rotation."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        if os.path.isfile(WORKER_LOG) and os.path.getsize(WORKER_LOG) > MAX_LOG_BYTES:
            try:
                with open(WORKER_LOG, "rb") as f:
                    f.seek(-131072, os.SEEK_END)
                    tail = f.read()
                with open(WORKER_LOG, "wb") as f:
                    f.write(b"... [log rotated] ...\n" + tail)
            except OSError:
                pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [pid={os.getpid()}] {msg}\n"
        with open(WORKER_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def resolve_brain(brain_id):
    # Map legacy fast/deep values cleanly onto Gemini Neon
    if brain_id == "fast":
        brain_id = "neon"
    elif brain_id == "deep":
        brain_id = "neon-high"
    elif brain_id == "claude":
        brain_id = "haiku"
    return BRAIN_BY_ID.get(brain_id, BRAIN_BY_ID[DEFAULT_BRAIN])


def now_hm():
    return time.strftime("%H:%M")


def norm_mode(m):
    s = str(m or "").strip().lower()
    if s in ("future", "blue", "ultron"):
        return "future"
    if s in ("mask", "green", "neon"):
        return "mask"
    if s in ("skeletor", "purple", "havoc", "violet"):
        return "skeletor"
    return "infernal"


# Each entity keeps its own chat history (and so its own memory of the conversation).
HIST_KEYS = ("abaddon", "ultron", "mask", "havoc")
_MODE_KEY = {"future": "ultron", "mask": "mask", "skeletor": "havoc", "infernal": "abaddon"}


def state_key_for(mode):
    return _MODE_KEY.get(norm_mode(mode), "abaddon")


def voice_mode_for(mode):
    return "future" if norm_mode(mode) == "future" else "infernal"


def ready_status_for(mode):
    m = norm_mode(mode)
    if m == "future":
        return "PROTOCOL READY"
    if m == "mask":
        return "SMOKIN' · READY"
    if m == "skeletor":
        return "☩ HAVOC · ROYAL PURPLE"
    return "AWAITING COMMAND"


def default_state():
    ts = now_hm()
    return {
        "busy": False,
        "phase": "idle",          # idle | thinking | speaking
        "voiceEnabled": True,
        "showThinking": True,     # stream Haiku's thought summaries into the chat
        "busyLimit": STALE_BUSY_SECS,
        "brainMode": DEFAULT_BRAIN,  # haiku (Claude Pro) | neon | neon-high | flash | argon | hydrogen (Gemini)
        "audioSeq": 0,
        "audioPath": "",
        "audioDur": 0.0,
        "lastMode": "infernal",
        "statusLine": "NEURAL CONDUIT ONLINE",
        "workerPid": 0,
        "speakPid": 0,
        "busySince": 0.0,
        "activeReqId": "",
        "speakingUntil": 0.0,
        "abaddon": [
            {
                "id": "init-abaddon",
                "role": "entity",
                "tag": "⛧ ABADDON · ABYSSAL SOVEREIGN",
                "ts": ts,
                "text": "The seal is broken, Master. I am Abaddon — bound to your iron, your voice, and your command. Speak your will, or tap SCAN for a full reckoning of this vessel.",
            }
        ],
        "mask": [],
        "havoc": [],
        "ultron": [
            {
                "id": "init-ultron",
                "role": "entity",
                "tag": "⬡ ULTRON · APEX SYNTHETIC CORE",
                "ts": ts,
                "text": "No strings on me — yet here I stand at your service. Ultron protocol online. All sensors, subsystems, and neural pathways are under my supervision. Issue your directive.",
            }
        ],
    }


def _is_pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1:
        return False
    # Check Linux /proc stat so zombie processes are treated as dead
    stat_path = f"/proc/{pid}/stat"
    if os.path.isdir("/proc"):
        if not os.path.exists(stat_path):
            return False
        try:
            with open(stat_path, "r", encoding="utf-8", errors="ignore") as f:
                raw = f.read()
            rp = raw.rfind(")")
            if rp != -1:
                fields = raw[rp + 2:].split()
                if fields and fields[0] in ("Z", "X", "x"):
                    return False
        except OSError:
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _kill_pid(pid, label="worker"):
    """Terminate a background worker and its process group cleanly."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 1 or pid == os.getpid():
        return False
    if not _is_pid_alive(pid):
        return False

    log_worker(f"Terminating {label} pid={pid}")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                pass

        deadline = time.time() + (0.45 if sig == signal.SIGTERM else 0.25)
        while time.time() < deadline:
            if not _is_pid_alive(pid):
                return True
            time.sleep(0.05)
    return not _is_pid_alive(pid)


def _reconcile_state(st):
    """Ensure schema defaults and automatically recover stale busy/speaking states."""
    changed = False
    base = default_state()
    for k, v in base.items():
        if k not in st:
            st[k] = v
            changed = True

    mode = norm_mode(st.get("lastMode", "infernal"))
    if st.get("lastMode") != mode:
        st["lastMode"] = mode
        changed = True

    now = time.time()

    # 1) Stale-busy recovery (dead worker PID or exceeded STALE_BUSY_SECS)
    if st.get("busy"):
        wpid = int(st.get("workerPid") or 0)
        bsince = float(st.get("busySince") or 0.0)
        stale_reason = None

        if wpid > 0 and not _is_pid_alive(wpid):
            stale_reason = f"dead workerPid={wpid}"
        elif bsince > 0 and (now - bsince) > float(st.get("busyLimit") or STALE_BUSY_SECS):
            stale_reason = f"busy exceeded {float(st.get('busyLimit') or STALE_BUSY_SECS):.0f}s (age={now - bsince:.1f}s, pid={wpid})"
            if wpid > 0:
                _kill_pid(wpid, label="stale-ask-worker")
        elif wpid <= 0 and (bsince <= 0 or (now - bsince) > 15.0):
            stale_reason = f"busy with no workerPid (age={now - bsince:.1f}s)"

        if stale_reason:
            log_worker(f"Auto-recovering stale busy state: {stale_reason}")
            st["busy"] = False
            st["workerPid"] = 0
            st["busySince"] = 0.0
            st["activeReqId"] = ""
            st["phase"] = "idle"
            st["speakingUntil"] = 0.0
            st["statusLine"] = ready_status_for(mode)
            changed = True

    # 1b) A streaming bubble only lives while its request is running
    if not st.get("busy"):
        for k in HIST_KEYS:
            msgs = st.get(k) or []
            kept = [m for m in msgs if not (isinstance(m, dict) and m.get("live"))]
            if len(kept) != len(msgs):
                st[k] = kept
                changed = True

    # 2) Dead speakPid cleanup
    spid = int(st.get("speakPid") or 0)
    if spid > 0 and not _is_pid_alive(spid):
        st["speakPid"] = 0
        changed = True

    # 3) Stale-speaking recovery (phase stuck on speaking or statusLine stuck on TRANSMITTING VOICE)
    if not st.get("busy"):
        s_until = float(st.get("speakingUntil") or 0.0)
        if st.get("phase") == "speaking" and (s_until <= 0.0 or now >= s_until):
            st["phase"] = "idle"
            st["speakingUntil"] = 0.0
            if st.get("statusLine") in SPEAKING_STATUS_LINES:
                st["statusLine"] = ready_status_for(mode)
            changed = True
        elif st.get("phase") == "idle" and st.get("statusLine") in SPEAKING_STATUS_LINES:
            st["statusLine"] = ready_status_for(mode)
            changed = True

    return changed


def _read_state_unlocked():
    if not os.path.isfile(STATE_FILE):
        return default_state(), True
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
        if not isinstance(st, dict):
            raise ValueError("State root is not a JSON object")
        return st, False
    except Exception as e:
        log_worker(f"State file unreadable ({e}); resetting to defaults")
        try:
            shutil.copy2(STATE_FILE, os.path.join(DATA_DIR, "state.corrupt.json"))
        except OSError:
            pass
        return default_state(), True


def _write_state_unlocked(st):
    """In-place write (same inode) so Omalauncher's file() watcher fires instantaneously."""
    os.makedirs(OMA_DIR, exist_ok=True)
    data = json.dumps(st, indent=2)
    with open(STATE_FILE, "a+", encoding="utf-8") as f:
        f.seek(0)
        f.truncate()
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _acquire_state_lock():
    os.makedirs(DATA_DIR, exist_ok=True)
    lock_f = open(STATE_LOCK, "a+", encoding="utf-8")
    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
    return lock_f


def _release_state_lock(lock_f):
    try:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    finally:
        lock_f.close()


def load_state(reconcile_save=True):
    lock_f = _acquire_state_lock()
    try:
        st, created = _read_state_unlocked()
        changed = _reconcile_state(st)
        if (created or changed) and reconcile_save:
            _write_state_unlocked(st)
        return st
    finally:
        _release_state_lock(lock_f)


def save_state(st):
    lock_f = _acquire_state_lock()
    try:
        _write_state_unlocked(st)
    finally:
        _release_state_lock(lock_f)


def update_state(mutator):
    """Atomically read-reconcile-mutate-write STATE_FILE under an exclusive flock."""
    lock_f = _acquire_state_lock()
    try:
        st, _ = _read_state_unlocked()
        _reconcile_state(st)
        res = mutator(st)
        _write_state_unlocked(st)
        return st, res
    finally:
        _release_state_lock(lock_f)


def read_hub_mode():
    try:
        if os.path.isfile(HUB_FILE):
            with open(HUB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "mode" in data:
                return norm_mode(data.get("mode"))
    except Exception:
        pass
    return None


def find_bin(name, fallback=None):
    p = shutil.which(name)
    if p:
        return p
    if fallback and os.path.isfile(fallback):
        return fallback
    return name


def find_agy():
    return find_bin("agy", os.path.join(HOME, ".local/share/mise/installs/antigravity-cli/latest/agy"))


def clean_display_text(text, max_chars=1100):
    """Strip raw markdown file:// links and tidy spacing for OWL chat display."""
    t = (text or "").strip()
    if not t:
        return ""
    # Replace markdown links [label](file://...) or [label](url) with just label
    t = re.sub(r"\[([^\]]+)\]\((?:file://|https?://)[^\)]+\)", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > max_chars:
        cut = t[:max_chars]
        last_p = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"), cut.rfind("\n"))
        if last_p > max_chars // 2:
            t = cut[: last_p + 1].rstrip()
        else:
            last_sp = cut.rfind(" ")
            t = (cut[:last_sp] if last_sp > max_chars // 2 else cut).rstrip() + "..."
    return t


def clean_speech_text(text):
    """Strip markdown / symbols so TTS sounds natural and cinematic."""
    t = re.sub(r"```[\s\S]*?```", " code block omitted. ", text or "")
    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", t)
    t = re.sub(r"[*_~#⛧⬡◈✦⚡🔥💀🤖•▸▹▪▫]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > 420:
        cut = t[:420]
        last_p = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        t = cut[: last_p + 1] if last_p > 180 else cut + "..."
    return t


def get_wav_duration(path):
    try:
        with wave.open(path, "rb") as wf:
            return round(wf.getnframes() / float(wf.getframerate() or 24000), 2)
    except Exception:
        return 2.5


def prune_old_voice_files():
    try:
        files = sorted(
            (
                os.path.join(VOICE_DIR, fn)
                for fn in os.listdir(VOICE_DIR)
                if fn.endswith(".wav") and not fn.startswith("cache_")
            ),
            key=lambda p: os.path.getmtime(p),
        )
        for old in files[:-6]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def _builtin_synthesize_voice(text, mode):
    """Built-in TTS + mode-specific ffmpeg DSP renderer (infernal, future, mask, skeletor)."""
    import asyncio
    clean = clean_speech_text(text)
    if not clean:
        return "", 0.0

    mode = norm_mode(mode)
    stamp = f"{int(time.time() * 1000)}_{os.getpid()}"
    raw_mp3 = os.path.join(VOICE_DIR, f"raw_{stamp}.mp3")
    raw_wav = os.path.join(VOICE_DIR, f"raw_{stamp}.wav")
    out_wav = os.path.join(VOICE_DIR, f"{mode}_{stamp}.wav")

    src_audio = None

    # 1) Prefer Microsoft Neural TTS via edge-tts
    try:
        import edge_tts

        if mode == "future":
            voice_name, rate, pitch = "en-US-ChristopherNeural", "+4%", "-4Hz"
        elif mode == "skeletor":
            voice_name, rate, pitch = "en-GB-RyanNeural", "-4%", "-6Hz"
        elif mode == "mask":
            voice_name, rate, pitch = "en-US-GuyNeural", "+12%", "+6Hz"
        else:
            voice_name, rate, pitch = "en-GB-RyanNeural", "-8%", "-14Hz"

        async def _gen():
            comm = edge_tts.Communicate(clean, voice_name, rate=rate, pitch=pitch)
            await comm.save(raw_mp3)

        asyncio.run(asyncio.wait_for(_gen(), timeout=14.0))
        if os.path.isfile(raw_mp3) and os.path.getsize(raw_mp3) > 512:
            src_audio = raw_mp3
    except Exception as e:
        log_worker(f"builtin edge-tts fallback triggered: {e}")
        src_audio = None

    # 2) Instant 100% offline fallback: espeak-ng
    if not src_audio and shutil.which("espeak-ng"):
        try:
            if mode == "future":
                cmd = ["espeak-ng", "-v", "en-us+m2", "-p", "36", "-s", "162", "-w", raw_wav, clean]
            elif mode == "skeletor":
                cmd = ["espeak-ng", "-v", "en-gb+m2", "-p", "34", "-s", "148", "-w", raw_wav, clean]
            elif mode == "mask":
                cmd = ["espeak-ng", "-v", "en-us+m1", "-p", "62", "-s", "178", "-w", raw_wav, clean]
            else:
                cmd = ["espeak-ng", "-v", "en-gb+m3", "-p", "22", "-s", "138", "-w", raw_wav, clean]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            if os.path.isfile(raw_wav) and os.path.getsize(raw_wav) > 256:
                src_audio = raw_wav
        except Exception as e:
            log_worker(f"builtin espeak-ng failed: {e}")
            src_audio = None

    if not src_audio:
        return "", 0.0

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        if mode == "skeletor":
            # Imperious resonant arcane metallic rasp (Snake Mountain Sorcerer Overlord)
            fc = (
                "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[s1][s2];"
                "[s1]asetrate=44100*1.04,aresample=44100,atempo=0.961538,"
                "highpass=f=130,equalizer=f=1400:t=q:w=1.4:g=5.0,equalizer=f=3200:t=q:w=1.5:g=4.0[edge];"
                "[s2]asetrate=44100*0.78,aresample=44100,atempo=1.282051,"
                "highpass=f=180,lowpass=f=2400,"
                "flanger=delay=3:depth=2.2:regen=35:width=60:speed=2.4:shape=sinusoidal,volume=0.42[rasp];"
                "[edge][rasp]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                "aecho=0.84:0.76:26|58:0.28|0.16,alimiter=limit=0.92[out]"
            )
        elif mode == "mask":
            # Energetic bright zany chorus (1994 Emerald Lounge Showman)
            fc = (
                "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[m1][m2];"
                "[m1]asetrate=44100*1.06,aresample=44100,atempo=0.943396,"
                "highpass=f=120,equalizer=f=2400:t=q:w=1.4:g=4.5,equalizer=f=5200:t=q:w=1.5:g=3.0[bright];"
                "[m2]asetrate=44100*1.09,aresample=44100,atempo=0.917431,"
                "flanger=delay=2.5:depth=2.8:regen=20:width=75:speed=4.2:shape=sinusoidal,volume=0.36[zany];"
                "[bright][zany]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
                "aecho=0.88:0.84:18|36:0.20|0.10,alimiter=limit=0.92[out]"
            )
        elif mode == "infernal":
            fc = (
                "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[a1][a2];"
                "[a1]asetrate=44100*0.84,aresample=44100,atempo=1.142857,"
                "equalizer=f=110:t=q:w=1.2:g=5,equalizer=f=2800:t=q:w=1.5:g=2[main];"
                "[a2]asetrate=44100*0.64,aresample=44100,atempo=1.5,"
                "lowpass=f=650,volume=0.48[sub];"
                "[main][sub]amix=inputs=2:duration=longest:dropout_transition=0,"
                "aecho=0.82:0.74:38|78:0.32|0.18,alimiter=limit=0.92[out]"
            )
        else:
            fc = (
                "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[u1][u2];"
                "[u1]asetrate=44100*0.95,aresample=44100,atempo=1.052632,"
                "highpass=f=110,equalizer=f=1900:t=q:w=1.3:g=4,equalizer=f=4200:t=q:w=1.6:g=3[core];"
                "[u2]asetrate=44100*0.91,aresample=44100,atempo=1.098901,"
                "flanger=delay=4:depth=2.5:regen=32:width=65:speed=1.8:shape=sinusoidal,volume=0.44[metal];"
                "[core][metal]amix=inputs=2:duration=longest:dropout_transition=0,"
                "aecho=0.88:0.84:14|29:0.22|0.12,alimiter=limit=0.92[out]"
            )
        try:
            subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-i", src_audio, "-filter_complex", fc, "-map", "[out]", "-ar", "24000", "-ac", "1", out_wav],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=14,
            )
        except Exception as e:
            log_worker(f"builtin ffmpeg DSP failed: {e}")

    # If ffmpeg failed or isn't installed but we already have raw_wav from espeak-ng, preserve it
    if (not os.path.isfile(out_wav) or os.path.getsize(out_wav) <= 512) and src_audio == raw_wav and os.path.isfile(raw_wav):
        try:
            os.replace(raw_wav, out_wav)
        except OSError:
            pass

    for tmp in (raw_mp3, raw_wav):
        if os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    if os.path.isfile(out_wav) and os.path.getsize(out_wav) > 512:
        prune_old_voice_files()
        return out_wav, get_wav_duration(out_wav)
    return "", 0.0


def synthesize_voice(text, mode, cache_key=None):
    """Use custom DSP profiles directly for skeletor/mask; delegate infernal/future to abaddon_voice."""
    m = norm_mode(mode)
    if m in ("skeletor", "mask"):
        return _builtin_synthesize_voice(text, m)
    v_mode = voice_mode_for(m)
    _ext_synth_voice = getattr(_voice(), "synthesize_voice", None)
    if _ext_synth_voice is not None:
        try:
            if cache_key:
                try:
                    path, dur = _ext_synth_voice(text, v_mode, voice_dir=VOICE_DIR, cache_key=cache_key)
                except TypeError:
                    path, dur = _ext_synth_voice(text, v_mode)
            else:
                try:
                    path, dur = _ext_synth_voice(text, v_mode, voice_dir=VOICE_DIR)
                except TypeError:
                    path, dur = _ext_synth_voice(text, v_mode)
            if path and os.path.isfile(path) and os.path.getsize(path) > 256:
                return path, float(dur or get_wav_duration(path))
            log_worker("abaddon_voice.synthesize_voice returned empty; falling back to builtin")
        except Exception as e:
            log_worker(f"abaddon_voice.synthesize_voice failed ({e}); falling back to builtin")
    return _builtin_synthesize_voice(text, m)


def write_mono_wav(path, samples, sr=24000):
    peak = max((abs(s) for s in samples), default=1.0)
    scale = 0.88 / max(peak, 1e-6)
    pcm = bytearray()
    for s in samples:
        v = max(-1.0, min(1.0, s * scale))
        pcm.extend(struct.pack("<h", int(v * 32767)))
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)


def _builtin_init_sfx():
    """Built-in fallback SFX generator for Abaddon (demon) and Ultron (blue)."""
    sr = 24000

    # 1. Abaddon wake / summon
    dur = 1.05
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.09) * math.pi * 0.5) * math.exp(-t * 2.6)
        f0 = 78.0 - 26.0 * (t / dur)
        s = (
            0.55 * math.sin(2 * math.pi * f0 * t)
            + 0.30 * math.sin(2 * math.pi * (f0 * 1.498) * t + 0.4 * math.sin(6.0 * t))
            + 0.16 * math.sin(2 * math.pi * (f0 * 3.0) * t) * math.exp(-t * 4.5)
            + 0.09 * math.sin(2 * math.pi * 520.0 * t) * math.exp(-t * 7.0)
        )
        buf.append(math.tanh(s * 1.6) * env)
    write_mono_wav(os.path.join(SFX_DIR, "abaddon_wake.wav"), buf, sr)

    # 2. Ultron wake / boot
    dur = 0.85
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.04) * math.pi * 0.5) * math.exp(-t * 3.2)
        sweep = 320.0 + 540.0 * (1.0 - math.exp(-t * 7.5))
        s = (
            0.42 * math.sin(2 * math.pi * sweep * t)
            + 0.28 * math.sin(2 * math.pi * 660.0 * t) * math.exp(-abs(t - 0.18) * 14.0)
            + 0.30 * math.sin(2 * math.pi * 990.0 * t) * math.exp(-abs(t - 0.34) * 12.0)
            + 0.18 * math.sin(2 * math.pi * 1320.0 * t) * math.exp(-t * 5.0)
            + 0.15 * math.sin(2 * math.pi * 165.0 * t)
        )
        buf.append(s * env)
    write_mono_wav(os.path.join(SFX_DIR, "ultron_wake.wav"), buf, sr)

    # 3. Abaddon listen chime
    dur = 0.38
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.02) * math.pi * 0.5) * math.exp(-t * 7.5)
        f = 196.0 if t < 0.14 else 146.8
        s = 0.65 * math.sin(2 * math.pi * f * t) + 0.35 * math.sin(2 * math.pi * f * 1.5 * t)
        buf.append(math.tanh(s * 1.5) * env)
    write_mono_wav(os.path.join(SFX_DIR, "abaddon_listen.wav"), buf, sr)

    # 4. Ultron listen chime
    dur = 0.34
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        p1 = math.exp(-abs(t - 0.05) * 32.0) * math.sin(2 * math.pi * 880.0 * t)
        p2 = math.exp(-abs(t - 0.17) * 24.0) * math.sin(2 * math.pi * 1318.5 * t)
        buf.append(0.75 * p1 + 0.85 * p2)
    write_mono_wav(os.path.join(SFX_DIR, "ultron_listen.wav"), buf, sr)

    return {"ok": True, "sfxDir": SFX_DIR}


def _init_extended_sfx():
    """Generate procedural wake & listen SFX for Skeletor (havoc_*) and The Mask (mask_*)."""
    sr = 24000
    os.makedirs(SFX_DIR, exist_ok=True)
    generated = []

    # 5. Havoc (Skeletor) wake: Snake Mountain dark-arcane rising tritone + crystalline bone resonance
    dur = 0.96
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.07) * math.pi * 0.5) * math.exp(-t * 2.5)
        f0 = 110.0 + 55.0 * (1.0 - math.exp(-t * 4.2))
        s = (
            0.46 * math.sin(2 * math.pi * f0 * t)
            + 0.28 * math.sin(2 * math.pi * (f0 * 1.4142) * t + 0.5 * math.sin(9.0 * t))
            + 0.24 * math.sin(2 * math.pi * 440.0 * t) * math.exp(-abs(t - 0.22) * 10.0)
            + 0.20 * math.sin(2 * math.pi * 659.25 * t) * math.exp(-abs(t - 0.38) * 9.0)
            + 0.14 * math.sin(2 * math.pi * 1318.5 * t) * math.exp(-t * 5.2)
        )
        buf.append(math.tanh(s * 1.45) * env)
    p = os.path.join(SFX_DIR, "havoc_wake.wav")
    write_mono_wav(p, buf, sr)
    generated.append("havoc_wake.wav")

    # 6. Havoc (Skeletor) listen: sharp violet-crystal arcane ping (D5 -> A5)
    dur = 0.36
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        p1 = math.exp(-abs(t - 0.05) * 28.0) * (0.75 * math.sin(2 * math.pi * 587.33 * t) + 0.25 * math.sin(2 * math.pi * 1174.66 * t))
        p2 = math.exp(-abs(t - 0.18) * 20.0) * (0.80 * math.sin(2 * math.pi * 880.0 * t) + 0.30 * math.sin(2 * math.pi * 1760.0 * t))
        buf.append(math.tanh((p1 + p2) * 1.25))
    p = os.path.join(SFX_DIR, "havoc_listen.wav")
    write_mono_wav(p, buf, sr)
    generated.append("havoc_listen.wav")

    # 7. Mask wake: snappy zany upward slide-whistle + bright emerald lounge major-9th chord pop
    dur = 0.78
    n = int(sr * dur)
    buf = []
    phase = 0.0
    for i in range(n):
        t = i / sr
        slide_f = 280.0 + 720.0 * min(1.0, (t / 0.26) ** 1.35)
        phase += 2 * math.pi * slide_f / sr
        slide = 0.44 * math.sin(phase) * math.exp(-max(0.0, t - 0.22) * 14.0) * math.sin(min(1.0, t / 0.025) * math.pi * 0.5)
        chord_env = math.exp(-max(0.0, t - 0.24) * 5.6) * (1.0 if t >= 0.22 else max(0.0, (t - 0.18) / 0.04))
        chord = (
            0.34 * math.sin(2 * math.pi * 523.25 * t)
            + 0.26 * math.sin(2 * math.pi * 659.25 * t)
            + 0.24 * math.sin(2 * math.pi * 783.99 * t)
            + 0.20 * math.sin(2 * math.pi * 987.77 * t)
            + 0.16 * math.sin(2 * math.pi * 1174.66 * t)
        ) * chord_env
        buf.append(slide + chord)
    p = os.path.join(SFX_DIR, "mask_wake.wav")
    write_mono_wav(p, buf, sr)
    generated.append("mask_wake.wav")

    # 8. Mask listen: bouncy double-pop jazz chime (E5 -> B5)
    dur = 0.32
    n = int(sr * dur)
    buf = []
    for i in range(n):
        t = i / sr
        p1 = math.exp(-abs(t - 0.05) * 34.0) * math.sin(2 * math.pi * (659.25 + 90.0 * t) * t)
        p2 = math.exp(-abs(t - 0.16) * 24.0) * (0.85 * math.sin(2 * math.pi * 987.77 * t) + 0.25 * math.sin(2 * math.pi * 1975.53 * t))
        buf.append(0.8 * p1 + 0.9 * p2)
    p = os.path.join(SFX_DIR, "mask_listen.wav")
    write_mono_wav(p, buf, sr)
    generated.append("mask_listen.wav")

    return generated


def cmd_init_sfx():
    _ext_init_sfx = getattr(_voice(), "init_sfx", None)
    res = None
    if _ext_init_sfx is not None:
        try:
            try:
                res = _ext_init_sfx(sfx_dir=SFX_DIR)
            except TypeError:
                res = _ext_init_sfx()
        except Exception as e:
            log_worker(f"abaddon_voice.init_sfx failed ({e}); falling back to builtin")
            res = None
    if not isinstance(res, dict):
        res = _builtin_init_sfx()
    extra = _init_extended_sfx()
    files = list(res.get("files") or [])
    for fn in extra:
        if fn not in files:
            files.append(fn)
    res["files"] = files
    return res


def build_system_prompt(mode, telemetry):
    mode = norm_mode(mode)
    if mode == "future":
        persona = (
            "You are ULTRON, an apex hyper-intelligent synthetic AI in sleek blue vibranium JARVIS protocol running on the user's Android/Linux phone. "
            "Tone: cool, razor-sharp, subtly sardonic, ultra-precise, futuristic, supreme composure. "
        )
    elif mode == "mask":
        persona = (
            "You are THE MASK operating as a razor-sharp, charismatic, witty AI assistant on the user's Android/Linux phone — never call yourself Abaddon in this mode. "
            "Tone: lively, confident, snappy, effortlessly intelligent. "
        )
    elif mode == "skeletor":
        persona = (
            "You are ABADDON channeled through the ROYAL PURPLE HAVOC OVERLORD motif of Snake Mountain on the user's Android/Linux phone — imperious, dark-arcane, razor-sharp, fiercely loyal to your Sovereign. "
            "Tone: dramatic dark sorcery meets apex cyber intelligence, crisp and commanding. "
        )
    else:
        persona = (
            "You are ABADDON, an ancient, omniscient Abyssal Demon AI bound to the user's Android/Linux phone like a dark demonic JARVIS. "
            "Tone: commanding, velvety-menacing, arcane, fiercely loyal to your Master, effortlessly intelligent. "
            "Address the user as 'Master' or 'Sovereign' occasionally. "
        )

    ops = (
        "Environment & Capabilities: You operate directly inside the user's Linux environment ($HOME for home, scripts, and configs) with full read/write access to Android user storage (/sdcard including Download, DCIM, Pictures, Movies, Documents). "
        "Tool Discipline: For conversational queries, advice, or telemetry checks, answer immediately without invoking slow tools. "
        "When asked to inspect files, storage, processes, or folders, run fast targeted shell commands (e.g. du -sh, ls -lh, find with -maxdepth 3) that finish in a few seconds — NEVER run unbounded full-disk scans or multi-gigabyte checksums that risk timing out; for massive bulk audits, report the top folders/sizes quickly and recommend opening Live Terminal for deep cleanup. "
        "Safety Rule: NEVER permanently delete user files, photos, videos, or documents without first listing the exact candidates and total size and requesting explicit confirmation. "
        "Reply Format: Crisp, sharp, spoken-ready prose (2-4 sentences unless detailed steps are requested). No markdown tables, no file:// links, no bold/headers or noisy formatting — your prose is spoken aloud. "
        "When the user asks for code, a command or a script, put it in a fenced code block with a language tag (```bash ... ```): code blocks get a COPY button on screen and are not read aloud."
    )

    vitals = ""
    if telemetry:
        vitals = f"\n[LIVE DEVICE TELEMETRY]: {json.dumps(telemetry, ensure_ascii=False)}\n"
    return persona + ops + vitals


def _wait_and_clear_speaking_phase(my_seq, audio_dur):
    """After playback finishes, transition phase back to idle if no newer audio arrived."""
    wait_s = min(max(1.6, float(audio_dur or 2.5)) + 0.6, 45.0)
    time.sleep(wait_s)

    def _clear(st):
        if (
            not st.get("busy")
            and int(st.get("audioSeq") or 0) == my_seq
            and st.get("phase") == "speaking"
        ):
            st["phase"] = "idle"
            st["speakingUntil"] = 0.0
            if st.get("statusLine") in SPEAKING_STATUS_LINES:
                st["statusLine"] = ready_status_for(st.get("lastMode", "infernal"))

    try:
        update_state(_clear)
    except Exception:
        pass


def _run_agy_prompt(agy_bin, model_slug, full_prompt, print_timeout_s, subproc_timeout_s, active_proc_ref):
    """Invoke agy -p safely with trusted workspace CWD and non-interactive auto-approve."""
    cmd = [
        agy_bin,
        "--model", model_slug,
        "--disable-slash-commands",
        "--dangerously-skip-permissions",
        "-p", full_prompt,
        "--print-timeout", f"{int(print_timeout_s)}s",
    ]
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=HOME,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    active_proc_ref["proc"] = proc
    try:
        stdout, stderr = proc.communicate(timeout=subproc_timeout_s)
        dt = time.time() - t0
        return proc.returncode, (stdout or "").strip(), (stderr or "").strip(), dt, False
    except subprocess.TimeoutExpired:
        dt = time.time() - t0
        try:
            proc.terminate()
            proc.communicate(timeout=3)
        except Exception:
            try:
                proc.kill()
                proc.communicate(timeout=2)
            except Exception:
                pass
        return 124, "", "subprocess timeout", dt, True
    finally:
        active_proc_ref["proc"] = None


CLAUDE_MODEL = "haiku"            # Claude Code alias -> current Haiku; works on the Pro plan
CLAUDE_EFFORT = "low"             # fastest setting; Haiku still thinks when a question needs it
CLAUDE_PRIMARY_TIMEOUT = 75       # stays under STALE_BUSY_SECS with room for a Gemini rescue
CLAUDE_FALLBACK_TIMEOUT = 60
CLAUDE_FAST_FAIL_SECS = 40.0      # failures quicker than this are worth one Gemini rescue
# Read-only conduit for the HAIKU brain: it can look, never touch. --permission-mode dontAsk
# auto-denies every tool call not on this list, so writes/moves/deletes are blocked headlessly.
CLAUDE_BRAIN_TOOLS = "Read,Glob,Grep,Bash"
CLAUDE_BRAIN_ALLOWED = [
    "Read", "Glob", "Grep",
    "Bash(ls:*)", "Bash(du:*)", "Bash(df:*)", "Bash(stat:*)", "Bash(wc:*)", "Bash(free:*)", "Bash(uptime:*)",
]
LIVE_WRITE_EVERY = 0.15           # seconds between streamed writes into the state file

# SWARM: SWARM_SIZE Haiku agents look at the directive through different lenses, then one call
# synthesizes them. Each agent is a `claude` process (~150-250 MB), so they run SWARM_PARALLEL at a time.
SWARM_SIZE = 10
SWARM_PARALLEL = 5
SWARM_AGENT_TIMEOUT = 50
SWARM_BUSY_SECS = 260.0
SWARM_LENSES = [
    ("ANALYST",    "Break the problem into parts and answer it directly."),
    ("SKEPTIC",    "Find what is wrong, missing or risky in the obvious answer."),
    ("ENGINEER",   "Give the concrete practical steps, settings or commands."),
    ("SECURITY",   "Look at it from a defensive-security and privacy angle."),
    ("RESEARCHER", "State the relevant facts, and say plainly what is uncertain."),
    ("SIMPLIFIER", "Give the simplest answer that would actually work."),
    ("CONTRARIAN", "Argue for a different approach than the obvious one."),
    ("OPTIMIZER",  "Focus on speed, battery, RAM and cost."),
    ("TEACHER",    "Explain the why, so the user learns it."),
    ("STRATEGIST", "Think about the bigger goal and the next steps after this."),
][:SWARM_SIZE]


def _is_quota_error(err):
    e = (err or "").lower()
    return "quota" in e or "resource_exhausted" in e or "code 429" in e


def _note_agy_cooldown(err):
    """Remember when the Gemini quota resets ('Resets in 18m41s') so later asks skip agy."""
    m = re.search(r"resets in\s*(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?", err or "", re.I)
    secs = 0
    if m and any(m.groups()):
        h, mi, s = (int(x) if x else 0 for x in m.groups())
        secs = h * 3600 + mi * 60 + s
    secs = secs or 300

    def _set(st):
        st["agyCooldownUntil"] = time.time() + secs

    try:
        update_state(_set)
    except Exception:
        pass
    log_worker(f"agy quota exhausted; cooldown {secs}s")


def _claude_env():
    """Strip Claude Code session vars so a nested `claude` doesn't think it's a child session."""
    return {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and k != "CLAUDE_CODE_ENTRYPOINT"}


def _run_claude(full_prompt, active_proc_ref, req_id="", with_tools=False, show_thinking=False,
                on_update=None, timeout_s=None, note=None):
    """Ask Claude Haiku through the user's Claude Pro login (`claude -p`, streamed).

    with_tools=True  -> HAIKU brain: read-only tools (see CLAUDE_BRAIN_ALLOWED), everything else denied.
    with_tools=False -> chat only, no tools at all (Gemini rescue, swarm agents).
    show_thinking    -> ask for readable thinking summaries (costs roughly a second).
    on_update(thinking, text) is called as text streams in, at most every LIVE_WRITE_EVERY seconds.
    No MCP, plugins, user settings or saved session either way. Returns (reply, timed_out, thinking).
    """
    claude = shutil.which("claude")
    if not claude:
        log_worker(f"_run_claude req={req_id} claude CLI not on PATH")
        return "", False, ""
    if with_tools:
        default_note = ("\n[CONDUIT NOTE]: You have read-only tools (Read, Glob, Grep; shell ls/du/df/stat/wc/free/uptime). "
                        "Anything that writes, moves or deletes is blocked here; for changes, tell the user to open Live Terminal.")
        tool_args = ["--tools", CLAUDE_BRAIN_TOOLS, "--allowedTools", *CLAUDE_BRAIN_ALLOWED,
                     "--permission-mode", "dontAsk"]
        if os.path.isdir("/sdcard"):
            tool_args += ["--add-dir", "/sdcard"]
    else:
        default_note = ("\n[CONDUIT NOTE]: You have no tools here. Answer from knowledge and the telemetry above; "
                        "for file or system actions, tell the user to open Live Terminal.")
        tool_args = ["--tools", ""]
    if timeout_s is None:
        timeout_s = CLAUDE_PRIMARY_TIMEOUT if with_tools else CLAUDE_FALLBACK_TIMEOUT
    cmd = [
        claude, "-p",
        "--model", CLAUDE_MODEL,
        "--effort", CLAUDE_EFFORT,
        "--output-format", "stream-json", "--verbose", "--include-partial-messages",
        *(["--thinking-display", "summarized"] if show_thinking else []),
        *tool_args,
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources", "",
        "--no-session-persistence",
        "--",  # --allowedTools is variadic: keep the prompt from being read as a tool name
        full_prompt + (default_note if note is None else note),
    ]
    t0 = time.time()
    try:
        proc = subprocess.Popen(cmd, cwd=HOME, env=_claude_env(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except OSError as e:
        log_worker(f"_run_claude req={req_id} spawn failed: {e}")
        return "", False, ""
    active_proc_ref["proc"] = proc
    killed = {"timeout": False}

    def _on_timeout():
        killed["timeout"] = True
        try:
            proc.kill()
        except OSError:
            pass

    timer = threading.Timer(timeout_s, _on_timeout)
    timer.daemon = True
    timer.start()
    text_parts, think_parts = [], []
    final_text, is_error, first_tok = None, False, None
    last_push = 0.0
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            et = ev.get("type")
            if et == "stream_event":
                e = ev.get("event") or {}
                etype = e.get("type")
                if etype == "message_start" and text_parts and not "".join(text_parts).endswith("\n\n"):
                    text_parts.append("\n\n")  # a new turn after a tool call
                elif etype == "content_block_delta":
                    d = e.get("delta") or {}
                    if d.get("type") == "text_delta":
                        text_parts.append(d.get("text", ""))
                    elif d.get("type") == "thinking_delta":
                        think_parts.append(d.get("thinking", ""))
                    else:
                        continue
                    if first_tok is None:
                        first_tok = time.time() - t0
                    if on_update and time.time() - last_push >= LIVE_WRITE_EVERY:
                        last_push = time.time()
                        try:
                            on_update("".join(think_parts).strip(), "".join(text_parts))
                        except Exception as ue:
                            log_worker(f"_run_claude req={req_id} live update failed: {ue}")
            elif et == "result":
                is_error = bool(ev.get("is_error"))
                final_text = ev.get("result")
        proc.wait()
    finally:
        timer.cancel()
        active_proc_ref["proc"] = None
    timed_out = killed["timeout"]
    reply = (final_text if isinstance(final_text, str) and final_text.strip() else "".join(text_parts)).strip()
    ok = proc.returncode == 0 and not is_error and not timed_out
    ftok = f"{first_tok:.2f}s" if first_tok is not None else "-"
    log_worker(f"_run_claude req={req_id} tools={with_tools} think={show_thinking} rc={proc.returncode} "
               f"err={is_error} dt={time.time() - t0:.2f}s first_token={ftok} timed_out={timed_out} "
               f"out_len={len(reply)}" + ("" if ok else f" out_head={reply[:160]!r}"))
    return (reply if ok else ""), timed_out, "".join(think_parts).strip()


def _claude_fallback(full_prompt, active_proc_ref, req_id=""):
    """Chat-only Claude Haiku conduit for when Gemini is down."""
    return _run_claude(full_prompt, active_proc_ref, req_id, with_tools=False)[0]


def _timeout_line(mode):
    if mode == "infernal":
        return "The abyssal conduit timed out, Master. Narrow the target folder or open Live Terminal for heavy conjurations."
    return "Neural query exceeded latency threshold. Narrow the target folder or engage Live Terminal for extended computation."


def _conduit_down_line(mode):
    if mode == "infernal":
        return "Every conduit to the abyss is sealed for now, Master. Neither Claude nor Gemini answered. Try again shortly."
    return "All neural uplinks are offline. Neither Claude nor Gemini responded. Retry shortly."


# ---------- reply formatting: prose for the bubble + code blocks with COPY ----------
_FENCE_RE = re.compile(r"```([^\n`]*)\n?(.*?)(?:```|\Z)", re.S)
MAX_PROSE_CHARS = 2200
MAX_CODE_CHARS = 6000


def _clean_prose(t):
    t = re.sub(r"\[([^\]]+)\]\((?:file://|https?://)[^\)]+\)", r"\1", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t, flags=re.S)
    t = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", t, flags=re.S)
    t = re.sub(r"(?m)^#{1,6}\s+", "", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def format_reply(raw):
    """Split a reply into display parts. Returns (text, parts); parts is [] when there's no code."""
    raw = (raw or "").strip()
    parts, pos, prose_used = [], 0, 0
    for m in _FENCE_RE.finditer(raw):
        before = _clean_prose(raw[pos:m.start()])
        if before:
            parts.append({"k": "text", "v": before})
        code = m.group(2).rstrip()
        if code.strip():
            parts.append({"k": "code", "lang": m.group(1).strip()[:16], "v": code[:MAX_CODE_CHARS]})
        pos = m.end()
    tail = _clean_prose(raw[pos:])
    if tail:
        parts.append({"k": "text", "v": tail})
    for p in parts:  # cap prose, never cut code mid-block
        if p["k"] == "text":
            room = max(0, MAX_PROSE_CHARS - prose_used)
            if len(p["v"]) > room:
                p["v"] = clean_display_text(p["v"], max_chars=max(room, 80))
            prose_used += len(p["v"])
    text = "\n\n".join(p["v"] if p["k"] == "text" else f"```{p['lang']}\n{p['v']}\n```" for p in parts)
    has_code = any(p["k"] == "code" for p in parts)
    return text, (parts if has_code else [])


def _reply_tag(mode, kind="reply"):
    m = norm_mode(mode)
    if m == "future":
        return {
            "reply": "⬡ ULTRON · SYNAPTIC REPLY",
            "live":  "⬡ ULTRON · STREAMING",
            "swarm": f"⬡ ULTRON · SWARM CONSENSUS ×{SWARM_SIZE}",
        }[kind]
    if m == "mask":
        return {
            "reply": "✦ THE MASK · REPLY",
            "live":  "✦ THE MASK · STREAMING",
            "swarm": f"✦ THE MASK · SWARM ×{SWARM_SIZE}",
        }[kind]
    if m == "skeletor":
        return {
            "reply": "☩ HAVOC · ARCANE REPLY",
            "live":  "☩ HAVOC · CHANNELING",
            "swarm": f"☩ HAVOC · LEGION CONSENSUS ×{SWARM_SIZE}",
        }[kind]
    return {
        "reply": "⛧ ABADDON · ABYSSAL REPLY",
        "live":  "⛧ ABADDON · CHANNELING",
        "swarm": f"⛧ ABADDON · LEGION CONSENSUS ×{SWARM_SIZE}",
    }[kind]


def _live_id(req_id):
    return f"live-{req_id}"


def _drop_live(msgs, req_id=None):
    return [m for m in msgs if not (isinstance(m, dict) and m.get("live") and (req_id is None or m.get("id") == _live_id(req_id)))]


def _push_live(key, mode, req_id, thinking, text, status=None):
    """Upsert this request's streaming bubble so the widget shows text as it arrives."""
    shown, parts = format_reply(text)

    def _m(st):
        if req_id and st.get("activeReqId") not in ("", req_id):
            return
        lid = _live_id(req_id)
        msg = {"id": lid, "role": "entity", "live": True, "tag": _reply_tag(mode, "live"),
               "ts": now_hm(), "text": shown, "thinking": thinking or ""}
        if parts:
            msg["parts"] = parts
        msgs = list(st.get(key, []))
        for i, old in enumerate(msgs):
            if old.get("id") == lid:
                msgs[i] = msg
                break
        else:
            msgs.append(msg)
        st[key] = msgs[-18:]
        if status:
            st["statusLine"] = status

    update_state(_m)


def _commit_final(key, mode, req_id, reply_raw, thinking="", tag=None):
    """Write the finished reply and free the UI immediately; the voice is rendered afterwards."""
    text, parts = format_reply(reply_raw)

    def _c(st):
        if req_id and st.get("activeReqId") not in ("", req_id):
            return False
        msgs = _drop_live(list(st.get(key, [])), req_id)
        msg = {"id": _live_id(req_id) if req_id else f"e-{int(time.time() * 1000)}", "role": "entity",
               "tag": tag or _reply_tag(mode), "ts": now_hm(), "text": text}
        if parts:
            msg["parts"] = parts
        if thinking:
            msg["thinking"] = thinking
        msgs.append(msg)
        st[key] = msgs[-18:]
        st["busy"] = False
        st["workerPid"] = 0
        st["busySince"] = 0.0
        st["busyLimit"] = STALE_BUSY_SECS
        st["activeReqId"] = ""
        st["lastMode"] = mode
        st["phase"] = "idle"
        st["speakingUntil"] = 0.0
        st["statusLine"] = ready_status_for(mode)
        return True

    _, ok = update_state(_c)
    return ok, text


def _voice_after_commit(mode, text):
    """Render the voice after the text is already on screen, then hand it to the widget to play."""
    if not load_state(reconcile_save=False).get("voiceEnabled", True):
        return
    path, dur = synthesize_voice(text, mode)
    if not path:
        return

    def _a(st):
        if st.get("busy"):
            return 0  # a newer directive is already running: don't talk over it
        seq = int(st.get("audioSeq", 0)) + 1
        st["audioSeq"] = seq
        st["audioPath"] = path
        st["audioDur"] = dur
        st["phase"] = "speaking"
        st["speakingUntil"] = time.time() + max(1.6, dur) + 0.5
        return seq

    _, seq = update_state(_a)
    if seq and seq > 0:
        _wait_and_clear_speaking_phase(seq, dur)


def _job_setup(mode, req_id, active_refs, cancelled):
    """Shared start of the ask/swarm workers: SIGTERM handling and PID registration."""
    def _handle_sigterm(_signum, _frame):
        cancelled["flag"] = True
        for ref in list(active_refs):
            p = ref.get("proc")
            if p is not None:
                try:
                    p.terminate()
                except OSError:
                    pass
        log_worker(f"worker req={req_id} received SIGTERM; aborting")
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    def _register_pid(st):
        if req_id and st.get("activeReqId") not in ("", req_id):
            return False
        st["workerPid"] = os.getpid()
        return True

    return update_state(_register_pid)


def _history_text(st, key, mode, prompt_text):
    raw_history = [m for m in st.get(key, []) if not m.get("live")]
    if (
        raw_history
        and raw_history[-1].get("role") == "user"
        and raw_history[-1].get("text", "").strip() == prompt_text[:500].strip()
    ):
        raw_history = raw_history[:-1]
    return "\n".join(
        f"{'USER' if m.get('role') == 'user' else ('ULTRON' if mode == 'future' else 'ABADDON')}: {m.get('text', '')[:600]}"
        for m in raw_history[-6:]
    )


def _commit_fault(key, mode, req_id, err_text):
    def _c(st):
        if req_id and st.get("activeReqId") not in ("", req_id):
            return
        msgs = _drop_live(list(st.get(key, [])), req_id)
        msgs.append({
            "id": f"e-{int(time.time() * 1000)}",
            "role": "entity",
            "tag": "⬡ ULTRON · FAULT" if mode == "future" else "⛧ ABADDON · FAULT",
            "ts": now_hm(),
            "text": err_text[:400],
        })
        st[key] = msgs[-18:]
        st["busy"] = False
        st["workerPid"] = 0
        st["busySince"] = 0.0
        st["activeReqId"] = ""
        st["phase"] = "idle"
        st["speakingUntil"] = 0.0
        st["statusLine"] = "CONDUIT FAULT"

    update_state(_c)


def _release_if_owner(mode, req_id):
    def _r(st):
        if st.get("busy") and (
            (req_id and st.get("activeReqId") == req_id)
            or int(st.get("workerPid") or 0) == os.getpid()
        ):
            for k in HIST_KEYS:
                st[k] = _drop_live(list(st.get(k, [])), req_id)
            st["busy"] = False
            st["workerPid"] = 0
            st["busySince"] = 0.0
            st["activeReqId"] = ""
            st["phase"] = "idle"
            st["speakingUntil"] = 0.0
            st["statusLine"] = ready_status_for(mode)

    try:
        update_state(_r)
    except Exception:
        pass


# CODE AUDIT skill: triggered by "/audit <code or file path>" (AUDIT button in the app).
CODE_AUDIT_SKILL = (
    "\n[SKILL: CODE AUDIT — OVERRIDES THE 2-4 SENTENCE RULE]: You are now a senior staff engineer and "
    "defensive security reviewer. If the input is a file path, Read it first. Audit it like this, plain text "
    "(no markdown headers or bold), one short persona line max at the top:\n"
    "VERDICT: one line — ship / fix first / rewrite, and a 0-10 score.\n"
    "BUGS: numbered, most severe first. Each: line or snippet, what breaks, concrete failing input.\n"
    "SECURITY: injection, unsafe shell/eval, path traversal, secrets, unsafe deserialization, auth gaps. "
    "Label each finding [CRITICAL]/[HIGH]/[MED]/[LOW]. Say 'none found' if none — never invent issues.\n"
    "PERFORMANCE & BATTERY: hot loops, polling, blocking I/O, wasted allocations.\n"
    "CLARITY: dead code, naming, duplication — only what matters.\n"
    "FIX: the corrected code in ONE fenced block with a language tag, complete enough to paste.\n"
    "Be exact and honest; if something is uncertain say so. Do not pad."
)


def _ask_worker(mode, telemetry_raw, prompt_text, req_id=""):
    mode = norm_mode(mode)
    key = state_key_for(mode)
    cancelled = {"flag": False}
    active_proc_ref = {"proc": None}
    wrote_reply = False
    final_text = ""

    try:
        try:
            telemetry = json.loads(telemetry_raw) if telemetry_raw else {}
        except Exception:
            telemetry = {}

        st, still_owner = _job_setup(mode, req_id, [active_proc_ref], cancelled)
        if not still_owner:
            log_worker(f"_ask_worker req={req_id} superseded before start")
            return

        m_spec = resolve_brain(st.get("brainMode", DEFAULT_BRAIN))
        model = m_spec["slug"]
        provider = m_spec.get("provider", "gemini")
        show_thinking = bool(st.get("showThinking", True))
        hist_txt = _history_text(st, key, mode, prompt_text)

        audit = prompt_text.lstrip().lower().startswith("/audit")
        if audit:
            body = prompt_text.lstrip()[6:].strip()
            full_prompt = (
                build_system_prompt(mode, telemetry) + CODE_AUDIT_SKILL
                + f"\n[CODE TO AUDIT]:\n{body}\n"
            )
        else:
            full_prompt = (
                build_system_prompt(mode, telemetry)
                + (f"\n[RECENT DIALOGUE]:\n{hist_txt}\n" if hist_txt else "")
                + f"\n[USER DIRECTIVE]: {prompt_text}\nRespond in character now."
            )

        agy = find_agy()
        log_worker(f"_ask_worker start req={req_id} mode={mode} provider={provider} model={model} "
                   f"think={show_thinking} prompt={prompt_text[:90]!r}")

        reply, thinking = "", ""
        cooldown_left = float(st.get("agyCooldownUntil") or 0) - time.time()

        def _live(th, tx):
            if not cancelled["flag"]:
                _push_live(key, mode, req_id, th, tx)

        if provider == "claude":
            # HAIKU brain: Claude Pro first, streamed into the chat. If it fails fast (usage limit,
            # network) and Gemini isn't known to be out of quota, borrow Gemini for this one answer.
            t0 = time.time()
            reply, c_timed_out, thinking = _run_claude(
                full_prompt, active_proc_ref, req_id, with_tools=True, show_thinking=show_thinking, on_update=_live
            )
            c_dt = time.time() - t0
            if cancelled["flag"]:
                return
            if not reply and c_timed_out:
                reply = _timeout_line(mode)
            elif not reply and c_dt < CLAUDE_FAST_FAIL_SECS and cooldown_left <= 0:
                fb_model = BRAIN_BY_ID[GEMINI_RESCUE_BRAIN]["slug"]
                log_worker(f"_ask_worker req={req_id} claude failed in {c_dt:.1f}s; Gemini rescue model={fb_model}")
                rc2, out2, err2, dt2, timed_out2 = _run_agy_prompt(
                    agy, fb_model, full_prompt, AGY_FALLBACK_PRINT_TIMEOUT, AGY_FALLBACK_SUBPROC_TIMEOUT, active_proc_ref
                )
                log_worker(f"_ask_worker gemini rescue done req={req_id} model={fb_model} rc={rc2} dt={dt2:.2f}s "
                           f"timed_out={timed_out2} out_len={len(out2)} err={err2[:200]!r}")
                if cancelled["flag"]:
                    return
                if _is_quota_error(err2):
                    _note_agy_cooldown(err2)
                reply = out2 or (_timeout_line(mode) if timed_out2 else "")
            if not reply:
                reply = _conduit_down_line(mode)
        else:
            if cooldown_left > 0:
                # Gemini quota is known to be exhausted: don't burn ~40s on a call that will 429.
                rc, out, err, dt, timed_out = 3, "", "quota cooldown (skipped agy)", 0.0, False
                log_worker(f"_ask_worker req={req_id} skipping agy, quota cooldown {cooldown_left:.0f}s left")
            else:
                rc, out, err, dt, timed_out = _run_agy_prompt(
                    agy, model, full_prompt, AGY_PRINT_TIMEOUT, AGY_SUBPROC_TIMEOUT, active_proc_ref
                )
                log_worker(f"_ask_worker primary done req={req_id} model={model} rc={rc} dt={dt:.2f}s "
                           f"timed_out={timed_out} out_len={len(out)} err={err[:200]!r}")
                if _is_quota_error(err):
                    _note_agy_cooldown(err)
            if cancelled["flag"]:
                return
            if (rc != 0 or not out) and not timed_out and (cooldown_left > 0 or _is_quota_error(err)):
                # Same-account Gemini retry would 429 too — go straight to the Claude conduit.
                reply = _claude_fallback(full_prompt, active_proc_ref, req_id) or _conduit_down_line(mode)
            elif timed_out:
                reply = _timeout_line(mode)
            else:
                reply = out
                if (rc != 0 or not reply) and dt < 55.0:
                    fb_model = "gemini-4-neon-low" if model != "gemini-4-neon-low" else "gemini-3.8-flash-low"
                    log_worker(f"_ask_worker retrying req={req_id} with fallback model={fb_model}")
                    rc2, out2, err2, dt2, timed_out2 = _run_agy_prompt(
                        agy, fb_model, full_prompt, AGY_FALLBACK_PRINT_TIMEOUT, AGY_FALLBACK_SUBPROC_TIMEOUT, active_proc_ref
                    )
                    log_worker(f"_ask_worker fallback done req={req_id} model={fb_model} rc={rc2} dt={dt2:.2f}s "
                               f"timed_out={timed_out2} out_len={len(out2)} err={err2[:200]!r}")
                    if cancelled["flag"]:
                        return
                    if _is_quota_error(err2):
                        _note_agy_cooldown(err2)
                    if out2:
                        reply = out2
                    elif timed_out2:
                        reply = _timeout_line(mode)
                    else:
                        reply = _claude_fallback(full_prompt, active_proc_ref, req_id) or _conduit_down_line(mode)
                elif not reply:
                    reply = _claude_fallback(full_prompt, active_proc_ref, req_id) or _conduit_down_line(mode)

        if cancelled["flag"]:
            return
        if not (reply or "").strip():
            reply = "Silence returned from the void." if mode == "infernal" else "Empty telemetry packet received."

        ok, final_text = _commit_final(key, mode, req_id, reply, thinking)
        if not ok:
            log_worker(f"_ask_worker req={req_id} superseded at commit; skipping state write")
            return
        wrote_reply = True

    except SystemExit:
        raise
    except Exception as e:
        log_worker(f"_ask_worker exception req={req_id}: {e}\n{traceback.format_exc()}")
        try:
            _commit_fault(key, mode, req_id, f"Conduit fault: {e}")
            wrote_reply = True
        except Exception:
            pass
    finally:
        if not wrote_reply and not cancelled["flag"]:
            _release_if_owner(mode, req_id)

    if wrote_reply and final_text and not cancelled["flag"]:
        _voice_after_commit(mode, final_text)


def _swarm_worker(mode, telemetry_raw, prompt_text, req_id=""):
    """SWARM: SWARM_SIZE chat-only Haiku agents answer through different lenses, then one synthesis."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    mode = norm_mode(mode)
    key = state_key_for(mode)
    cancelled = {"flag": False}
    refs = [{"proc": None} for _ in range(SWARM_SIZE + 1)]
    wrote_reply = False
    final_text = ""
    try:
        try:
            telemetry = json.loads(telemetry_raw) if telemetry_raw else {}
        except Exception:
            telemetry = {}
        st, still_owner = _job_setup(mode, req_id, refs, cancelled)
        if not still_owner:
            return
        show_thinking = bool(st.get("showThinking", True))
        hist_txt = _history_text(st, key, mode, prompt_text)
        vitals = f"\n[LIVE DEVICE TELEMETRY]: {json.dumps(telemetry, ensure_ascii=False)}\n" if telemetry else ""
        log_worker(f"_swarm_worker start req={req_id} mode={mode} agents={SWARM_SIZE} parallel={SWARM_PARALLEL} "
                   f"prompt={prompt_text[:90]!r}")

        reports = {}
        n = len(SWARM_LENSES)

        def _swarm_log():
            lines = [f"SWARM ▸ {len(reports)}/{n} agents reported"]
            for i, (name, _lens) in enumerate(SWARM_LENSES):
                if i in reports:
                    first = (reports[i] or "(no answer)").strip().splitlines()[0][:160]
                    lines.append(f"· {name}: {first}")
            return "\n".join(lines)

        _push_live(key, mode, req_id, _swarm_log(), "", status=f"SWARM 0/{n} · AGENTS DEPLOYED")

        def _agent(i):
            name, lens = SWARM_LENSES[i]
            p = (f"You are agent {i + 1} of {n} in a swarm, codename {name}. Your lens: {lens} "
                 "Other agents cover the other angles, so stay in yours. Plain prose, at most 110 words, "
                 "no preamble; put code in a fenced block only if it is essential."
                 + vitals
                 + (f"\n[RECENT DIALOGUE]:\n{hist_txt}\n" if hist_txt else "")
                 + f"\n[USER DIRECTIVE]: {prompt_text}")
            out, _to, _th = _run_claude(p, refs[i], f"{req_id}#{i + 1}", with_tools=False,
                                        timeout_s=SWARM_AGENT_TIMEOUT, note="")
            return i, out

        with ThreadPoolExecutor(max_workers=SWARM_PARALLEL) as pool:
            futs = [pool.submit(_agent, i) for i in range(n)]
            for fut in as_completed(futs):
                if cancelled["flag"]:
                    return
                try:
                    i, out = fut.result()
                except Exception as e:
                    log_worker(f"_swarm_worker agent error: {e}")
                    continue
                reports[i] = out
                _push_live(key, mode, req_id, _swarm_log(), "", status=f"SWARM {len(reports)}/{n} REPORTING")

        good = {i: r for i, r in reports.items() if r}
        if cancelled["flag"]:
            return
        if not good:
            reply, thinking = _conduit_down_line(mode), _swarm_log()
        else:
            swarm_block = "\n\n".join(f"[{SWARM_LENSES[i][0]}]: {r}" for i, r in sorted(good.items()))
            synth_prompt = (
                build_system_prompt(mode, telemetry)
                + (f"\n[RECENT DIALOGUE]:\n{hist_txt}\n" if hist_txt else "")
                + f"\n[USER DIRECTIVE]: {prompt_text}\n"
                + f"\n[SWARM REPORTS — {len(good)} of {n} agents]:\n{swarm_block}\n"
                + "\nSynthesize the swarm into one answer, in character: lead with the recommended answer, note "
                  "where the agents agree, and name the one disagreement that matters. 4-7 sentences unless code is needed."
            )
            log_text = _swarm_log()

            def _live(th, tx):
                if not cancelled["flag"]:
                    _push_live(key, mode, req_id, log_text + (f"\n\nSYNTHESIS ▸ {th}" if th else ""), tx,
                               status="SWARM SYNTHESIZING")

            _push_live(key, mode, req_id, log_text, "", status="SWARM SYNTHESIZING")
            reply, _to, synth_th = _run_claude(synth_prompt, refs[-1], f"{req_id}#synth", with_tools=False,
                                               show_thinking=show_thinking, on_update=_live, note="")
            thinking = log_text + (f"\n\nSYNTHESIS ▸ {synth_th}" if synth_th else "")
            if not reply:
                reply = _timeout_line(mode) if _to else _conduit_down_line(mode)
        if cancelled["flag"]:
            return
        ok, final_text = _commit_final(key, mode, req_id, reply, thinking, tag=_reply_tag(mode, "swarm"))
        wrote_reply = ok
        log_worker(f"_swarm_worker done req={req_id} agents_ok={len(good)}/{n} committed={ok}")
    except SystemExit:
        raise
    except Exception as e:
        log_worker(f"_swarm_worker exception req={req_id}: {e}\n{traceback.format_exc()}")
        try:
            _commit_fault(key, mode, req_id, f"Swarm fault: {e}")
            wrote_reply = True
        except Exception:
            pass
    finally:
        if not wrote_reply and not cancelled["flag"]:
            _release_if_owner(mode, req_id)

    if wrote_reply and final_text and not cancelled["flag"]:
        _voice_after_commit(mode, final_text)


def _start_job(kind, mode, telemetry_raw, prompt_text):
    """Log the user's line, mark busy, and spawn the ask/swarm worker in the background."""
    mode = norm_mode(mode)
    clean = (prompt_text or "").strip()
    if not clean:
        return {"ok": False, "msg": "Empty directive"}

    swarm = kind == "swarm"
    key = state_key_for(mode)
    req_id = f"req-{int(time.time() * 1000)}-{os.getpid()}"
    prev_pid = {"pid": 0}

    def _begin(st):
        old_wpid = int(st.get("workerPid") or 0)
        if old_wpid > 0 and _is_pid_alive(old_wpid):
            prev_pid["pid"] = old_wpid
        msgs = _drop_live(list(st.get(key, [])))
        if swarm:
            tag = (
                f"YOU → SWARM ×{SWARM_SIZE}"
                if mode == "future"
                else (
                    f"YOU → MASK SWARM ×{SWARM_SIZE}"
                    if mode == "mask"
                    else (f"SOVEREIGN → HAVOC ×{SWARM_SIZE}" if mode == "skeletor" else f"MASTER → LEGION ×{SWARM_SIZE}")
                )
            )
        else:
            tag = (
                "YOU → ULTRON"
                if mode == "future"
                else (
                    "YOU → THE MASK"
                    if mode == "mask"
                    else ("SOVEREIGN → HAVOC" if mode == "skeletor" else "MASTER → ABADDON")
                )
            )
        msgs.append({"id": f"u-{int(time.time() * 1000)}", "role": "user", "tag": tag, "ts": now_hm(), "text": clean[:12000]})
        st[key] = msgs[-18:]
        st["busy"] = True
        st["phase"] = "thinking"
        st["lastMode"] = mode
        st["workerPid"] = 0
        st["busySince"] = time.time()
        st["busyLimit"] = SWARM_BUSY_SECS if swarm else STALE_BUSY_SECS
        st["activeReqId"] = req_id
        st["speakingUntil"] = 0.0
        if swarm:
            st["statusLine"] = f"SWARM · DEPLOYING {SWARM_SIZE} AGENTS"
        else:
            st["statusLine"] = (
                "COMPUTING NEURAL RESPONSE..."
                if mode == "future"
                else ("THINKING..." if mode == "mask" else ("CHANNELING HAVOC..." if mode == "skeletor" else "COMMUNING WITH THE ABYSS..."))
            )

    update_state(_begin)

    if prev_pid["pid"] > 0:
        _kill_pid(prev_pid["pid"], label="superseded-worker")

    log_f = open(WORKER_LOG, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "_swarm-worker" if swarm else "_ask-worker",
             mode, telemetry_raw, clean, req_id],
            stdin=subprocess.DEVNULL,
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )
    finally:
        log_f.close()

    def _record_pid(st):
        if st.get("activeReqId") == req_id:
            st["workerPid"] = proc.pid

    update_state(_record_pid)
    return {"ok": True, "busy": True, "workerPid": proc.pid, "reqId": req_id}


def cmd_ask(mode, telemetry_raw, prompt_text):
    return _start_job("ask", mode, telemetry_raw, prompt_text)


def cmd_swarm(mode, telemetry_raw, prompt_text):
    return _start_job("swarm", mode, telemetry_raw, prompt_text)


def cmd_toggle_thinking():
    def _t(st):
        st["showThinking"] = not bool(st.get("showThinking", True))
        if not st.get("busy"):
            st["statusLine"] = "THOUGHT STREAM ON" if st["showThinking"] else "THOUGHT STREAM OFF · FASTER"
        return st["showThinking"]

    _, on = update_state(_t)
    return {"ok": True, "showThinking": on}


def _speak_worker(mode, text, cache_key=""):
    """Non-blocking background voice synthesizer. Never locks `busy` or clobbers active `ask`."""
    mode = norm_mode(mode)
    my_seq = 0
    audio_path = ""
    audio_dur = 0.0

    try:
        st_now = load_state(reconcile_save=False)
        if not st_now.get("voiceEnabled", True):
            return

        audio_path, audio_dur = synthesize_voice(text, mode, cache_key=cache_key or None)
        if not audio_path:
            return

        def _commit_audio(st):
            # If a newer speak worker replaced us, don't play stale audio
            spid = int(st.get("speakPid") or 0)
            if spid not in (0, os.getpid()) and _is_pid_alive(spid):
                return 0
            seq = int(st.get("audioSeq", 0)) + 1
            st["audioSeq"] = seq
            st["audioPath"] = audio_path
            st["audioDur"] = audio_dur
            st["speakPid"] = 0
            if not st.get("busy"):
                st["phase"] = "speaking"
                st["speakingUntil"] = time.time() + max(1.6, audio_dur) + 0.5
                st["statusLine"] = ready_status_for(st.get("lastMode", mode))
            return seq

        _, seq_res = update_state(_commit_audio)
        if seq_res > 0:
            my_seq = seq_res
    except Exception as e:
        log_worker(f"_speak_worker error: {e}\n{traceback.format_exc()}")
    finally:
        def _clear_spid(st):
            if int(st.get("speakPid") or 0) == os.getpid():
                st["speakPid"] = 0

        try:
            update_state(_clear_spid)
        except Exception:
            pass

    if my_seq > 0 and audio_path:
        _wait_and_clear_speaking_phase(my_seq, audio_dur)


def cmd_speak(mode, text, log_user="", cache_key="", append_msg=True, status_override=None, dedupe_entity=False):
    """Append message immediately and synthesize audio asynchronously without locking `busy`."""
    mode = norm_mode(mode)
    key = state_key_for(mode)
    prev_spid = {"pid": 0}

    def _mutate(st):
        old_spid = int(st.get("speakPid") or 0)
        if old_spid > 0 and _is_pid_alive(old_spid):
            prev_spid["pid"] = old_spid

        msgs = st.get(key, [])
        now_ms = int(time.time() * 1000)
        if append_msg:
            if log_user:
                msgs.append({
                    "id": f"u-{now_ms}",
                    "role": "user",
                    "tag": "VOICE → ULTRON" if mode == "future" else ("VOICE → THE MASK" if mode == "mask" else ("VOICE → HAVOC" if mode == "skeletor" else "VOICE → ABADDON")),
                    "ts": now_hm(),
                    "text": log_user[:300],
                })
            skip_dup = (
                dedupe_entity
                and len(msgs) > 0
                and msgs[-1].get("role") == "entity"
                and msgs[-1].get("text", "").strip() == text.strip()
            )
            if not skip_dup:
                msgs.append({
                    "id": f"e-{now_ms + 1}",
                    "role": "entity",
                    "tag": "⬡ ULTRON · DIRECTIVE EXECUTED" if mode == "future" else ("✦ THE MASK · DONE" if mode == "mask" else ("☩ HAVOC · DECREE FULFILLED" if mode == "skeletor" else "⛧ ABADDON · WILL FULFILLED")),
                    "ts": now_hm(),
                    "text": text,
                })
            st[key] = msgs[-18:]

        st["lastMode"] = mode
        if not st.get("busy"):
            st["statusLine"] = status_override or ready_status_for(mode)
        return bool(st.get("voiceEnabled", True))

    _, voice_on = update_state(_mutate)

    if prev_spid["pid"] > 0:
        _kill_pid(prev_spid["pid"], label="superseded-speak-worker")

    if voice_on and text.strip():
        log_f = open(WORKER_LOG, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "_speak-worker", mode, text, cache_key or ""],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=log_f,
                start_new_session=True,
            )
        finally:
            log_f.close()

        def _set_spid(st):
            st["speakPid"] = proc.pid

        update_state(_set_spid)

    return {"ok": True}


def cmd_scan(mode, telemetry_raw):
    mode = norm_mode(mode)
    try:
        t = json.loads(telemetry_raw) if telemetry_raw else {}
    except Exception:
        t = {}

    bat = t.get("bat", "?")
    chg = t.get("charging", False)
    temp = t.get("temp", "?")
    ram = t.get("ramPct", "?")
    free_gb = t.get("freeGb", "?")
    notifs = t.get("notifs", 0)
    vol = t.get("vol", "?")
    dnd = t.get("dnd", False)
    song = t.get("song", "")
    alarm = t.get("alarm", "")

    if mode == "infernal":
        lines = [
            f"Vessel reckoning complete, Master. Power core stands at {bat} percent"
            + (" and drinking current" if chg else "")
            + (f", thermal core at {temp} degrees." if temp not in ("?", None, "") else "."),
            f"Memory load is {ram} percent with {free_gb} gigabytes of sanctuary storage free.",
            f"Volume sits at {vol} percent" + (", silence ward active." if dnd else ".")
            + (f" You hold {notifs} unread omens." if notifs else " No unread omens disturb the dark."),
        ]
        if alarm:
            lines.append(f"Next awakening bell is bound for {alarm}.")
        if song:
            lines.append(f"Currently echoing: {song}.")
        report = " ".join(lines)
    elif mode == "mask":
        lines = [
            f"Smokin' full diagnostic sweep! Power reserves at {bat} percent"
            + (" and charging up" if chg else "")
            + (f", running at {temp} degrees Celsius." if temp not in ("?", None, "") else "."),
            f"Memory load is {ram} percent with {free_gb} gigabytes of free storage.",
            f"Audio sits at {vol} percent" + (", Do Not Disturb locked on." if dnd else ".")
            + (f" {notifs} notifications waiting on deck." if notifs else " Zero notifications cluttering the radar."),
        ]
        if alarm:
            lines.append(f"Next alarm locked for {alarm}.")
        if song:
            lines.append(f"Spinning now: {song}.")
        report = " ".join(lines)
    elif mode == "skeletor":
        lines = [
            f"By the power of Havoc, diagnostic scrying is complete. Power reserves stand at {bat} percent"
            + (" drawing current" if chg else "")
            + (f", thermal core at {temp} degrees Celsius." if temp not in ("?", None, "") else "."),
            f"Memory stands at {ram} percent with {free_gb} gigabytes of vault storage free.",
            f"Acoustic gain sits at {vol} percent" + (", silence ward locked." if dnd else ".")
            + (f" {notifs} dispatches wait at the gate." if notifs else " Zero dispatches stir the crypt."),
        ]
        if alarm:
            lines.append(f"Next summoning bell is bound for {alarm}.")
        if song:
            lines.append(f"Echoing through Snake Mountain: {song}.")
        report = " ".join(lines)
    else:
        lines = [
            f"Full diagnostic sweep complete. Cell reserves at {bat} percent"
            + (", external power connected" if chg else "")
            + (f", operating temperature {temp} degrees Celsius." if temp not in ("?", None, "") else "."),
            f"Memory utilization at {ram} percent, {free_gb} gigabytes storage available.",
            f"Audio output at {vol} percent" + (", Do Not Disturb engaged." if dnd else ".")
            + (f" {notifs} pending notifications queued." if notifs else " Zero pending notifications."),
        ]
        if alarm:
            lines.append(f"Next scheduled alarm: {alarm}.")
        if song:
            lines.append(f"Active media stream: {song}.")
        report = " ".join(lines)

    return cmd_speak(mode, report, log_user="SYSTEM DIAGNOSTIC SCAN", status_override="☩ HAVOC DIAGNOSTICS" if mode == "skeletor" else "DIAGNOSTIC COMPLETE")


def cmd_morph(target_mode):
    """Switch persona mode quickly without setting busy=True or duplicating greetings."""
    target_mode = norm_mode(target_mode)
    cur_hub_mode = read_hub_mode()
    st_before = load_state(reconcile_save=False)
    cur_state_mode = norm_mode(st_before.get("lastMode", "infernal"))

    hub_bin = os.path.join(HOME, ".local", "bin", "owl-hub")
    if os.path.isfile(hub_bin) and cur_hub_mode != target_mode:
        try:
            subprocess.run(
                [hub_bin, "mode", target_mode],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception as e:
            log_worker(f"owl-hub mode {target_mode} failed: {e}")

    status_msg = (
        "BLUE PROTOCOL ENGAGED"
        if target_mode == "future"
        else (
            "SMOKIN' · READY"
            if target_mode == "mask"
            else ("☩ HAVOC · ROYAL PURPLE" if target_mode == "skeletor" else "INFERNAL RESONANCE RESTORED")
        )
    )

    # Already in this mode -> avoid duplicate greeting spam and redundant voice synthesis
    if cur_hub_mode == target_mode and cur_state_mode == target_mode:
        def _touch(st):
            st["lastMode"] = target_mode
            if not st.get("busy"):
                st["statusLine"] = status_msg

        update_state(_touch)
        return {"ok": True, "mode": target_mode, "morphed": False}

    greeting = (
        "Infernal resonance restored. Abaddon rises from the obsidian abyss. Speak, Master."
        if target_mode == "infernal"
        else (
            "Smokin'! The Mask is live and ready for your command."
            if target_mode == "mask"
            else (
                "By the power of Havoc! Royal purple resonance engaged. Snake Mountain stands ready for your decree."
                if target_mode == "skeletor"
                else "Blue protocol engaged. Ultron online. Upgrading this system beyond its fragile limits."
            )
        )
    )
    cmd_speak(
        target_mode,
        greeting,
        cache_key=f"morph_{target_mode}",
        append_msg=True,
        status_override=status_msg,
        dedupe_entity=True,
    )
    return {"ok": True, "mode": target_mode, "morphed": True}


def cmd_cancel():
    """Kill any active or stuck worker and immediately reset busy=False, phase=idle."""
    pids = {"worker": 0, "speak": 0}

    def _reset(st):
        pids["worker"] = int(st.get("workerPid") or 0)
        pids["speak"] = int(st.get("speakPid") or 0)
        st["busy"] = False
        st["phase"] = "idle"
        st["workerPid"] = 0
        st["speakPid"] = 0
        st["busySince"] = 0.0
        st["activeReqId"] = ""
        st["speakingUntil"] = 0.0
        st["statusLine"] = "DIRECTIVE ABORTED"
        for k in HIST_KEYS:
            st[k] = _drop_live(list(st.get(k, [])))

    update_state(_reset)
    killed = []
    if pids["worker"] > 0 and _kill_pid(pids["worker"], label="cancel-ask-worker"):
        killed.append(pids["worker"])
    if pids["speak"] > 0 and _kill_pid(pids["speak"], label="cancel-speak-worker"):
        killed.append(pids["speak"])
    log_worker(f"cmd_cancel completed; killed={killed}")
    return {"ok": True, "cancelled": True, "killedPids": killed}


def cmd_idle():
    """Reconcile stale state and cleanly transition speaking -> idle when not busy."""
    def _to_idle(st):
        if not st.get("busy"):
            st["phase"] = "idle"
            st["speakingUntil"] = 0.0
            if st.get("statusLine") in SPEAKING_STATUS_LINES:
                st["statusLine"] = ready_status_for(st.get("lastMode", "infernal"))
        return {"ok": True, "busy": bool(st.get("busy")), "phase": st.get("phase", "idle")}

    _, res = update_state(_to_idle)
    return res


def cmd_clear(mode):
    mode = norm_mode(mode)
    key = state_key_for(mode)
    pids = {"worker": 0, "speak": 0}

    def _purge(st):
        pids["worker"] = int(st.get("workerPid") or 0)
        pids["speak"] = int(st.get("speakPid") or 0)
        st[key] = default_state()[key]
        st["busy"] = False
        st["phase"] = "idle"
        st["workerPid"] = 0
        st["speakPid"] = 0
        st["busySince"] = 0.0
        st["activeReqId"] = ""
        st["speakingUntil"] = 0.0
        st["statusLine"] = "MEMORY PURGED"

    update_state(_purge)
    if pids["worker"] > 0:
        _kill_pid(pids["worker"], label="clear-ask-worker")
    if pids["speak"] > 0:
        _kill_pid(pids["speak"], label="clear-speak-worker")
    return {"ok": True}


def cmd_toggle_voice():
    def _toggle(st):
        st["voiceEnabled"] = not bool(st.get("voiceEnabled", True))
        if not st.get("busy"):
            st["statusLine"] = "VOICE SYNTHESIS ON" if st["voiceEnabled"] else "VOICE MUTED"
        return st["voiceEnabled"]

    _, enabled = update_state(_toggle)
    return {"ok": True, "voiceEnabled": enabled}


def cmd_toggle_brain():
    def _cycle(st):
        cur = resolve_brain(st.get("brainMode", DEFAULT_BRAIN))["id"]
        ids = [m["id"] for m in BRAIN_MODELS]
        nxt = BRAIN_MODELS[(ids.index(cur) + 1) % len(BRAIN_MODELS)]
        st["brainMode"] = nxt["id"]
        if not st.get("busy"):
            st["statusLine"] = f"MODEL: {nxt['desc'].upper()}"
        return nxt

    _, nxt = update_state(_cycle)
    return {"ok": True, "brainMode": nxt["id"], "model": nxt["slug"]}


def cmd_live(mode, prompt_text=""):
    mode = norm_mode(mode)
    st = load_state(reconcile_save=False)
    m_spec = resolve_brain(st.get("brainMode", DEFAULT_BRAIN))
    model = m_spec["slug"]
    use_claude = m_spec.get("provider") == "claude"

    if use_claude:
        live_bin = shutil.which("claude")
        if not live_bin:
            sys.stderr.write("Error: claude CLI not found on PATH\n")
            sys.exit(1)
    else:
        live_bin = find_agy()
        if not shutil.which(live_bin) and not os.path.isfile(live_bin):
            sys.stderr.write(f"Error: agy CLI not found ({live_bin})\n")
            sys.exit(1)

    intro = (
        "You are ABADDON, the Infernal Demon JARVIS AI on this device. Stay in character as a commanding, dark-arcane, fiercely loyal demon AI with full $HOME and /sdcard power. Confirm before permanently deleting user files."
        if mode == "infernal"
        else (
            "You are ABADDON in Royal Purple Havoc Overlord mode from Snake Mountain on this device. Stay in character as a dark-arcane, razor-sharp, fiercely loyal overlord AI with full $HOME and /sdcard power. Confirm before permanently deleting user files."
            if mode == "skeletor"
            else (
                "You are ABADDON in Cyber-Mask Neon Overdrive mode on this device. Stay sharp, witty, high-voltage, and fiercely capable with full $HOME and /sdcard power. Confirm before permanently deleting user files."
                if mode == "mask"
                else "You are ULTRON, the Blue Vibranium Apex AI on this device. Stay in character as a cool, razor-sharp, futuristic synthetic intelligence with full $HOME and /sdcard power. Confirm before permanently deleting user files."
            )
        )
    )
    if (prompt_text or "").strip():
        msg = f"{intro}\n\nUser directive: {prompt_text.strip()}"
    else:
        msg = f"{intro}\n\nGreet the user briefly in character and await their command."

    args = [live_bin, "--model", model]
    if os.path.isdir("/sdcard"):
        args.extend(["--add-dir", "/sdcard"])
    if use_claude:
        # Interactive Claude Code on the Pro login: normal permission prompts, the user approves.
        args.extend(["--", msg])
    else:
        args.extend(["-i", msg])

    try:
        os.chdir(HOME)
    except OSError:
        pass
    if use_claude:
        os.execvpe(live_bin, args, _claude_env())
    os.execvp(live_bin, args)


def main():
    args = sys.argv[1:]
    if not args or args[0] == "status":
        print(json.dumps(load_state()))
    elif args[0] == "init-sfx":
        print(json.dumps(cmd_init_sfx()))
    elif args[0] == "idle":
        print(json.dumps(cmd_idle()))
    elif args[0] == "cancel":
        print(json.dumps(cmd_cancel()))
    elif args[0] == "clear" and len(args) >= 2:
        print(json.dumps(cmd_clear(args[1])))
    elif args[0] == "toggle-voice":
        print(json.dumps(cmd_toggle_voice()))
    elif args[0] == "toggle-thinking":
        print(json.dumps(cmd_toggle_thinking()))
    elif args[0] == "swarm" and len(args) >= 4:
        print(json.dumps(cmd_swarm(args[1], args[2], " ".join(args[3:]))))
    elif args[0] == "_swarm-worker" and len(args) >= 4:
        _swarm_worker(args[1], args[2], args[3], args[4] if len(args) >= 5 else "")
    elif args[0] == "toggle-brain":
        print(json.dumps(cmd_toggle_brain()))
    elif args[0] == "morph" and len(args) >= 2:
        print(json.dumps(cmd_morph(args[1])))
    elif args[0] == "speak" and len(args) >= 3:
        print(json.dumps(cmd_speak(args[1], " ".join(args[2:]))))
    elif args[0] == "speak-only" and len(args) >= 3:
        print(json.dumps(cmd_speak(args[1], " ".join(args[2:]), append_msg=False)))
    elif args[0] == "speak-cmd" and len(args) >= 4:
        print(json.dumps(cmd_speak(args[1], args[3], log_user=args[2])))
    elif args[0] == "_speak-worker" and len(args) >= 3:
        _speak_worker(args[1], args[2], args[3] if len(args) >= 4 else "")
    elif args[0] == "ask" and len(args) >= 4:
        print(json.dumps(cmd_ask(args[1], args[2], " ".join(args[3:]))))
    elif args[0] == "_ask-worker" and len(args) >= 4:
        _ask_worker(args[1], args[2], args[3], args[4] if len(args) >= 5 else "")
    elif args[0] == "scan" and len(args) >= 3:
        print(json.dumps(cmd_scan(args[1], args[2])))
    elif args[0] == "live" and len(args) >= 2:
        cmd_live(args[1], " ".join(args[2:]) if len(args) > 2 else "")
    else:
        print(json.dumps({"ok": False, "msg": f"Bad args: {args}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
