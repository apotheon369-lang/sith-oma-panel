# Sith OMA ☠ Panel for Omalauncher

High-performance, pure AMOLED black (`#000000` / `#050002`) Sith theme & workspace suite for [omalauncher](https://github.com/SimonSchubert/omalauncher).

## Features
- **00-Infernal Main Hub**: Pure black AMOLED zero-milliwatt power floor, zero-loop AGSL tactile hex texture, interactive voice telemetry & system reckoning.
- **Cardboard App (`widgets/cardboard.owl`)**: Standalone app (`kind app "cardboard"`) featuring an AMOLED Sith icon, multi-size card engine (S/M/L), Obsidian markdown bridge, tables, and native drawer registration.
- **Full Sith Widget Suite**:
  - `00-infernal.owl` (Main AMOLED hub face)
  - `10-sith-calendar.owl` (Chrono spectra & agenda)
  - `25-sith-command-deck.owl` & `cardboard.owl` (Obsidian canvas & card workspace)
  - `27-sith-menu.owl` (Tactical launcher quick menu)
  - `abaddon-ultron.owl` (AI entity interface & voice telemetry)
  - `cyber-range.owl` (Offensive/defensive simulation interface)
  - `downloads.owl` (Local package & download manager)
  - `drawer.owl` & `drawer.css` (Glassmorphic AMOLED app drawer)
  - `fleet-graph.owl` (Multi-agent mesh network graph)
  - `keybar.owl` (Ergonomic thumb-accessible navigation bar)
  - `nexus.owl` (Central coordination deck)
  - `notifs-board.owl` (Integrated notifications aggregator)
  - `sith-music-panel.owl` (Lord Maul swipe-down music panel)
  - `theme-forge.owl` (Dynamic live token & theme editor)
- **Backend Automation Scripts**: Complete Python automation suite in `bin/` (`owl-hub`, `abaddon-ultron.py`, `chaos-media.py`, `sith-theme.py`, `theme-forge.py`, etc.).
- **Themes & Palettes**: Curated themes in `themes/` (`aether-prism`, `hulk-gamma`, `pluto-faust`) with bespoke SVG icons.
- **Audio Synthesizer**: 5 tactile low-latency synth pads and Abaddon/Ultron SFX audio in `share/`.

## Installation

Inside the Omalauncher Linux terminal:

```bash
git clone https://github.com/apotheon369-lang/sith-oma-panel.git
cd sith-oma-panel
sh install.sh
```

The installer backs up your existing configuration to `~/.config/omalauncher.backup-<timestamp>`, copies all widgets, backend scripts, themes, and audio assets, and configures the pure AMOLED interface.

## Customization
- Switch themes via the **Forge** widget or `owl-hub`.
- FX can be toggled in `~/.config/omalauncher/animations.json` or through the HUD controls.
