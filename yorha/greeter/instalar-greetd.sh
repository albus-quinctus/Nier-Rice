#!/bin/sh
# instalar-greetd.sh — cambia LightDM por greetd con la pantalla de inicio YoRHa (saludo.py).
# Lo ejecutas TÚ como root:
#   su -
#   sh /srv/nier/yorha/greeter/instalar-greetd.sh              (instalar y cambiar)
#   sh /srv/nier/yorha/greeter/instalar-greetd.sh --deshacer   (volver a LightDM)
# No instala paquetes ni quita LightDM. Pregunta antes de cambiar. Se puede repetir.
set -eu
[ "$(id -u)" = 0 ] || { echo "Ejecútalo como root (su -)." >&2; exit 1; }
paso() { printf '\n==> %s\n' "$1"; }
GREETER=/srv/nier/yorha/greeter
FONDO=/srv/nier/yorha/fondos/yorha-claro.png
ANTIGUO=/srv/nier/antiguo
PAM=/etc/pam.d/greetd
DM=/etc/systemd/system/display-manager.service
fecha=$(date +%Y%m%d-%H%M%S)

instalado() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }

confirmar() {
  printf '%s [s/N] ' "$1"
  read -r respuesta
  case $respuesta in
    s|S) ;;
    *) echo "Cancelado; la pantalla de inicio sigue como estaba."; exit 1 ;;
  esac
}

# Desactiva $1 y activa $2 como display-manager.service; si falla, deja $1 como estaba
cambiar() {
  # La unidad $1 puede no existir (LightDM ya quitado, o greetd purgado al deshacer)
  systemctl disable "$1" 2>/dev/null || echo "Aviso: no se pudo desactivar $1 (¿no está instalado?)."
  # Por si el enlace display-manager.service no lo puso "systemctl enable" y sigue ahí
  if [ -L "$DM" ] && ! readlink "$DM" | grep -q "$2"; then rm -f "$DM"; fi
  if ! systemctl enable "$2" || ! readlink "$DM" | grep -q "$2"; then
    echo "No se pudo activar $2; se vuelve a dejar $1." >&2
    systemctl disable "$2" 2>/dev/null || true
    systemctl enable "$1" 2>/dev/null || echo "Tampoco se pudo volver a activar $1." >&2
    exit 1
  fi
  echo "display-manager.service -> $(readlink "$DM")"
  # Si el servicio comprueba /etc/X11/default-display-manager (el de LightDM en Debian lo
  # hace antes de arrancar), ese archivo tiene que nombrarlo a él
  ddm=/etc/X11/default-display-manager
  programa=$(command -v "$2" || echo "/usr/sbin/$2")
  if systemctl cat "$2" 2>/dev/null | grep -q default-display-manager \
      && [ "$(cat "$ddm" 2>/dev/null)" != "$programa" ]; then
    [ -f "$ddm" ] && cp -p "$ddm" "$ANTIGUO/default-display-manager.$fecha"
    echo "$programa" > "$ddm"
    echo "$ddm -> $(cat "$ddm")"
  fi
}

if [ "${1:-}" = --deshacer ]; then
  paso "Volver a LightDM (greetd queda instalado pero apagado)"
  instalado lightdm || { echo "LightDM no está instalado. Instálalo con: apt install lightdm" >&2; exit 1; }
  confirmar "¿Desactivar greetd y activar LightDM?"
  cambiar greetd lightdm
  printf '\nHecho. Reinicia el equipo para volver a la pantalla de LightDM.\n'
  exit 0
fi

paso "1/5 Comprobar lo necesario (este script no instala nada)"
if ! instalado greetd; then
  echo "greetd no está instalado. Instálalo tú y vuelve a ejecutar este script:" >&2
  echo "  apt install greetd" >&2
  exit 1
fi
command -v sway >/dev/null || { echo "Falta sway." >&2; exit 1; }
python3 -c 'import gi; gi.require_version("Gtk", "3.0"); gi.require_version("GtkLayerShell", "0.1")' \
  || { echo "Faltan python3-gi o gir1.2-gtklayershell-0.1 (los usa también la barra)." >&2; exit 1; }
[ -f "$PAM" ] || { echo "No existe $PAM (debería venir con greetd)." >&2; exit 1; }
getent passwd _greetd >/dev/null || { echo "No existe el usuario _greetd (lo crea el paquete greetd)." >&2; exit 1; }
for f in saludo.py saludo.css sway.conf config.toml; do
  [ -f "$GREETER/$f" ] || { echo "Falta $GREETER/$f" >&2; exit 1; }
done
echo "greetd $(dpkg-query -W -f='${Version}' greetd), sway y GTK listos."
echo
echo "Si sigues, se hará esto (con copia de seguridad en $ANTIGUO):"
echo "  - /etc/greetd/config.toml y $PAM (llavero de GNOME, como LightDM);"
echo "  - permisos de lectura del saludo para _greetd;"
echo "  - LightDM se desactiva y greetd se activa (LightDM sigue instalado)."
echo "Surte efecto al reiniciar. Hasta que respondas s no se toca nada."
confirmar "¿Seguir?"

paso "2/5 Copias de /etc/greetd/config.toml y $PAM en $ANTIGUO"
mkdir -p "$ANTIGUO"
[ -f /etc/greetd/config.toml ] && cp -p /etc/greetd/config.toml "$ANTIGUO/greetd-config.toml.$fecha"
[ -f "$PAM" ] && cp -p "$PAM" "$ANTIGUO/pam.d-greetd.$fecha"
ls -l "$ANTIGUO" | grep "$fecha" || true

paso "3/5 Configuración de greetd y llavero de GNOME en PAM (como tenía LightDM)"
install -o root -g root -m 644 "$GREETER/config.toml" /etc/greetd/config.toml
[ -f "$PAM" ] || { echo "No existe $PAM (debería venir con greetd)." >&2; exit 1; }
# Añade la línea $2 tras "@include $3" (o al final) si no hay ya una línea $1 con pam_gnome_keyring
anadir_pam() {
  if grep -Eq "^[[:space:]]*-?$1[[:space:]].*pam_gnome_keyring" "$PAM"; then
    echo "Ya estaba: $2"
  elif grep -q "^@include $3\$" "$PAM"; then
    sed -i "/^@include $3\$/a\\
$2" "$PAM"
    echo "Añadida tras @include $3: $2"
  else
    printf '%s\n' "$2" >> "$PAM"
    echo "Añadida al final: $2"
  fi
}
anadir_pam auth "-auth      optional     pam_gnome_keyring.so" common-auth
anadir_pam session "-session   optional     pam_gnome_keyring.so auto_start" common-session
[ -f "$ANTIGUO/pam.d-greetd.$fecha" ] && { diff -u "$ANTIGUO/pam.d-greetd.$fecha" "$PAM" || true; }
[ -f /etc/pam.d/greetd-greeter ] && echo "Nota: existe /etc/pam.d/greetd-greeter (PAM del saludo); no se toca."

paso "4/5 Que _greetd pueda leer el saludo y el fondo, y escribir en su HOME"
chmod -R u=rwX,go=rX "$GREETER"
chmod go+rX "$(dirname "$FONDO")" "$FONDO" 2>/dev/null || echo "Aviso: no existe $FONDO (se verá el color de fondo)."
casa=$(getent passwd _greetd | cut -d: -f6)
# Solo se crea una carpeta normal de servicio; nunca /nonexistent ni rutas raras
case $casa in
  *..*) echo "Aviso: el HOME de _greetd ($casa) tiene \"..\"; no lo creo." ;;
  /var/lib/?*|/var/cache/?*|/run/?*)
    [ -d "$casa" ] || install -d -o _greetd -g _greetd -m 755 "$casa" ;;
  *) echo "Aviso: el HOME de _greetd es $casa; no lo creo." ;;
esac
for f in "$GREETER/saludo.py" "$GREETER/saludo.css" "$GREETER/sway.conf" "$FONDO"; do
  [ -e "$f" ] || continue
  runuser -u _greetd -- test -r "$f" || { echo "_greetd no puede leer $f (¿permisos de /srv o /srv/nier?)" >&2; exit 1; }
done
if runuser -u _greetd -- test -w "$casa"; then
  echo "Último usuario: se guarda en $casa/ultimo-usuario"
else
  echo "Aviso: _greetd no puede escribir en $casa; no recordará el último usuario."
fi

paso "5/5 Cambiar la pantalla de inicio: LightDM -> greetd"
cambiar lightdm greetd

printf '\nHecho. Reinicia el equipo para ver la pantalla de inicio YoRHa.\n'
printf 'Para volver a LightDM (como root):\n  sh %s/instalar-greetd.sh --deshacer\n' "$GREETER"
printf 'Si la pantalla gráfica no aparece: Ctrl+Alt+F2, entra como root y o bien vuelve a LightDM\n'
printf 'con la orden de arriba y reinicia, o pasa greetd a modo texto (agreety) cambiando en\n'
printf '/etc/greetd/config.toml la línea command por:\n'
printf '  command = "agreety --cmd '"'"'sh /srv/nier/yorha/sesion.sh'"'"'"\n'
printf 'y luego: systemctl restart greetd\n'
