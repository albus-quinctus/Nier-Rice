#!/bin/sh
# vigilar.sh — mantiene vivo un programa de la sesión (la barra, swayidle, el panel de trabajo):
# si se cierra o se cae, lo vuelve a abrir mientras sway siga en marcha. Así la sesión nunca
# se queda sin bloqueo automático ni sin el aviso de batería baja.
# Uso (desde sway.conf): sh vigilar.sh PROGRAMA [ARGUMENTOS...]
# Sus errores y cada vez que se vuelve a abrir quedan en $XDG_RUNTIME_DIR/yorha-vigilar.log
# (las últimas 300 líneas; se borra al cerrar sesión).
REGISTRO="${XDG_RUNTIME_DIR:-/nonexistent}/yorha-vigilar.log"
# Sin carpeta de sesión escribible, sin registro (pero el programa se abre igual)
{ true >> "$REGISTRO"; } 2>/dev/null || REGISTRO=/dev/null

while swaymsg -t get_version >/dev/null 2>&1; do
  "$@" 2>> "$REGISTRO"
  estado=$?
  swaymsg -t get_version >/dev/null 2>&1 || break   # sway se ha cerrado: fin de la sesión
  echo "$(date '+%F %T') $* terminó con $estado; se vuelve a abrir" >> "$REGISTRO"
  if [ "$REGISTRO" != /dev/null ]; then
    tail -n 300 "$REGISTRO" > "$REGISTRO.nuevo" && mv -f "$REGISTRO.nuevo" "$REGISTRO"
  fi
  sleep 2
done
