#!/bin/sh
# asociar.sh — ARCHBOX, el visor, Obsidian y mpv como programas por defecto del usuario que lo ejecuta.
# Uso, como ese usuario (lo llama también instalar-usuario.sh): sh /srv/nier/yorha/asociar.sh
# No instala nada: enlaza los .desktop de ARCHBOX y del visor en ~/.local/share/applications y
# escribe los programas por defecto en ~/.config/mimeapps.list con «gio mime».
# Lo que no está instalado (mpv, Obsidian) se salta.
set -eu
apps="$HOME/.local/share/applications"
mkdir -p "$apps"
ln -sfn /srv/nier/yorha/archbox/archbox.desktop "$apps/archbox.desktop"
ln -sfn /srv/nier/yorha/visor.desktop "$apps/visor.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$apps" 2>/dev/null || true

# por_defecto APLICACIÓN TIPO...: solo si esa aplicación existe
por_defecto() {
  app=$1; shift
  for tipo in "$@"; do
    gio mime "$tipo" "$app" >/dev/null 2>&1 || { echo "Sin $app: me salto el resto." >&2; return 0; }
  done
}
por_defecto archbox.desktop inode/directory
# Las imágenes que lee el visor (las de su .desktop)
tipos=$(sed -n 's/^MimeType=//p' /srv/nier/yorha/visor.desktop | tr ';' ' ')
# shellcheck disable=SC2086
por_defecto visor.desktop $tipos
por_defecto obsidian_obsidian.desktop text/markdown text/x-markdown
por_defecto mpv.desktop video/mp4 video/x-matroska video/webm video/x-msvideo video/quicktime video/mpeg video/ogg \
  audio/mpeg audio/mp4 audio/ogg audio/flac audio/x-flac audio/x-wav audio/x-vorbis+ogg audio/opus audio/aac
echo "Listo: carpetas en ARCHBOX, imágenes en el visor, Markdown en Obsidian y vídeo y audio en mpv."
