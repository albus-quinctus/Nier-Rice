#!/bin/sh
# escritorio.sh — cambia al escritorio N con el deslizamiento del original (Mod+1…6).
# Uso: sh escritorio.sh N
# Deja N en $XDG_RUNTIME_DIR/yorha-escritorio y avisa a barra.py con SIGWINCH: la barra
# captura el escritorio actual, cambia y lo desliza fuera. Sin barra, cambia sin animación.
set -u
YORHA=$(dirname "$(readlink -f "$0")")
. "$YORHA/comun.sh"

case "${1:-}" in
  ''|*[!0-9]*) echo "uso: $0 NUMERO" >&2; exit 2 ;;
esac
if pid=$(pid_barra) && echo "$1" > "${XDG_RUNTIME_DIR:?}/yorha-escritorio" \
    && kill -WINCH "$pid" 2>/dev/null; then
  exit 0
fi
exec swaymsg -q "workspace number $1"
