#!/usr/bin/env python3
"""Render 5 resonant cyber-synth beat pad samples directly through Odin2.vst3."""

import os
import wave
import numpy as np
import pedalboard

VST_PATH = "/usr/lib/vst3/Odin2.vst3"
OUT_DIR = os.path.expanduser("~/.local/share/omalauncher/synth-pads")
SAMPLE_RATE = 44100

# C minor pentatonic across the 5 colored plates (Green, Yellow, Red, Pink, Blue)
# (midi_note, velocity, filter_cutoff_hz, resonance)
PADS = [
    (48, 118, 520.0, 0.36),  # 0: Green  (C3 + C4 shimmer)
    (51, 118, 640.0, 0.38),  # 1: Yellow (Eb3 + Eb4)
    (53, 120, 740.0, 0.42),  # 2: Red    (F3 + F4)
    (55, 118, 860.0, 0.40),  # 3: Pink   (G3 + G4)
    (58, 120, 980.0, 0.38),  # 4: Blue   (Bb3 + Bb4)
]


def set_param(plugin, name, value):
    if name in plugin.parameters:
        setattr(plugin, name, value)


def render_pad(idx, midi_note, velocity, cutoff_hz, resonance):
    plugin = pedalboard.load_plugin(VST_PATH)

    # Rich 3-oscillator detuned cyber-synth patch in Odin2
    set_param(plugin, "osc1_octave", 0.0)
    set_param(plugin, "osc1_finetune", -6.0)
    set_param(plugin, "osc1_volume", 0.0)
    set_param(plugin, "osc1_detune", 0.25)

    set_param(plugin, "osc2_octave", 1.0)
    set_param(plugin, "osc2_finetune", 7.0)
    set_param(plugin, "osc2_volume", -3.5)
    set_param(plugin, "osc2_detune", 0.30)

    set_param(plugin, "osc3_octave", 0.0)
    set_param(plugin, "osc3_semitones", 7.0)  # subtle fifth harmonic glow
    set_param(plugin, "osc3_finetune", 4.0)
    set_param(plugin, "osc3_volume", -11.0)

    # Unison width for lush stereo spread
    set_param(plugin, "unison_detune", 0.14)
    set_param(plugin, "unison_width", 0.78)

    # Punchy resonant filter sweep
    set_param(plugin, "filter1_osc1_input", 1.0)
    set_param(plugin, "filter1_osc2_input", 1.0)
    set_param(plugin, "filter1_osc3_input", 1.0)
    set_param(plugin, "filter1_frequency", cutoff_hz)
    set_param(plugin, "filter1_resonance", resonance)
    set_param(plugin, "filter1_saturation", 0.24)
    set_param(plugin, "filter1_envelope_amount", 0.68)
    set_param(plugin, "filter1_keyboard_follow", 0.35)

    # Snappy beat-pad amp + filter envelopes
    set_param(plugin, "ampenv_attack", 0.002)
    set_param(plugin, "ampenv_decay", 0.38)
    set_param(plugin, "ampenv_sustain", 0.16)
    set_param(plugin, "ampenv_release", 0.42)

    set_param(plugin, "filterenv_attack", 0.002)
    set_param(plugin, "filterenv_decay", 0.26)
    set_param(plugin, "filterenv_sustain", 0.10)
    set_param(plugin, "filterenv_release", 0.30)

    # Subtle chorus + dark hall reverb inside Odin2
    set_param(plugin, "chorus_on", 1.0)
    set_param(plugin, "chorus_rate", 0.35)
    set_param(plugin, "chorus_amount", 0.35)
    set_param(plugin, "chorus_drywet", 0.28)

    set_param(plugin, "reverb_on", 1.0)
    set_param(plugin, "reverb_mid_hall", 1.5)
    set_param(plugin, "reverb_high_freq_damp", 7500.0)
    set_param(plugin, "reverb_drywet", 0.22)

    set_param(plugin, "master", -2.0)

    # Trigger MIDI note on at 0.0s, note off at 0.22s, capture 0.85s tail
    msgs = [
        ([0x90, midi_note, velocity], 0.0),
        ([0x80, midi_note, 0], 0.22),
    ]
    audio = plugin(msgs, duration=0.85, sample_rate=SAMPLE_RATE)

    # Normalize cleanly to -1.5 dBFS and apply gentle 25ms tail fade to avoid clicks
    peak = float(np.max(np.abs(audio)))
    if peak > 1e-6:
        audio = audio * (0.84 / peak)

    fade_n = int(SAMPLE_RATE * 0.025)
    fade = np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
    audio[:, -fade_n:] *= fade

    pcm = np.clip(audio.T, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype(np.int16)

    out_path = os.path.join(OUT_DIR, f"pad-{idx}.wav")
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(pcm16.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm16.tobytes())

    rms = float(np.sqrt(np.mean(audio ** 2)))
    print(f"pad-{idx}.wav: note={midi_note} peak={float(np.max(np.abs(audio))):.3f} rms={rms:.3f} size={os.path.getsize(out_path)}B")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    for idx, (note, vel, cutoff, res) in enumerate(PADS):
        render_pad(idx, note, vel, cutoff, res)


if __name__ == "__main__":
    main()
