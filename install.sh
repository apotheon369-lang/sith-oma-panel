#!/bin/sh
# Sith OMA panel installer: fetches dependencies, backs up your config, installs the theme.
set -e
D=$(cd "$(dirname "$0")" && pwd)
C="$HOME/.config/omalauncher"
TS=$(date +%Y%m%d-%H%M%S)

echo "Installing dependencies (python, mpv, btop)..."
if command -v pacman >/dev/null 2>&1; then
    pacman -S --needed --noconfirm python mpv btop 2>/dev/null || sudo pacman -S --needed --noconfirm python mpv btop
else
    echo "pacman not found: ensure python, mpv, btop are installed."
fi

if [ -d "$C" ]; then
    cp -a "$C" "$C.backup-$TS"
    echo "Old config backed up -> $C.backup-$TS"
fi

mkdir -p "$C/widgets" "$C/home" "$C/style" "$C/themes/icons" "$HOME/.local/bin" \
  "$HOME/.local/share/omalauncher/synth-pads" \
  "$HOME/.local/share/omalauncher/obsidian-bridge" \
  "$HOME/.local/share/omalauncher/abaddon-ultron/sfx"

# Install widgets
cp "$D"/widgets/*.owl "$C/widgets/"

# Install layout and styles
cp "$D"/home/layout.toml "$C/home/"
cp "$D"/style/*.toml "$C/style/"
[ -f "$D/style/drawer.css" ] && cp "$D/style/drawer.css" "$C/drawer.css"

# Install themes
[ -d "$D/themes" ] && cp "$D"/themes/*.json "$C/themes/" 2>/dev/null || true
[ -d "$D/themes/icons" ] && cp "$D"/themes/icons/* "$C/themes/icons/" 2>/dev/null || true

# Install configs
cp "$D"/config/* "$C/"

# Install backend bin scripts
cp "$D"/bin/* "$HOME/.local/bin/"
chmod +x "$HOME"/.local/bin/*

# Install share assets
cp "$D"/share/synth-pads/* "$HOME/.local/share/omalauncher/synth-pads/" 2>/dev/null || true
cp "$D"/share/obsidian-bridge/* "$HOME/.local/share/omalauncher/obsidian-bridge/" 2>/dev/null || true
cp "$D"/share/abaddon-ultron/sfx/* "$HOME/.local/share/omalauncher/abaddon-ultron/sfx/" 2>/dev/null || true

# Ensure path references use current HOME
sed -i "s#~/\.local/#$HOME/.local/#g" "$C"/widgets/*.owl 2>/dev/null || true

echo "Sith OMA panel installed successfully."
echo "Undo: rm -rf $C && mv $C.backup-$TS $C"
