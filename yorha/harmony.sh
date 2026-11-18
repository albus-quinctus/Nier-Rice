#!/bin/sh
# harmony.sh — muestra u oculta Harmony Music. Vive en el scratchpad de sway, así que
# no ocupa ningún escritorio. Si no está abierto, lo arranca (la regla de sway.conf lo
# manda al scratchpad y lo muestra). "harmony.sh cerrar" lo cierra del todo.
# El panel (ajustes.py) usa además "lateral X Y ANCHO ALTO" (lo coloca a la derecha del
# menú, abriéndolo si hace falta) y "ocultar" (lo devuelve al scratchpad).
# Los errores quedan en $XDG_RUNTIME_DIR/yorha-harmony.log
exec 2>>"${XDG_RUNTIME_DIR:-/tmp}/yorha-harmony.log"
APP='[app_id="harmonymusic"]'
BIN="$HOME/Aplicaciones/harmonymusic/harmonymusic"
VACIO=harmony
VOLVER="${XDG_RUNTIME_DIR:-/tmp}/yorha-harmony-escritorio"
abierta() { pgrep -u "$(id -u)" -x harmonymusic >/dev/null; }

# Dónde está la ventana: oculta (en el scratchpad), enfocada, otra, o ninguna
estado() {
  swaymsg -t get_tree | python3 -I -c '
import json, sys
def buscar(n, ws=None):
    if n.get("type") == "workspace":
        ws = n.get("name")
    if n.get("app_id") == "harmonymusic":
        return ws, n
    for h in n.get("nodes", []) + n.get("floating_nodes", []):
        r = buscar(h, ws)
        if r:
            return r
r = buscar(json.load(sys.stdin))
if not r:
    print("ninguna")
elif r[0] == "__i3_scratch":
    print("oculta")
elif r[1].get("focused"):
    print("enfocada")
else:
    print("otra")
'
}

# Nombre del escritorio enfocado
actual() {
  swaymsg -t get_workspaces | python3 -I -c '
import json, sys
print(next((w["name"] for w in json.load(sys.stdin) if w.get("focused")), ""))
'
}

# Pasa al escritorio vacío, apuntando de cuál se viene
aparte() {
  ws="$(actual)"
  [ -z "$ws" ] || [ "$ws" = "$VACIO" ] && return 0
  printf '%s\n' "$ws" >"$VOLVER"
  swaymsg "workspace $VACIO" >/dev/null
}

# Si seguimos en el escritorio vacío, vuelve al de antes (sway borra el vacío solo)
volver() {
  if [ -s "$VOLVER" ] && [ "$(actual)" = "$VACIO" ]; then
    ws="$(cat "$VOLVER")"
    swaymsg "workspace \"$ws\"" >/dev/null
  fi
  rm -f "$VOLVER"
}

if [ "$1" = cerrar ]; then
  swaymsg "$APP kill" >/dev/null 2>&1
  for _ in 1 2 3 4; do abierta || break; sleep 0.5; done
  abierta && pkill -u "$(id -u)" -x harmonymusic
  volver
  exit 0
fi

if [ "$1" = ocultar ]; then
  case "$(estado)" in
    enfocada|otra) swaymsg "$APP move scratchpad" >/dev/null ;;
  esac
  volver
  exit 0
fi

if [ "$1" = lateral ]; then
  aparte  # antes de abrirla: la regla de sway.conf la muestra en el escritorio enfocado
  if ! abierta; then
    [ -x "$BIN" ] || { echo "harmony.sh: no existe $BIN"; volver; exit 1; }
    "$BIN" </dev/null >/dev/null 2>&1 &
  fi
  # Espera a que la ventana exista (al arrancar tarda un poco)
  for _ in $(seq 40); do
    [ "$(estado)" != ninguna ] && break
    sleep 0.25
  done
  sitio="resize set width $4 px height $5 px, move absolute position $2 px $3 px"
  case "$(estado)" in
    oculta)  swaymsg "$APP scratchpad show, $sitio" >/dev/null ;;
    ninguna) echo "harmony.sh: Harmony no tiene ventana (¿cerrada a la bandeja?). Úsala desde el icono de la bandeja."
             volver
             exit 1 ;;
    *)       # a la vista (sway no deja cambiar nada de una ventana escondida en el scratchpad)
             swaymsg "$APP floating enable, move scratchpad, scratchpad show, $sitio" >/dev/null ;;
  esac
  exit 0
fi

if abierta; then
  case "$(estado)" in
    oculta)   swaymsg "$APP scratchpad show, move position center" ;;
    enfocada) swaymsg "$APP move scratchpad"; volver ;;  # se oculta
    otra)     # abierta fuera del scratchpad (o en otro escritorio): se trae aquí
              swaymsg "$APP floating enable, resize set 1100 720, move scratchpad, scratchpad show, move position center" ;;
    *)        echo "harmony.sh: el programa corre pero no tiene ventana (¿cerrada a la bandeja?). Úsala desde el icono de la bandeja." ;;
  esac
  exit 0
fi
[ -x "$BIN" ] || { echo "harmony.sh: no existe $BIN"; exit 1; }
exec "$BIN"
