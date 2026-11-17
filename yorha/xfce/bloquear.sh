#!/bin/sh
# bloquear.sh — pantalla de bloqueo del escritorio de trabajo: el fondo de XFCE y un anillo
# discreto que se ve siempre (así se nota que el teclado responde). Ctrl+Alt+L y swayidle.
FONDO=/usr/share/images/desktop-base/desktop-background-nier-ligth.svg
[ -r "$FONDO" ] && imagen="-i $FONDO -s fill" || imagen=""
# shellcheck disable=SC2086
exec swaylock -f -e -F -c 2b2b2b $imagen --indicator-idle-visible \
  --indicator-radius 70 --indicator-thickness 6 \
  --inside-color 2b2b2bd9 --ring-color 9e9e9e --text-color eeeeee \
  --line-color 00000000 --separator-color 00000000 --key-hl-color eeeeee --bs-hl-color cd664d \
  --inside-clear-color 2b2b2bd9 --ring-clear-color 9e9e9e --text-clear-color eeeeee \
  --inside-ver-color 2b2b2bd9 --ring-ver-color eeeeee --text-ver-color eeeeee \
  --inside-wrong-color cd664dd9 --ring-wrong-color cd664d --text-wrong-color ffffff
