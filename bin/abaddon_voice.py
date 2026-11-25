#!/usr/bin/env python3
"""ABADDON // ULTRON — Voice TTS, Phase-Locked DSP, Phrase Cache & Cinematic SFX Engine.

Public API for abaddon-ultron.py:
  - clean_speech_text(text, max_chars=380) -> str
  - synthesize_voice(text, mode, voice_dir=DEFAULT_VOICE_DIR, cache_key=None) -> (wav_path, duration_sec)
  - init_sfx(sfx_dir=DEFAULT_SFX_DIR) -> {"ok": True, "sfxDir": sfx_dir, "files": [...]}
  - get_wav_duration(path) -> float
  - prune_old_voice_files(voice_dir=DEFAULT_VOICE_DIR, keep=6) -> None
  - norm_mode(mode) -> "infernal" | "future"
"""

import asyncio
import hashlib
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import wave

HOME = os.path.expanduser("~")
DATA_DIR = os.path.join(HOME, ".local", "share", "omalauncher", "abaddon-ultron")
DEFAULT_SFX_DIR = os.path.join(DATA_DIR, "sfx")
DEFAULT_VOICE_DIR = os.path.join(DATA_DIR, "voice")

# Version tag baked into phrase cache keys so DSP/voice upgrades automatically invalidate stale clips
DSP_CACHE_VERSION = "v2"
MAX_AUTO_CACHE_CHARS = 160
MAX_CACHED_CLIPS = 32
DEFAULT_MAX_SPEECH_CHARS = 380

# Exact phase-locked DSP filter graphs (44.1 kHz mono internal processing -> 24 kHz mono output).
# Abaddon: both layers locked to exact effective speed 0.960000:
#   main: 0.84 * (0.96 / 0.84) = 0.84 * 1.1428571429 = 0.96
#   sub : 0.64 * (0.96 / 0.64) = 0.64 * 1.5000000000 = 0.96
ABADDON_FILTER_COMPLEX = (
    "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[a1][a2];"
    "[a1]asetrate=44100*0.84,aresample=44100,atempo=1.1428571429,"
    "highpass=f=55,equalizer=f=110:t=q:w=1.2:g=4.5,equalizer=f=2800:t=q:w=1.4:g=3.5[main];"
    "[a2]asetrate=44100*0.64,aresample=44100,atempo=1.5,"
    "highpass=f=45,lowpass=f=540,volume=0.46[sub];"
    "[main][sub]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
    "aecho=0.84:0.74:32|68:0.24|0.12,"
    "acompressor=threshold=-22dB:ratio=3.2:attack=6:release=110:makeup=9dB,"
    "alimiter=limit=0.93:attack=2:release=40,"
    "afade=t=in:d=0.012[out]"
)

# Ultron: both layers locked to exact effective speed 1.000000:
#   core : 0.95 * (1.0 / 0.95) = 0.95 * 1.0526315789 = 1.00
#   metal: 0.91 * (1.0 / 0.91) = 0.91 * 1.0989010989 = 1.00
ULTRON_FILTER_COMPLEX = (
    "[0:a]aformat=sample_rates=44100:channel_layouts=mono,asplit=2[u1][u2];"
    "[u1]asetrate=44100*0.95,aresample=44100,atempo=1.0526315789,"
    "highpass=f=105,equalizer=f=1900:t=q:w=1.3:g=3.5,equalizer=f=4200:t=q:w=1.6:g=2.5[core];"
    "[u2]asetrate=44100*0.91,aresample=44100,atempo=1.0989010989,"
    "flanger=delay=4:depth=2.4:regen=30:width=65:speed=1.8:shape=sinusoidal,volume=0.38[metal];"
    "[core][metal]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,"
    "aecho=0.88:0.84:13|27:0.19|0.10,"
    "acompressor=threshold=-22dB:ratio=3.2:attack=5:release=95:makeup=9dB,"
    "alimiter=limit=0.93:attack=2:release=40,"
    "afade=t=in:d=0.012[out]"
)

# Precompiled cleaning patterns
_RE_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_RE_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_RE_URL = re.compile(r"\b(?:https?://|ftp://|www\.)\S+", re.IGNORECASE)
_RE_FILE_PATH = re.compile(
    r"(?:~|/sdcard|/storage|/home|/usr|/etc|/var|/tmp|/data|/opt|/bin|/proc|/sys)(?:/[^\s,;)'\"`<>]+)+"
)
_RE_BARE_ROOT_PATH = re.compile(r"(?<!\w)/(?:root|sdcard|storage|home|tmp|etc|var)(?!/\S)")
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_MD_HEADER = re.compile(r"(?m)^\s*#{1,6}\s+")
_RE_MD_HR = re.compile(r"(?m)^\s*[-*_]{3,}\s*$")
_RE_MD_QUOTE = re.compile(r"(?m)^\s*>\s*")
_RE_MD_BULLET = re.compile(r"(?m)^\s*(?:[-*+•▸▹▪▫]|\d+[.)])\s+")
_RE_EMOJI_SYMBOLS = re.compile(
    r"[\U00010000-\U0010FFFF\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF"
    r"⛧⬡◈✦⚡🔥💀🤖🎤🔊🔇✕⇄☾▸▹▪▫•◌●■□▲△◆◇★☆]+"
)
_RE_MD_DECOR = re.compile(r"[*_~#^|\\<>]+")
_RE_SPACE = re.compile(r"\s+")


def norm_mode(mode):
    return "future" if str(mode or "").strip().lower() in ("future", "blue", "ultron") else "infernal"


def _path_to_spoken(match):
    raw = match.group(0).rstrip(".,:;!?")
    base = os.path.basename(raw.rstrip("/"))
    if not base:
        return "that directory"
    base = base.lstrip(".")
    # Keep short readable filenames (e.g. hub.json), simplify noisy dotfiles/hashes
    if len(base) > 32:
        return "that file"
    return base.replace("_", " ").replace("-", " ")


def clean_speech_text(text, max_chars=DEFAULT_MAX_SPEECH_CHARS):
    """Strip markdown, URLs, file paths, code blocks, and emojis for crisp cinematic TTS."""
    if not text:
        return ""
    t = str(text)

    # 1) Code blocks, markdown links/images, and raw URLs
    t = _RE_CODE_FENCE.sub(" code block omitted. ", t)
    t = _RE_MD_IMAGE.sub(r" \1 ", t)
    t = _RE_MD_LINK.sub(r"\1", t)
    t = _RE_URL.sub(" ", t)

    # 2) File paths -> readable basename instead of "slash root slash dot config..."
    t = _RE_FILE_PATH.sub(_path_to_spoken, t)
    t = _RE_BARE_ROOT_PATH.sub(lambda m: m.group(0).lstrip("/"), t)

    # 3) Markdown structural markers & inline code
    t = _RE_INLINE_CODE.sub(r"\1", t)
    t = _RE_MD_HR.sub(" ", t)
    t = _RE_MD_HEADER.sub("", t)
    t = _RE_MD_QUOTE.sub("", t)
    t = _RE_MD_BULLET.sub("", t)

    # 4) Natural spoken substitutions for common telemetry / shorthand tokens
    t = t.replace("//", " ")
    t = re.sub(r"\s*(?:->|=>|→)\s*", " to ", t)
    t = re.sub(r"\s*[—–]\s*", ", ", t)
    t = re.sub(r"(\d)\s*°\s*[Cc]\b", r"\1 degrees Celsius", t)
    t = re.sub(r"(\d)\s*°\s*[Ff]\b", r"\1 degrees Fahrenheit", t)
    t = re.sub(r"(\d)\s*°", r"\1 degrees", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"\s+&\s+", " and ", t)

    # 5) Strip emojis, decorative glyphs, and leftover markdown formatting
    t = _RE_EMOJI_SYMBOLS.sub(" ", t)
    t = _RE_MD_DECOR.sub(" ", t)

    # 6) Normalize newlines -> pauses & clean redundant punctuation/whitespace
    t = re.sub(r"[\r\n]+", ". ", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([.!?])(?:\s*[.!?])+", r"\1", t)
    t = re.sub(r",\s*,+", ", ", t)
    t = _RE_SPACE.sub(" ", t).strip(" ,;:-")

    if not t:
        return ""

    # 7) Smart sentence-boundary cap so synthesis stays fast and natural
    if max_chars and len(t) > max_chars:
        cut = t[:max_chars]
        last_sent = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        if last_sent >= int(max_chars * 0.38):
            t = cut[: last_sent + 1].strip()
        else:
            last_pause = max(cut.rfind(","), cut.rfind(";"), cut.rfind(" "))
            if last_pause >= int(max_chars * 0.5):
                t = cut[:last_pause].rstrip(" ,;:-") + "."
            else:
                t = cut.rstrip(" ,;:-") + "."

    return t


def get_wav_duration(path):
    """Read exact duration of a WAV file in seconds."""
    try:
        with wave.open(path, "rb") as wf:
            sr = float(wf.getframerate() or 24000)
            return round(wf.getnframes() / sr, 2)
    except Exception:
        return 0.0


def _is_valid_wav(path, min_bytes=512):
    if not path or not os.path.isfile(path) or os.path.getsize(path) < min_bytes:
        return False
    try:
        with wave.open(path, "rb") as wf:
            return wf.getnframes() > 0 and wf.getframerate() > 0
    except Exception:
        return False


def prune_old_voice_files(voice_dir=DEFAULT_VOICE_DIR, keep=6):
    """Prune old ephemeral WAV files in voice_dir while preserving cache/ and temp files."""
    try:
        if not os.path.isdir(voice_dir):
            return
        files = []
        for fn in os.listdir(voice_dir):
            if not fn.endswith(".wav") or fn.startswith("raw_") or fn.startswith("."):
                continue
            p = os.path.join(voice_dir, fn)
            if os.path.isfile(p):
                files.append(p)
        files.sort(key=lambda p: os.path.getmtime(p))
        for old in files[:-max(1, keep)]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def _prune_cache(cache_dir, keep=MAX_CACHED_CLIPS):
    try:
        if not os.path.isdir(cache_dir):
            return
        files = [
            os.path.join(cache_dir, fn)
            for fn in os.listdir(cache_dir)
            if fn.endswith(".wav") and not fn.startswith(".")
        ]
        if len(files) <= keep:
            return
        files.sort(key=lambda p: os.path.getmtime(p))
        for old in files[:-keep]:
            try:
                os.remove(old)
            except OSError:
                pass
    except OSError:
        pass


def _resolve_cache_path(voice_dir, mode, clean_text, cache_key=None):
    """Return a deterministic cache file path for explicit cache_key or short reusable phrases."""
    if cache_key:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(cache_key)).strip("_")[:48]
        digest = hashlib.sha256(f"{DSP_CACHE_VERSION}|{mode}|{clean_text}".encode("utf-8")).hexdigest()[:8]
        name = f"{mode}_{safe}_{digest}.wav" if safe else f"{mode}_{digest}.wav"
    elif len(clean_text) <= MAX_AUTO_CACHE_CHARS:
        digest = hashlib.sha256(f"{DSP_CACHE_VERSION}|{mode}|{clean_text}".encode("utf-8")).hexdigest()[:14]
        name = f"{mode}_{digest}.wav"
    else:
        return None
    cache_dir = os.path.join(voice_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, name)


def _synth_edge_tts(clean_text, mode, raw_mp3):
    """Generate neural TTS via edge_tts using forced IPv4 (avoids IPv6 ConnectionResetError on Bing)."""
    try:
        import aiohttp
        import edge_tts
    except Exception:
        return False

    voice_name = "en-GB-RyanNeural" if mode == "infernal" else "en-US-ChristopherNeural"
    rate = "-6%" if mode == "infernal" else "+4%"
    pitch = "-12Hz" if mode == "infernal" else "-4Hz"

    async def _attempt():
        # Force AF_INET (IPv4): Bing's IPv6 endpoint frequently resets TLS on mobile/proot networks.
        connector = aiohttp.TCPConnector(family=socket.AF_INET)
        try:
            comm = edge_tts.Communicate(
                clean_text,
                voice_name,
                rate=rate,
                pitch=pitch,
                connector=connector,
                connect_timeout=5,
                receive_timeout=10,
            )
            await asyncio.wait_for(comm.save(raw_mp3), timeout=11.0)
            return os.path.isfile(raw_mp3) and os.path.getsize(raw_mp3) > 512
        finally:
            await connector.close()

    async def _run_with_retry():
        deadline = time.monotonic() + 14.5
        for attempt in range(2):
            if time.monotonic() >= deadline:
                break
            try:
                if await _attempt():
                    return True
            except Exception:
                pass
            if attempt == 0 and time.monotonic() + 3.0 < deadline:
                await asyncio.sleep(0.2)
        return False

    try:
        return asyncio.run(_run_with_retry())
    except Exception:
        return False


def _synth_espeak(clean_text, mode, out_wav):
    """Instant 100% offline fallback TTS via espeak-ng."""
    espeak = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak:
        return False
    try:
        if mode == "infernal":
            cmd = [espeak, "-v", "en-gb+m3", "-p", "22", "-s", "142", "-w", out_wav, clean_text]
        else:
            cmd = [espeak, "-v", "en-us+m2", "-p", "36", "-s", "162", "-w", out_wav, clean_text]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        return _is_valid_wav(out_wav, min_bytes=256)
    except Exception:
        return False


def _apply_dsp_ffmpeg(src_audio, mode, out_wav_tmp):
    """Apply phase-locked Abaddon / Ultron DSP filter graph via ffmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    fc = ABADDON_FILTER_COMPLEX if mode == "infernal" else ULTRON_FILTER_COMPLEX
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", src_audio,
                "-filter_complex", fc,
                "-map", "[out]",
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                out_wav_tmp,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=14,
        )
        if proc.returncode == 0 and _is_valid_wav(out_wav_tmp):
            return True
    except Exception:
        pass

    # Fallback: plain decode/resample without complex filter graph
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y", "-loglevel", "error",
                "-i", src_audio,
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                out_wav_tmp,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        return proc.returncode == 0 and _is_valid_wav(out_wav_tmp)
    except Exception:
        return False


def synthesize_voice(text, mode, voice_dir=DEFAULT_VOICE_DIR, cache_key=None):
    """Render spoken text into a signature phase-locked DSP WAV file for Abaddon or Ultron.

    Returns (wav_path, duration_sec), or ("", 0.0) only if all TTS engines fail.
    """
    clean = clean_speech_text(text)
    if not clean:
        return "", 0.0

    mode = norm_mode(mode)
    os.makedirs(voice_dir, exist_ok=True)

    # 0) Check phrase cache for instant zero-latency playback
    cache_path = _resolve_cache_path(voice_dir, mode, clean, cache_key=cache_key)
    if cache_path and _is_valid_wav(cache_path):
        try:
            os.utime(cache_path, None)
        except OSError:
            pass
        return cache_path, get_wav_duration(cache_path)

    stamp = f"{int(time.time() * 1000)}_{os.getpid()}"
    raw_mp3 = os.path.join(voice_dir, f".raw_{stamp}.mp3")
    raw_wav = os.path.join(voice_dir, f".raw_{stamp}.wav")
    final_wav = cache_path if cache_path else os.path.join(voice_dir, f"{mode}_{stamp}.wav")
    tmp_wav = final_wav + f".{os.getpid()}.tmp.wav"

    src_audio = None
    used_neural = False

    try:
        # 1) Neural Microsoft TTS (forced IPv4 + bounded retry)
        if _synth_edge_tts(clean, mode, raw_mp3):
            src_audio = raw_mp3
            used_neural = True

        # 2) Instant offline espeak-ng fallback
        if not src_audio and _synth_espeak(clean, mode, raw_wav):
            src_audio = raw_wav

        if not src_audio:
            return "", 0.0

        # 3) Apply phase-locked DSP via ffmpeg
        rendered = _apply_dsp_ffmpeg(src_audio, mode, tmp_wav)

        # 4) Resilience fallback if ffmpeg is missing or failed:
        #    - reuse raw_wav if espeak-ng already produced one
        #    - otherwise synthesize directly with espeak-ng so we never return silence
        if not rendered:
            if src_audio == raw_wav and _is_valid_wav(raw_wav, min_bytes=256):
                shutil.copy2(raw_wav, tmp_wav)
                rendered = _is_valid_wav(tmp_wav, min_bytes=256)
            elif _synth_espeak(clean, mode, tmp_wav):
                rendered = True
                used_neural = False

        if not rendered:
            return "", 0.0

        # Only store in long-term cache if we didn't fall back to espeak when neural was wanted?
        # If neural failed transiently, still play the clip, just put it as ephemeral so next time
        # neural gets a fresh chance to populate the cache!
        if cache_path and not used_neural and shutil.which("ffmpeg"):
            final_wav = os.path.join(voice_dir, f"{mode}_{stamp}.wav")

        os.replace(tmp_wav, final_wav)
        if cache_path and final_wav == cache_path:
            _prune_cache(os.path.dirname(cache_path))
        else:
            prune_old_voice_files(voice_dir)

        return final_wav, get_wav_duration(final_wav)
    finally:
        for tmp in (raw_mp3, raw_wav, tmp_wav):
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def _apply_edge_fades(samples, sr, fade_in_ms=12.0, fade_out_ms=35.0):
    """Apply smooth raised-cosine (Hann) fade-in and fade-out to eliminate start/end clicks."""
    n = len(samples)
    if n == 0:
        return samples
    fi = min(n // 2, max(1, int(sr * fade_in_ms / 1000.0)))
    fo = min(n // 2, max(1, int(sr * fade_out_ms / 1000.0)))
    for i in range(fi):
        g = 0.5 * (1.0 - math.cos(math.pi * i / fi))
        samples[i] *= g
    for i in range(fo):
        idx = n - 1 - i
        g = 0.5 * (1.0 - math.cos(math.pi * i / fo))
        samples[idx] *= g
    samples[0] = 0.0
    samples[-1] = 0.0
    return samples


def write_mono_wav(path, samples, sr=24000, target_peak=0.87):
    """Normalize and atomically write 16-bit PCM mono WAV."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    peak = max((abs(s) for s in samples), default=1.0)
    scale = target_peak / max(peak, 1e-6)
    pcm = bytearray(len(samples) * 2)
    pack_into = struct.pack_into
    for i, s in enumerate(samples):
        v = max(-1.0, min(1.0, s * scale))
        pack_into("<h", pcm, i * 2, int(round(v * 32767.0)))
    tmp_path = f"{path}.{os.getpid()}.tmp"
    try:
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm)
        os.replace(tmp_path, path)
    finally:
        if os.path.isfile(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def init_sfx(sfx_dir=DEFAULT_SFX_DIR):
    """Generate crisp, click-free, phase-continuous cinematic SFX for Abaddon and Ultron."""
    os.makedirs(sfx_dir, exist_ok=True)
    sr = 24000
    two_pi = 2.0 * math.pi
    created = []

    # 1. abaddon_wake.wav (0.95s): Abyssal sub-drop + dark fifth drone + ember harmonic shimmer
    dur = 0.95
    n = int(sr * dur)
    buf = [0.0] * n
    ph_sub = 0.0
    ph_fifth = 0.0
    ph_third = 0.0
    ph_ember = 0.0
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.055) * math.pi * 0.5) * math.exp(-t * 2.75)
        # True exponential pitch glide 86 Hz -> 46 Hz via phase accumulation
        f0 = 46.0 + 40.0 * math.exp(-t * 3.2)
        ph_sub += two_pi * f0 / sr
        ph_fifth += two_pi * (f0 * 1.498) / sr
        ph_third += two_pi * (f0 * 3.0) / sr
        ph_ember += two_pi * (460.0 + 90.0 * math.exp(-t * 5.0)) / sr
        s = (
            0.58 * math.sin(ph_sub)
            + 0.30 * math.sin(ph_fifth + 0.32 * math.sin(two_pi * 4.5 * t))
            + 0.16 * math.sin(ph_third) * math.exp(-t * 4.2)
            + 0.08 * math.sin(ph_ember) * math.exp(-t * 7.0)
        )
        buf[i] = math.tanh(s * 1.55) * env
    _apply_edge_fades(buf, sr, fade_in_ms=10.0, fade_out_ms=65.0)
    p = os.path.join(sfx_dir, "abaddon_wake.wav")
    write_mono_wav(p, buf, sr, target_peak=0.87)
    created.append(p)

    # 2. ultron_wake.wav (0.78s): Sleek ascending vibranium sweep + crystalline Stark triad lock
    dur = 0.78
    n = int(sr * dur)
    buf = [0.0] * n
    ph_sweep = 0.0
    ph_sub = 0.0
    for i in range(n):
        t = i / sr
        env = math.sin(min(1.0, t / 0.03) * math.pi * 0.5) * math.exp(-t * 3.35)
        f_sw = 280.0 + 540.0 * (1.0 - math.exp(-t * 7.8))
        ph_sweep += two_pi * f_sw / sr
        ph_sub += two_pi * 165.0 / sr
        # Smooth Gaussian-like harmonic lock chimes at 0.16s and 0.31s
        c1 = math.exp(-((t - 0.16) / 0.065) ** 2) * math.sin(two_pi * 660.0 * t)
        c2 = math.exp(-((t - 0.31) / 0.085) ** 2) * math.sin(two_pi * 990.0 * t)
        c3 = math.exp(-t * 5.2) * math.sin(two_pi * 1320.0 * t)
        s = (
            0.38 * math.sin(ph_sweep)
            + 0.28 * c1
            + 0.32 * c2
            + 0.16 * c3
            + 0.16 * math.sin(ph_sub)
        )
        buf[i] = math.tanh(s * 1.15) * env
    _apply_edge_fades(buf, sr, fade_in_ms=8.0, fade_out_ms=55.0)
    p = os.path.join(sfx_dir, "ultron_wake.wav")
    write_mono_wav(p, buf, sr, target_peak=0.87)
    created.append(p)

    # 3. abaddon_listen.wav (0.24s): Tight obsidian twin-pulse (continuous phase glide, zero clicks)
    dur = 0.24
    n = int(sr * dur)
    buf = [0.0] * n
    ph1 = 0.0
    ph2 = 0.0
    for i in range(n):
        t = i / sr
        # Smooth sigmoid transition from 210 Hz -> 148 Hz centered at 0.095s (no discontinuity!)
        blend = 1.0 / (1.0 + math.exp(-(t - 0.095) * 95.0))
        f = 210.0 * (1.0 - blend) + 148.0 * blend
        ph1 += two_pi * f / sr
        ph2 += two_pi * (f * 1.498) / sr
        p1 = math.exp(-((t - 0.048) / 0.036) ** 2)
        p2 = math.exp(-((t - 0.145) / 0.052) ** 2)
        env = 0.85 * p1 + 1.0 * p2
        s = 0.65 * math.sin(ph1) + 0.35 * math.sin(ph2)
        buf[i] = math.tanh(s * 1.4) * env
    _apply_edge_fades(buf, sr, fade_in_ms=6.0, fade_out_ms=28.0)
    p = os.path.join(sfx_dir, "abaddon_listen.wav")
    write_mono_wav(p, buf, sr, target_peak=0.84)
    created.append(p)

    # 4. ultron_listen.wav (0.22s): Crisp JARVIS dual harmonic ping (smooth Gaussian envelopes, zero click)
    dur = 0.22
    n = int(sr * dur)
    buf = [0.0] * n
    for i in range(n):
        t = i / sr
        e1 = math.exp(-((t - 0.042) / 0.024) ** 2)
        e2 = math.exp(-((t - 0.125) / 0.038) ** 2)
        s1 = e1 * (0.80 * math.sin(two_pi * 880.0 * t) + 0.20 * math.sin(two_pi * 1760.0 * t))
        s2 = e2 * (0.82 * math.sin(two_pi * 1318.5 * t) + 0.18 * math.sin(two_pi * 2637.0 * t))
        buf[i] = 0.78 * s1 + 0.90 * s2
    _apply_edge_fades(buf, sr, fade_in_ms=6.0, fade_out_ms=26.0)
    p = os.path.join(sfx_dir, "ultron_listen.wav")
    write_mono_wav(p, buf, sr, target_peak=0.84)
    created.append(p)

    return {"ok": True, "sfxDir": sfx_dir, "files": created}


if __name__ == "__main__":
    import json

    cmd = sys.argv[1] if len(sys.argv) > 1 else "init-sfx"
    if cmd == "init-sfx":
        print(json.dumps(init_sfx()))
    elif cmd == "speak" and len(sys.argv) >= 4:
        path, dur = synthesize_voice(" ".join(sys.argv[3:]), sys.argv[2])
        print(json.dumps({"ok": bool(path), "path": path, "dur": dur}))
    else:
        print(json.dumps({"ok": False, "usage": "abaddon_voice.py [init-sfx | speak <mode> <text>]"}))
        sys.exit(1)
