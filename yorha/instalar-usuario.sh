#!/bin/sh
# instalar-usuario.sh — activa YoRHa para el usuario que lo ejecuta (trabajo, lilith...).
# Uso, una vez y como ese usuario: sh /srv/nier/yorha/instalar-usuario.sh [yorha|xfce]
#   yorha (o nada): el escritorio YoRHa;  xfce: el de trabajo, con el aspecto del XFCE de lilith.
# Solo crea un enlace en su ~/.config; si ya tenía una configuración de sway, la guarda.
set -eu
case "${1:-yorha}" in
  yorha) config=/srv/nier/yorha/sway.conf ;;
  xfce) config=/srv/nier/yorha/xfce/sway.conf ;;
  *) echo "uso: $0 [yorha|xfce]" >&2; exit 2 ;;
esac
destino="$HOME/.config/sway/config"
mkdir -p "$(dirname "$destino")"
if [ -e "$destino" ] && [ ! -L "$destino" ]; then
  mv "$destino" "$destino.antes-yorha"
  echo "Tu configuración anterior queda en $destino.antes-yorha"
fi
ln -sfn "$config" "$destino"
# En el escritorio de trabajo, foot con el aspecto del terminal de XFCE (si no tiene ya el suyo)
if [ "${1:-yorha}" = xfce ] && [ ! -e "$HOME/.config/foot/foot.ini" ]; then
  mkdir -p "$HOME/.config/foot"
  ln -s /srv/nier/yorha/xfce/foot.ini "$HOME/.config/foot/foot.ini"
fi
# Obsidian solo abre en Wayland con una opción extra; si no está instalado, no pasa nada
sh /srv/nier/yorha/obsidian-wayland.sh 2>/dev/null || true
# ARCHBOX y el visor como programas por defecto (carpetas, imágenes, Markdown, vídeo y audio)
sh /srv/nier/yorha/asociar.sh || true
echo "Listo. Cierra la sesión y en la pantalla de inicio elige \"YoRHa (Sway)\"."
