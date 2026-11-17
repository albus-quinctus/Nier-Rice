#!/bin/sh
# tema.sh — alterna el tema YoRHa claro/oscuro: fondo, barras de título y barra de escritorios.
# Lo llama sway con Mod+t. Guarda el tema actual en $XDG_RUNTIME_DIR (se pierde al cerrar sesión).
# Con "aplicar" no alterna: vuelve a poner el tema guardado (sway lo usa al recargar).
set -eu
YORHA=$(dirname "$(readlink -f "$0")")
ESTADO="${XDG_RUNTIME_DIR:?}/yorha-tema"

. "$YORHA/comun.sh"
actual=$(cat "$ESTADO" 2>/dev/null || echo claro)

if [ "${1:-}" = aplicar ]; then
  nuevo=$actual
elif [ "$actual" = claro ]; then
  nuevo=oscuro
else
  nuevo=claro
fi

if [ "$nuevo" = oscuro ]; then
  barra="#c2bda6"; texto="#48463d"; tenue="#6e6b5e"
else
  nuevo=claro; barra="#48463d"; texto="#c2bda6"; tenue="#9e9a87"
fi

# Primero se guarda y luego se avisa: la barra lee el tema de este archivo
echo "$nuevo" > "$ESTADO"
avisar_barra USR1
# Una sola llamada a sway; sway aplica cada orden aunque otra falle (p. ej. sin fondos/
# generados), y un fallo aquí no debe dejar el tema a medias
swaymsg -q "output * bg $YORHA/fondos/yorha-$nuevo.png fill;
  client.focused $barra $barra $texto $barra $barra;
  client.focused_inactive $barra $barra $tenue $barra $barra;
  client.unfocused $barra $barra $tenue $barra $barra" || true
