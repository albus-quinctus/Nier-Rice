#!/bin/sh
# saludo.sh — abre la pantalla de inicio (saludo.py) dentro del sway de greeter/sway.conf.
# Si saludo.py se cae, lo vuelve a abrir (hasta 5 veces seguidas). Al terminar bien (sesión
# iniciada) o tras 5 fallos, cierra este sway y greetd sigue: abre la sesión o vuelve a
# empezar. Lo que pasa queda en ~_greetd/saludo.log (/var/lib/greetd, las últimas 300 líneas).
REGISTRO="${HOME:-/nonexistent}/saludo.log"
{ true >> "$REGISTRO"; } 2>/dev/null || REGISTRO=/dev/null

apuntar() {
  echo "$(date '+%F %T') $1" >> "$REGISTRO"
}

fallos=0
while [ "$fallos" -lt 5 ]; do
  if [ "$REGISTRO" != /dev/null ]; then
    tail -n 300 "$REGISTRO" > "$REGISTRO.nuevo" && mv -f "$REGISTRO.nuevo" "$REGISTRO"
  fi
  apuntar "se abre saludo.py"
  NO_AT_BRIDGE=1 python3 -I /srv/nier/yorha/greeter/saludo.py 2>> "$REGISTRO"
  estado=$?
  apuntar "saludo.py terminó con $estado"
  [ "$estado" = 0 ] && break
  fallos=$((fallos + 1))
  sleep 1
done
exec swaymsg exit
