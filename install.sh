#!/bin/sh
# Sith OMA panel installer: fetches dependencies, backs up your config, installs the theme.
set -e
D=$(cd "$(dirname "$0")" && pwd)
C="$HOME/.config/omalauncher"; TS=$(date +%Y%m%d-%H%M%S)
echo "Installing dependencies (python, mpv, btop)..."
if command -v pacman >/dev/null; then pacman -S --needed --noconfirm python mpv btop || sudo pacman -S --needed --noconfirm python mpv btop
else echo "pacman not found: install python, mpv, btop yourself."; fi
[ -d "$C" ] && cp -a "$C" "$C.backup-$TS" && echo "Old config backed up -> $C.backup-$TS"
mkdir -p "$C/widgets" "$C/home" "$C/style" "$HOME/.local/bin" \
  "$HOME/.local/share/omalauncher/synth-pads" "$HOME/.local/share/omalauncher/obsidian-bridge"
cp "$D"/widgets/*.owl "$C/widgets/"
cp "$D"/home/layout.toml "$C/home/"
cp "$D"/style/*.toml "$C/style/"
cp "$D"/config/* "$C/"
cp "$D"/bin/*.py "$HOME/.local/bin/" && chmod +x "$HOME"/.local/bin/*.py
cp "$D"/share/synth-pads/* "$HOME/.local/share/omalauncher/synth-pads/"
cp "$D"/share/obsidian-bridge/* "$HOME/.local/share/omalauncher/obsidian-bridge/"
sed -i "s#~/\.local/#$HOME/.local/#g" "$C"/widgets/*.owl
echo "Sith OMA installed. Undo: rm -rf $C && mv $C.backup-$TS $C"
