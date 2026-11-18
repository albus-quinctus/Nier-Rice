#!/usr/bin/env python3
"""Pantalla de inicio YoRHa para greetd (sustituye a LightDM).

Uso: python3 -I saludo.py
La arranca el sway mínimo de sway.conf como usuario _greetd. Habla con greetd por el
socket de $GREETD_SOCK: cada mensaje es una longitud de 32 bits (orden nativo) y JSON.
Pide usuario y contraseña, contesta a todo lo que pregunte PAM (varios pasos, avisos y
errores) y, al entrar, arranca sh /srv/nier/yorha/sesion.sh y termina; greetd lanza
entonces la sesión. Suspender, reiniciar y apagar van por systemctl, que logind permite
porque el saludo es la sesión activa del asiento. Sin red y sin shell.
"""
import json
import os
import pwd
import socket
import struct
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
FONDO = os.path.join(os.path.dirname(CARPETA), "fondos", "yorha-claro.png")
SESION = ["sh", os.path.join(os.path.dirname(CARPETA), "sesion.sh")]
ENTORNO = ["XDG_SESSION_TYPE=wayland", "XDG_SESSION_DESKTOP=sway", "XDG_CURRENT_DESKTOP=sway"]
# Último usuario que entró: en el HOME de _greetd (en Debian, /var/lib/greetd)
ULTIMO = os.path.join(os.path.expanduser("~"), "ultimo-usuario")
DIAS = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
         "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
UID_PERSONAS = range(1000, 60000)  # UID_MIN y UID_MAX de /etc/login.defs: usuarios de verdad
CONFIRMAR = 4  # segundos para el segundo clic en reiniciar o apagar
ENERGIA = [("SUSPENDER", "suspend"), ("REINICIAR", "reboot"), ("APAGAR", "poweroff")]


def usuarios():
    """Usuarios de /etc/passwd con uid >= 1000 y un shell de verdad, por uid."""
    try:
        with open("/etc/shells") as f:
            shells = {linea.strip() for linea in f if linea.startswith("/")}
    except OSError:
        shells = set()
    lista = [u for u in pwd.getpwall()
             if u.pw_uid in UID_PERSONAS
             and os.path.basename(u.pw_shell) not in ("nologin", "false", "")
             and (not shells or u.pw_shell in shells)]
    return [u.pw_name for u in sorted(lista, key=lambda u: u.pw_uid)]


def leer_ultimo():
    try:
        with open(ULTIMO) as f:
            return f.read().strip()
    except OSError:
        return ""


def guardar_ultimo(nombre):
    try:
        with open(ULTIMO, "w") as f:
            f.write(nombre + "\n")
    except OSError:
        pass  # sin HOME escribible solo se pierde el recuerdo


class Greetd:
    """Conexión con greetd: una petición, una respuesta."""

    def __init__(self, ruta):
        self.conexion = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.conexion.connect(ruta)

    def _leer(self, n):
        datos = b""
        while len(datos) < n:
            trozo = self.conexion.recv(n - len(datos))
            if not trozo:
                raise ConnectionError("greetd ha cerrado la conexión")
            datos += trozo
        return datos

    def pedir(self, tipo, **campos):
        datos = json.dumps(dict(type=tipo, **campos)).encode()
        self.conexion.sendall(struct.pack("=I", len(datos)) + datos)
        return json.loads(self._leer(struct.unpack("=I", self._leer(4))[0]))


def en_segundo_plano(trabajo, al_terminar):
    """Ejecuta trabajo() en un hilo (PAM puede tardar) y entrega el resultado en el de GTK."""
    def hilo():
        try:
            resultado = trabajo()
        except Exception as e:  # noqa: BLE001 — cualquier respuesta rara de greetd: nunca bloquear
            resultado = {"type": "error", "error_type": "conexion", "description": str(e) or type(e).__name__}
        GLib.idle_add(lambda: al_terminar(resultado) and False)
    threading.Thread(target=hilo, daemon=True).start()


def etiqueta(texto, clase):
    e = Gtk.Label(label=texto, xalign=0)
    e.get_style_context().add_class(clase)
    return e


class Saludo(Gtk.Window):
    def __init__(self):
        super().__init__(title="saludo-yorha")
        self.get_style_context().add_class("saludo")
        self._colocar_capa()
        self._cargar_estilo()

        self.greetd = None
        self.ocupado = False     # hay una petición en curso
        self.en_sesion = False   # create_session hecho y aún sin terminar
        self.iniciando = False   # start_session enviado
        self.pendiente = None    # contraseña escrita antes de que PAM la pida
        self.confirmar = None    # (acción, hora) del primer clic en reiniciar/apagar
        self.nombres = usuarios()
        ultimo = leer_ultimo()
        self.elegido = ultimo if ultimo in self.nombres else (self.nombres or [""])[0]

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(raiz)
        raiz.pack_start(self._construir_cabecera(), False, False, 0)
        raiz.pack_start(self._construir_tarjeta(), True, True, 0)
        raiz.pack_end(self._construir_pie(), False, False, 0)

        self.connect("key-press-event", self._al_teclear)
        self.connect("destroy", lambda *_: Gtk.main_quit())
        teclado = Gdk.Keymap.get_for_display(Gdk.Display.get_default())
        teclado.connect("state-changed", lambda k: self.mayus.set_visible(k.get_caps_lock_state()))
        self.mayus.set_visible(teclado.get_caps_lock_state())
        self.reloj()
        GLib.timeout_add_seconds(1, self.reloj)
        self.elegir(self.elegido)
        if not self.nombres:
            self.avisar("No hay usuarios con los que entrar.", True)
        try:
            self.greetd = Greetd(os.environ["GREETD_SOCK"])
        except (KeyError, OSError) as e:
            self.avisar(f"Sin conexión con greetd ({e}).", True)

    def _colocar_capa(self):
        """Pone la ventana en la capa de encima de todo, a pantalla completa y con el teclado."""
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "yorha-saludo")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, -1)
        for borde in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                      GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, borde, True)

    def _cargar_estilo(self):
        """Carga saludo.css y, si está, el fondo de pantalla."""
        proveedor = Gtk.CssProvider()
        with open(os.path.join(CARPETA, "saludo.css")) as f:
            css = f.read()
        if os.path.exists(FONDO):
            css += f'window.saludo {{ background-image: url("file://{FONDO}"); }}\n'
        proveedor.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _construir_cabecera(self):
        """Crea la franja de arriba con la hora, la fecha y la marca."""
        arriba = Gtk.Box()
        arriba.get_style_context().add_class("cabecera")
        reloj = Gtk.Box(spacing=26)  # todo por encima de la franja de arriba del fondo
        self.hora = etiqueta("", "hora")
        self.fecha = etiqueta("", "fecha")
        self.fecha.set_valign(Gtk.Align.CENTER)
        reloj.pack_start(self.hora, False, False, 0)
        reloj.pack_start(self.fecha, False, False, 0)
        arriba.pack_start(reloj, False, False, 0)
        marca = etiqueta("YoRHa · SISTEMA DE ACCESO", "marca")
        marca.set_valign(Gtk.Align.CENTER)
        arriba.pack_end(marca, False, False, 0)
        return arriba

    def _construir_tarjeta(self):
        """Crea la tarjeta del centro: usuarios, pregunta, campo y mensajes."""
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tarjeta.get_style_context().add_class("tarjeta")
        tarjeta.set_halign(Gtk.Align.CENTER)
        tarjeta.set_valign(Gtk.Align.CENTER)
        titulo = Gtk.Box(spacing=12)
        titulo.get_style_context().add_class("titulo")
        cuadro = Gtk.Box()
        cuadro.get_style_context().add_class("cuadro")
        cuadro.set_valign(Gtk.Align.CENTER)
        titulo.pack_start(cuadro, False, False, 0)
        titulo.pack_start(etiqueta("IDENTIFICACIÓN", "titulo-texto"), False, False, 0)
        tarjeta.pack_start(titulo, False, False, 0)
        self.pestanas = Gtk.Box(spacing=10)
        self.pestanas.get_style_context().add_class("usuarios")
        for nombre in self.nombres:
            boton = Gtk.Button(label=nombre.upper())
            boton.get_style_context().add_class("usuario")
            boton.set_can_focus(False)
            boton.connect("clicked", lambda _b, n=nombre: self.elegir(n))
            boton.nombre = nombre
            self.pestanas.pack_start(boton, False, False, 0)
        tarjeta.pack_start(self.pestanas, False, False, 0)
        self.pregunta = etiqueta("CONTRASEÑA", "pregunta")
        tarjeta.pack_start(self.pregunta, False, False, 0)
        self.campo = Gtk.Entry(visibility=False)
        self.campo.get_style_context().add_class("campo")
        self.campo.connect("activate", lambda _c: self.entrar())
        tarjeta.pack_start(self.campo, False, False, 0)
        self.mayus = etiqueta("BLOQ MAYÚS ACTIVADO", "mayus")
        self.mayus.set_halign(Gtk.Align.START)
        self.mayus.set_no_show_all(True)
        tarjeta.pack_start(self.mayus, False, False, 0)
        self.mensaje = etiqueta("", "mensaje")
        self.mensaje.set_line_wrap(True)
        self.mensaje.set_max_width_chars(40)
        tarjeta.pack_start(self.mensaje, False, False, 0)
        return tarjeta

    def _construir_pie(self):
        """Crea el pie con los botones de energía y la ayuda de teclas."""
        abajo = Gtk.Box(spacing=14)
        abajo.get_style_context().add_class("pie")
        for texto, accion in ENERGIA:
            boton = Gtk.Button(label=texto)
            boton.get_style_context().add_class("energia")
            boton.set_can_focus(False)
            boton.connect("clicked", lambda _b, a=accion, t=texto: self.energia(a, t))
            abajo.pack_start(boton, False, False, 0)
        for tecla, accion in (("Intro", "ENTRAR"), ("↑ ↓", "USUARIO"), ("Esc", "BORRAR")):
            abajo.pack_end(etiqueta(accion, "accion"), False, False, 0)
            abajo.pack_end(etiqueta(tecla, "tecla"), False, False, 0)
        return abajo

    # --- Interfaz -------------------------------------------------------------

    def reloj(self):
        t = time.localtime()
        self.hora.set_text(time.strftime("%H:%M", t))
        self.fecha.set_text(f"{DIAS[t.tm_wday]} {t.tm_mday} {MESES[t.tm_mon - 1]} {t.tm_year}")
        return True

    def avisar(self, texto, error=False):
        self.mensaje.set_text(texto)
        ctx = self.mensaje.get_style_context()
        (ctx.add_class if error else ctx.remove_class)("error")

    def elegir(self, nombre):
        if self.ocupado or self.en_sesion:
            return
        self.elegido = nombre
        for boton in self.pestanas.get_children():
            ctx = boton.get_style_context()
            (ctx.add_class if boton.nombre == nombre else ctx.remove_class)("elegido")
        self.campo.grab_focus()

    def preguntar(self, texto, secreto):
        if secreto and texto.strip().rstrip(":").lower() in ("password", "contraseña", ""):
            texto = "CONTRASEÑA"
        self.pregunta.set_text(texto.strip())
        self.campo.set_visibility(not secreto)

    def ocupar(self, si):
        self.ocupado = si
        self.campo.set_sensitive(not si)
        if not si:
            self.campo.grab_focus()

    def _al_teclear(self, _w, evento):
        tecla = evento.keyval
        if tecla in (Gdk.KEY_Up, Gdk.KEY_Down) and len(self.nombres) > 1:
            i = self.nombres.index(self.elegido) if self.elegido in self.nombres else 0
            paso = 1 if tecla == Gdk.KEY_Down else -1
            self.elegir(self.nombres[(i + paso) % len(self.nombres)])
            return True
        if tecla == Gdk.KEY_Escape and not self.ocupado:
            self.campo.set_text("")
            if self.en_sesion:
                self.cancelar("")
            return True
        return False

    # --- greetd ---------------------------------------------------------------

    def pedir(self, tipo, **campos):
        if self.greetd is None:
            try:
                self.greetd = Greetd(os.environ["GREETD_SOCK"])
            except (KeyError, OSError) as e:
                self.avisar(f"Sin conexión con greetd ({e}).", True)
                self.en_sesion = False
                return
        self.ocupar(True)
        en_segundo_plano(lambda: self.greetd.pedir(tipo, **campos), self.respuesta)

    def entrar(self):
        if self.ocupado or not self.elegido:
            return
        texto = self.campo.get_text()
        self.campo.set_text("")
        if self.en_sesion:  # contestación a lo que PAM ha preguntado
            self.pedir("post_auth_message_response", response=texto)
            return
        self.pendiente = texto
        self.en_sesion = True
        self.avisar("Comprobando…")
        self.pedir("create_session", username=self.elegido)

    def respuesta(self, r):
        self.ocupar(False)
        if not isinstance(r, dict):  # greetd siempre manda un objeto; si no, se trata como error
            r = {"type": "error", "error_type": "conexion", "description": "respuesta no válida de greetd"}
        tipo = r.get("type")
        if tipo == "auth_message":
            clase, texto = r.get("auth_message_type"), str(r.get("auth_message") or "")
            if clase in ("info", "error"):
                self.avisar(texto.strip(), clase == "error")
                self.pedir("post_auth_message_response", response=None)
            elif clase == "secret" and self.pendiente is not None:
                respuesta, self.pendiente = self.pendiente, None
                self.pedir("post_auth_message_response", response=respuesta)
            else:  # otra pregunta de PAM (otro factor, contraseña caducada…)
                if self.mensaje.get_text() == "Comprobando…":
                    self.avisar("")
                self.preguntar(texto, clase == "secret")
        elif tipo == "success" and self.iniciando:
            guardar_ultimo(self.elegido)
            Gtk.main_quit()
        elif tipo == "success":
            self.iniciando = True
            self.avisar("Iniciando sesión…")
            self.pedir("start_session", cmd=SESION, env=ENTORNO)
        else:
            if r.get("error_type") == "auth_error":
                texto = "Contraseña incorrecta. Vuelve a intentarlo."
            else:
                texto = str(r.get("description") or "Error desconocido de greetd.")
            if r.get("error_type") == "conexion":
                self.greetd = None
                self.en_sesion = False
            self.cancelar(texto)

    def cancelar(self, texto):
        """Deja greetd y el formulario listos para otro intento."""
        self.pendiente = None
        self.iniciando = False
        self.preguntar("", True)
        self.campo.set_text("")
        self.avisar(texto, bool(texto))
        if not self.en_sesion:
            return
        self.en_sesion = False
        self.ocupar(True)
        en_segundo_plano(lambda: self.greetd.pedir("cancel_session"),
                         lambda _r: self.ocupar(False))

    # --- Energía --------------------------------------------------------------

    def energia(self, accion, texto):
        ahora = time.monotonic()
        if accion != "suspend" and (self.confirmar is None or self.confirmar[0] != accion
                                    or ahora - self.confirmar[1] > CONFIRMAR):
            self.confirmar = (accion, ahora)
            self.avisar(f"Pulsa otra vez {texto} para confirmar.")
            return
        self.confirmar = None

        def orden():
            try:
                r = subprocess.run(["systemctl", "--no-ask-password", accion], capture_output=True,
                                   text=True, timeout=20, stdin=subprocess.DEVNULL)
                return r.stderr.strip() if r.returncode else ""
            except (OSError, subprocess.TimeoutExpired) as e:
                return str(e)
        en_segundo_plano(orden, lambda error: self.avisar(
            f"{texto}: {error}" if error else "", bool(error)))


def main():
    GLib.set_prgname("yorha-saludo")
    ventana = Saludo()
    ventana.show_all()
    Gtk.main()


if __name__ == "__main__":
    sys.exit(main())
