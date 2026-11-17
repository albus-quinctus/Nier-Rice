#!/usr/bin/env python3
"""Abre los programas de inicio automático, como hacía XFCE al entrar (sway no lo hace solo).

Lee ~/.config/autostart y /etc/xdg/autostart (el del usuario manda si se llaman igual) y
respeta lo de siempre: Hidden, X-GNOME-Autostart-enabled=false, OnlyShowIn/NotShowIn,
TryExec y AutostartCondition (como el lector de pantalla, que solo arranca si está activado).
Los que sway.conf ya arranca a mano se saltan para no abrirlos dos veces.

Uso: python3 -I autoarranque.py   (lo arranca sway.conf, el de YoRHa o el de xfce, al entrar)
"""
import os
import sys

# Así OnlyShowIn=XFCE (el gestor de energía de XFCE, sus avisos...) no se abre aquí
os.environ.setdefault("XDG_CURRENT_DESKTOP", "sway")

from gi.repository import Gio, GLib  # noqa: E402

# Ya los arranca sway.conf (o el panel hace su trabajo)
YA_ARRANCADOS = ("nm-applet", "gnome-keyring", "polkit", "snap-userd", "xfce4-", "xfsettingsd",
                 "at-spi", "geoclue", "print-applet", "user-dirs-update", "light-locker",
                 "xscreensaver", "pulseaudio", "xdg-user-dirs", "xiccd")  # xiccd: solo X11


def carpetas():
    usuario = os.path.join(GLib.get_user_config_dir(), "autostart")
    sistema = [os.path.join(d, "autostart") for d in GLib.get_system_config_dirs()]
    return [usuario] + sistema


def entradas():
    vistos = set()
    for carpeta in carpetas():
        try:
            nombres = sorted(os.listdir(carpeta))
        except OSError:
            continue
        for nombre in nombres:
            if not nombre.endswith(".desktop") or nombre in vistos:
                continue
            vistos.add(nombre)  # aunque esté desactivado: el del usuario tapa al del sistema
            yield nombre, os.path.join(carpeta, nombre)


def se_cumple(condicion):
    """AutostartCondition: "GSettings esquema clave", "if-exists archivo" o "unless-exists archivo"
    (relativo a ~/.config). Las que no se entienden no impiden arrancar."""
    partes = condicion.split()
    if len(partes) == 3 and partes[0] == "GSettings":
        fuente = Gio.SettingsSchemaSource.get_default()
        esquema = fuente and fuente.lookup(partes[1], True)
        if not esquema or not esquema.has_key(partes[2]) \
                or esquema.get_key(partes[2]).get_value_type().dup_string() != "b":
            return False
        return Gio.Settings.new(partes[1]).get_boolean(partes[2])
    if len(partes) == 2 and partes[0] in ("if-exists", "unless-exists"):
        existe = os.path.exists(os.path.join(GLib.get_user_config_dir(), partes[1]))
        return existe if partes[0] == "if-exists" else not existe
    return True


def desactivada(ruta):
    """Si la entrada pide no arrancar: Hidden, X-GNOME-Autostart-enabled=false o su
    AutostartCondition no se cumple. La clave que falta (o no se lee) no lo impide."""
    clave = GLib.KeyFile()
    try:
        clave.load_from_file(ruta, GLib.KeyFileFlags.NONE)
        if clave.get_boolean("Desktop Entry", "Hidden"):
            return True
    except GLib.Error:
        pass
    try:
        if not clave.get_boolean("Desktop Entry", "X-GNOME-Autostart-enabled"):
            return True
    except GLib.Error:
        pass
    try:
        if not se_cumple(clave.get_string("Desktop Entry", "AutostartCondition")):
            return True
    except GLib.Error:
        pass
    return False


def main():
    for nombre, ruta in entradas():
        if nombre.startswith(YA_ARRANCADOS) or desactivada(ruta):
            continue
        try:
            app = Gio.DesktopAppInfo.new_from_filename(ruta)
        except TypeError:  # falta el programa de TryExec, o el archivo está mal
            continue
        if not app or not app.get_show_in(None):
            continue
        try:
            app.launch([], None)
            print(f"autoarranque: {nombre}", file=sys.stderr)
        except GLib.Error as error:
            print(f"autoarranque: {nombre}: {error.message}", file=sys.stderr)


if __name__ == "__main__":
    main()
