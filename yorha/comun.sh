# comun.sh — lo comparten tema.sh, teclas.sh, escritorio.sh y xfce/orden.sh (se carga con
# ". comun.sh"; necesita $YORHA, la carpeta yorha).

# PID de la barra de verdad, el que publicó barra.py (o xfce/panel.py, en el escritorio de
# trabajo), comprobando que sigue siendo python con ese programa (nunca pkill -f, que también
# alcanzaría a un editor abierto); falla si no hay
pid_barra() {
  pid=$(cat "${XDG_RUNTIME_DIR:?}/yorha-barra.pid" 2>/dev/null) || return 1
  case "$( { tr '\0' ' ' < "/proc/$pid/cmdline"; } 2>/dev/null)" in
    *python*" $YORHA/barra.py"*|*python*" $YORHA/xfce/panel.py"*) echo "$pid" ;;
    *) return 1 ;;
  esac
}

# Señal a la barra, si está; con set -e, que no corte si no está
avisar_barra() {
  pid=$(pid_barra) && kill -"$1" "$pid" 2>/dev/null || true
}
