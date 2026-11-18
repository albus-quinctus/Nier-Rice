#!/bin/sh
# orden.sh — manda una orden al panel (panel.py) desde un atajo de sway.
# Uso: sh orden.sh menu|alttab|alttab-atras|minimizar|maximizar|izquierda|derecha|salir
# Añade la orden a $XDG_RUNTIME_DIR/yorha-orden y avisa al panel con SIGWINCH.
set -u
YORHA=$(dirname "$(dirname "$(readlink -f "$0")")")
. "$YORHA/comun.sh"

case "${1:-}" in
  menu|alttab|alttab-atras|minimizar|maximizar|izquierda|derecha|salir) ;;
  *) echo "uso: $0 menu|alttab|alttab-atras|minimizar|maximizar|izquierda|derecha|salir" >&2; exit 2 ;;
esac
pid=$(pid_barra) || exit 0
echo "$1" >> "${XDG_RUNTIME_DIR:?}/yorha-orden" && kill -WINCH "$pid" 2>/dev/null
exit 0
