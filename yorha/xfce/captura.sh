#!/bin/sh
# captura.sh — capturas como las de XFCE: Impr (pantalla), Mayús+Impr (zona), Alt+Impr (ventana).
# Uso: sh captura.sh pantalla|zona|ventana
# Guarda un PNG en la carpeta de Imágenes, lo deja en el portapapeles y avisa.
set -u
carpeta=$(xdg-user-dir PICTURES 2>/dev/null)
[ -n "$carpeta" ] && [ "$carpeta" != "$HOME" ] || carpeta="$HOME/Imágenes"
mkdir -p "$carpeta"
archivo="$carpeta/Captura_$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-}" in
  pantalla) grim "$archivo" ;;
  zona) zona=$(slurp) || exit 0; grim -g "$zona" "$archivo" ;;
  ventana)
    zona=$(swaymsg -t get_tree | python3 -I -c '
import json, sys
pendientes = [json.load(sys.stdin)]
while pendientes:
    n = pendientes.pop()
    if n.get("focused") and n.get("type") in ("con", "floating_con"):
        r, d = n["rect"], n.get("deco_rect") or {"height": 0}
        alto = d["height"]
        print("%d,%d %dx%d" % (r["x"], r["y"] - alto, r["width"], r["height"] + alto))
        break
    pendientes += n.get("nodes", []) + n.get("floating_nodes", [])
') || exit 1
    [ -n "$zona" ] || exit 0
    grim -g "$zona" "$archivo" ;;
  *) echo "uso: $0 pantalla|zona|ventana" >&2; exit 2 ;;
esac || exit 1

wl-copy --type image/png < "$archivo"
gdbus call --session --dest org.freedesktop.Notifications --object-path /org/freedesktop/Notifications \
  --method org.freedesktop.Notifications.Notify "Capturas" 0 "camera-photo" "Captura guardada" \
  "$(basename "$archivo") (también en el portapapeles)" "[]" "{}" 4000 >/dev/null 2>&1
exit 0
