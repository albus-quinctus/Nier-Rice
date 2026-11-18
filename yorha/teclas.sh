#!/bin/sh
# teclas.sh — volumen y brillo con las teclas del portátil, y aviso NieR en la barra.
# Uso: sh teclas.sh subir|bajar|silencio|mas-brillo|menos-brillo
# Deja "volumen|brillo VALOR si|no" en $XDG_RUNTIME_DIR/yorha-aviso y avisa a barra.py con SIGUSR2.
# El brillo se cambia a través de logind (sin root ni programas extra).
set -u
YORHA=$(dirname "$(readlink -f "$0")")
AVISO="${XDG_RUNTIME_DIR:?}/yorha-aviso"
export LC_ALL=C

. "$YORHA/comun.sh"

volumen() {
  v=$(pactl get-sink-volume @DEFAULT_SINK@ | grep -o '[0-9]*%' | head -n 1 | tr -d %)
  if pactl get-sink-mute @DEFAULT_SINK@ | grep -q yes; then m=si; else m=no; fi
  echo "volumen ${v:-0} $m" > "$AVISO"
}

brillo() {
  dir=$(ls -d /sys/class/backlight/* 2>/dev/null | head -n 1)
  [ -n "$dir" ] || exit 0
  max=$(cat "$dir/max_brightness")
  act=$(cat "$dir/brightness")
  paso=$((max / 20))
  [ "$paso" -gt 0 ] || paso=1
  nuevo=$((act + $1 * paso))
  [ "$nuevo" -lt "$paso" ] && nuevo=$paso   # nunca del todo a negro
  [ "$nuevo" -gt "$max" ] && nuevo=$max
  busctl call org.freedesktop.login1 /org/freedesktop/login1/session/auto \
    org.freedesktop.login1.Session SetBrightness ssu backlight "$(basename "$dir")" "$nuevo" || exit 1
  echo "brillo $((nuevo * 100 / max)) no" > "$AVISO"
}

case "${1:-}" in
  subir)
    pactl set-sink-volume @DEFAULT_SINK@ +5%
    volumen
    # Sin pasar del 100 %
    if [ "$(cut -d' ' -f2 "$AVISO")" -gt 100 ]; then
      pactl set-sink-volume @DEFAULT_SINK@ 100%
      volumen
    fi ;;
  bajar) pactl set-sink-volume @DEFAULT_SINK@ -5%; volumen ;;
  silencio) pactl set-sink-mute @DEFAULT_SINK@ toggle; volumen ;;
  mas-brillo) brillo 1 ;;
  menos-brillo) brillo -1 ;;
  *) echo "uso: $0 subir|bajar|silencio|mas-brillo|menos-brillo" >&2; exit 2 ;;
esac
avisar_barra USR2
