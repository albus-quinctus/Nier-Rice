#!/bin/sh
# bloquear.sh — pantalla de bloqueo YoRHa: el fondo del tema actual y un anillo en sus colores
# que se ve siempre, así se nota que el teclado responde. Lo usan Mod+l, swayidle y el panel.
YORHA=$(dirname "$(readlink -f "$0")")
tema=$(cat "${XDG_RUNTIME_DIR:-/nonexistent}/yorha-tema" 2>/dev/null || echo claro)
if [ "$tema" = oscuro ]; then
  fondo=57544a; tinta=dad4bb
else
  tema=claro; fondo=c2bda6; tinta=48463d
fi
exec swaylock -f -e -F -c "$fondo" -i "$YORHA/fondos/yorha-$tema.png" -s fill \
  --font "FOT-Rodin Pro" --indicator-idle-visible \
  --indicator-radius 80 --indicator-thickness 8 \
  --inside-color "${fondo}e6" --ring-color "$tinta" --text-color "$tinta" \
  --line-color 00000000 --separator-color 00000000 \
  --key-hl-color cd664d --bs-hl-color "$fondo" \
  --inside-clear-color "${fondo}e6" --ring-clear-color "$tinta" --text-clear-color "$tinta" \
  --inside-ver-color "${fondo}e6" --ring-ver-color "$tinta" --text-ver-color "$tinta" \
  --inside-wrong-color cd664de6 --ring-wrong-color cd664d --text-wrong-color f4f0e1
