#!/bin/sh
# obsidian-wayland.sh — hace que Obsidian (snap) abra en modo Wayland para el usuario que lo ejecuta.
# Uso, una vez y como ese usuario: sh /srv/nier/yorha/obsidian-wayland.sh
# No instala nada: copia el acceso directo de Obsidian a ~/.local/share/applications con
# --ozone-platform=wayland (sin Xwayland, sin esa opción no abre). La copia tapa al original.
set -eu
origen=/var/lib/snapd/desktop/applications/obsidian_obsidian.desktop
destino="$HOME/.local/share/applications/obsidian_obsidian.desktop"
[ -f "$origen" ] || { echo "No encuentro Obsidian ($origen). ¿Está instalado el snap?" >&2; exit 1; }
mkdir -p "$(dirname "$destino")"
sed 's|/snap/bin/obsidian\([^-]\)|/snap/bin/obsidian --ozone-platform=wayland\1|; s|/snap/bin/obsidian$|/snap/bin/obsidian --ozone-platform=wayland|' \
  "$origen" > "$destino"
grep -q -- '--ozone-platform=wayland' "$destino" || { rm -f "$destino"; echo "No he podido ajustar el acceso directo." >&2; exit 1; }
echo "Listo. Abre Obsidian con Super + Tab."
