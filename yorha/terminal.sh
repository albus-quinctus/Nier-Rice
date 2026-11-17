#!/bin/sh
# terminal.sh — abre foot con los colores del tema actual (claro u oscuro).
# Las terminales ya abiertas conservan sus colores; las nuevas siguen el tema.
YORHA=$(dirname "$(readlink -f "$0")")
if [ "$(cat "${XDG_RUNTIME_DIR:-/nonexistent}/yorha-tema" 2>/dev/null)" = oscuro ]; then
  exec foot -c "$YORHA/foot-oscuro.ini" "$@"
fi
exec foot -c "$YORHA/foot.ini" "$@"
