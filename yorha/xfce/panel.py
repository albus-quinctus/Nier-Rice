#!/usr/bin/env python3
"""Panel estilo XFCE para sway: el de lilith, rehecho a mano para trabajo (arriba de la pantalla).

De izquierda a derecha, como el panel de XFCE: menú de aplicaciones, lanzadores (Terminal,
ARCHBOX, LibreWolf, Cursor), botones de las ventanas del escritorio actual, escritorios,
notificaciones, energía, volumen, bandeja, Proton VPN y reloj.

Uso: python3 -I panel.py   (lo arranca xfce/sway.conf)

Su PID queda en $XDG_RUNTIME_DIR/yorha-barra.pid, como el de barra.py, para que teclas.sh
le avise con SIGUSR2 del volumen y el brillo (lo enseña como notificación). Las órdenes de
los atajos (menú, Alt+Tab, minimizar...) llegan con orden.sh: una línea por orden en
$XDG_RUNTIME_DIR/yorha-orden y SIGWINCH.

También lleva swayidle (bloqueo a los 5 min y pantalla apagada a los 10), para poder
pararlo con el modo presentación; si el panel se cierra, swayidle se cierra con él.

Ventanas (en ventanas.py): todas flotan, como en XFCE. Minimizar es mandarla al scratchpad de
sway con una marca _min_ESCRITORIO_ID, para volver a su escritorio al restaurarla. Las ventanas se reparten
solas la pantalla: cada vez que se abre, cierra, minimiza o cambia de escritorio una, las del
escritorio se ordenan (una ocupa todo; dos, mitad y mitad; más, en columnas), con los márgenes
de xfwm4. Los diálogos no entran en el reparto: van centrados sobre su ventana. Como el
tile_on_move de xfwm4, al soltar una ventana arrastrada contra el borde izquierdo o derecho
ocupa esa mitad, y contra el panel se maximiza. Ninguna puede quedarse bajo el panel (solo en
pantalla completa lo tapan).
"""
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import traceback

import gi

# El panel va en modo oscuro, como el de lilith, sin cambiar el tema de las demás ventanas:
# GTK_THEME se lee al cargar Gtk, así que se pone antes (con el tema elegido en gsettings)
from gi.repository import Gio  # noqa: E402

_TEMA_PUESTO = "GTK_THEME" not in os.environ
if _TEMA_PUESTO:
    _fuente = Gio.SettingsSchemaSource.get_default()
    # Sin el esquema, Gio.Settings.new() abortaría el programa: se mira antes
    _tema = (Gio.Settings.new("org.gnome.desktop.interface").get_string("gtk-theme")
             if _fuente and _fuente.lookup("org.gnome.desktop.interface", True) else "")
    os.environ["GTK_THEME"] = f"{_tema or 'Adwaita'}:dark"

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

if _TEMA_PUESTO:
    # Ya cargado el tema, se quita: los programas que abra el panel heredarían el modo oscuro
    Gtk.Settings.get_default()
    del os.environ["GTK_THEME"]

CARPETA = os.path.dirname(os.path.abspath(__file__))
YORHA = os.path.dirname(CARPETA)
sys.path[:0] = [CARPETA, YORHA]  # con python3 -I las carpetas no están en sys.path
import sistema  # noqa: E402
import comun  # noqa: E402
from barra import (COMANDO, OBTENER_ARBOL, OBTENER_ESCRITORIOS, SUSCRIBIR,  # noqa: E402
                   AlertaBateria, Sway, bateria)
import bandeja  # noqa: E402
import emergentes  # noqa: E402
from emergentes import icono  # noqa: E402
from ventanas import RESERVA, Ventanas  # noqa: E402

ALTO = 42
# Como el panel de lilith: flota a 12 px del borde y reserva RESERVA (65 px, en ventanas.py) arriba
SEPARACION = 12
ICONO = 22
VOLUMEN_MEDIO, VOLUMEN_ALTO = 34, 67  # % desde el que el icono pasa a medium y a high
ESCRITORIOS = 4
LANZADORES = (("org.codeberg.dnkl.foot", "foot", "xfce4-terminal"),
              ("archbox", "thunar", "Thunar"),
              ("librewolf",),
              ("cursor", "co.anysphere.cursor"))
PROTON = ("protonvpn-app", "proton.vpn.app.gtk", "protonvpn")
BLOQUEAR = ["sh", os.path.join(CARPETA, "bloquear.sh")]
ORDEN = os.path.join(comun.RUNTIME, "yorha-orden")


def app_de_seguro(candidatos):
    """La primera aplicación instalada de los ids candidatos (sin .desktop), o None."""
    for ident in candidatos:
        try:
            app = Gio.DesktopAppInfo.new(f"{ident}.desktop")
        except TypeError:  # PyGObject lanza esto cuando no existe
            app = None
        if app:
            return app
    return None


class Iconos:
    """Icono de cada app_id de sway, buscando su .desktop (con caché)."""

    def __init__(self):
        self.cache = {}
        self.apps = None

    def de(self, app_id):
        if app_id in self.cache:
            return self.cache[app_id]
        nombre = None
        app = app_de_seguro([app_id, app_id.lower()]) if app_id else None
        if not app and app_id:
            if self.apps is None:
                self.apps = Gio.AppInfo.get_all()
            clave = app_id.lower()
            for a in self.apps:
                wm = (a.get_startup_wm_class() or "").lower() if hasattr(a, "get_startup_wm_class") else ""
                ident = (a.get_id() or "").lower()
                if (clave in (wm, ident[:-8]) or ident.startswith(clave + "_")
                        or ident.endswith("." + clave + ".desktop")):
                    app = a
                    break
        if app and app.get_icon():
            gicon = app.get_icon()
            nombre = gicon.get_names()[0] if isinstance(gicon, Gio.ThemedIcon) else gicon.to_string()
        if not nombre and app_id and Gtk.IconTheme.get_default().has_icon(app_id.lower()):
            nombre = app_id.lower()
        self.cache[app_id] = nombre or icono("application-x-executable", "application-default-icon")
        return self.cache[app_id]


def nivel_volumen(valor):
    """low, medium o high: la parte del nombre del icono de volumen que toca a valor (%)."""
    return "low" if valor < VOLUMEN_MEDIO else "medium" if valor < VOLUMEN_ALTO else "high"


def imagen(nombre):
    if nombre and nombre.startswith("/"):
        img = Gtk.Image.new_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(nombre)),
                                       Gtk.IconSize.LARGE_TOOLBAR)
    else:
        img = Gtk.Image.new_from_icon_name(nombre, Gtk.IconSize.LARGE_TOOLBAR)
    img.set_pixel_size(ICONO)
    return img


def boton_panel(nombre_icono=None, consejo=None, gicon=None):
    b = Gtk.Button()
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.set_can_focus(False)
    if gicon:
        img = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.LARGE_TOOLBAR)
        img.set_pixel_size(ICONO)
        b.add(img)
    elif nombre_icono:
        b.add(imagen(nombre_icono))
    if consejo:
        b.set_tooltip_text(consejo)
    b.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
    return b


def separador(expandido=False):
    if expandido:
        return Gtk.Box()
    s = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
    s.set_margin_top(8)
    s.set_margin_bottom(8)
    return s


def sentido_rueda(evento):
    """+1 hacia arriba, -1 hacia abajo, 0 si no es vertical."""
    if evento.direction == Gdk.ScrollDirection.UP:
        return 1
    if evento.direction == Gdk.ScrollDirection.DOWN:
        return -1
    hay, _dx, dy = evento.get_scroll_deltas()
    return (-1 if dy > 0 else 1) if hay and abs(dy) > 0.3 else 0


class Inactividad:
    """swayidle hijo del panel: bloqueo a los 5 min, pantalla apagada a los 10."""

    def __init__(self):
        self.proceso = None
        self.presentacion = False
        self.arrancar()

    def arrancar(self):
        bloquear = " ".join(BLOQUEAR)
        if self.presentacion:  # aun así, bloquea antes de suspender
            argv = ["swayidle", "-w", "before-sleep", bloquear]
        else:
            argv = ["swayidle", "-w", "timeout", "300", bloquear,
                    "timeout", "600", "swaymsg 'output * power off'",
                    "resume", "swaymsg 'output * power on'", "before-sleep", bloquear]
        # setpriv (util-linux) hace que swayidle muera si muere el panel. Se evita preexec_fn,
        # que con los hilos del panel puede dejar al hijo bloqueado antes de arrancar
        if shutil.which("setpriv"):
            argv = ["setpriv", "--pdeathsig", "TERM", "--"] + argv
        try:
            self.proceso = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
        except OSError:
            self.proceso = None

    def parar(self):
        if self.proceso and self.proceso.poll() is None:
            self.proceso.terminate()
            try:
                self.proceso.wait(2)
            except subprocess.TimeoutExpired:
                self.proceso.kill()
        self.proceso = None

    def poner_presentacion(self, activa):
        if activa != self.presentacion:
            self.presentacion = activa
            self.parar()
            self.arrancar()


class Panel(Ventanas, Gtk.Window):
    def __init__(self):
        super().__init__()
        self.sway = Sway()
        self.iconos = Iconos()
        self.inactividad = Inactividad()
        self.emergente = None
        self.selector = None
        self.mru = []            # ids de ventana, la última enfocada primero
        self.ventanas = []       # del último repaso del árbol
        self.actual = "1"
        self.maximizadas = {}    # id -> (x, y, ancho, alto) para restaurar
        self.dialogos = set()    # ids de ventana que no entran en el reparto
        self.repartidas = {}     # escritorio -> ids que se repartieron la última vez
        self.pendiente_reparto = False
        self.posiciones = {}     # id -> caja en la última vuelta de vigilar_bordes
        self.quietas = {}        # id -> vueltas seguidas sin moverse
        self.arrastradas = {}    # id -> caja de antes de empezar a arrastrarla
        self.esperadas = {}      # id -> caja a la que la acaba de mandar el propio panel
        self.alerta = AlertaBateria()
        self.aviso_id = 0
        self.aviso_en_vuelo = False
        self.aviso_siguiente = None
        self.pendiente = False
        self.no_molestar = False
        self.recien_cerrada = (None, 0)  # (tipo, hora) de la emergente que cerró su propio botón
        self.boton_emergente = None

        self.colocar_capa()

        caja = Gtk.Box(spacing=2)
        caja.set_margin_start(4)
        caja.set_margin_end(4)
        self.add(caja)
        poner = lambda w, expandir=False: caja.pack_start(w, expandir, expandir, 0)  # noqa: E731

        self.construir_menu_y_tareas(poner)
        poner(separador(expandido=True), True)
        poner(separador())
        self.construir_paginador()
        poner(self.paginador)
        poner(separador())
        self.construir_estado_y_reloj(poner)
        poner(separador())

        # En fase de captura: llega antes que a los botones, que se quedan la pulsación
        self.gesto = Gtk.GestureMultiPress.new(self)
        self.gesto.set_button(0)
        self.gesto.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.gesto.connect("pressed", self.clic_en_panel)
        self.tic()
        GLib.timeout_add(400, self.vigilar_bordes)
        self.repartir(solo_anotar=True)  # si el panel se reinicia, no mueve nada
        self.repasar()
        self.vigilar_volumen()
        self.suscribir()

    def colocar_capa(self):
        """Pone el panel como capa de arriba, a lo ancho y con su zona reservada."""
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "panel")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        margen = round(Gdk.Display.get_default().get_monitor(0).get_geometry().width * 0.00625)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, margen)  # 98,75 % de ancho
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.RIGHT, margen)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, SEPARACION)
        # sway suma el margen de arriba a la zona exclusiva: 12 + 53 = los 65 px de XFCE
        GtkLayerShell.set_exclusive_zone(self, RESERVA - SEPARACION)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        self.set_size_request(-1, ALTO)
        self.get_style_context().add_class("panel")

    def construir_menu_y_tareas(self, poner):
        """Botón del menú, lanzadores fijos y la caja de los botones de tareas."""
        poner(separador())
        # El icono del menú de lilith es una imagen suelta en pixmaps, no de un tema de iconos
        nier = "/usr/share/pixmaps/Nier-Icon.png"
        self.boton_menu = boton_panel(nier if os.path.exists(nier) else
                                      icono("start-here", "xfce4-whiskermenu", "applications-other"),
                                      "Aplicaciones")
        self.boton_menu.connect("clicked", lambda _b: self.alternar_menu())
        poner(self.boton_menu)
        poner(separador())
        for candidatos in LANZADORES:
            app = app_de_seguro(candidatos)
            if app:
                poner(self.lanzador(app))
        self.tareas = Gtk.Box(spacing=2)
        self.botones_tareas = {}
        poner(self.tareas)

    def construir_paginador(self):
        """Un botón por escritorio; la rueda encima también cambia de escritorio."""
        self.paginador = Gtk.Box(spacing=0)
        self.paginador.get_style_context().add_class("paginador")
        self.paginas = []
        for n in range(1, ESCRITORIOS + 1):
            b = Gtk.Button(label=str(n))
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.set_can_focus(False)
            b.set_tooltip_text(f"Escritorio {n}")
            b.connect("clicked", lambda _b, n=n: self.ir_a(n))
            b.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
            b.connect("scroll-event", self.rueda_paginador)
            self.paginador.pack_start(b, False, False, 0)
            self.paginas.append(b)

    def construir_estado_y_reloj(self, poner):
        """Avisos, energía, volumen, bandeja, Proton y el reloj, en ese orden."""
        self.boton_avisos = boton_panel(self.icono_avisos(), "Notificaciones")
        self.boton_avisos.connect("clicked", self.menu_avisos)
        poner(self.boton_avisos)
        self.boton_energia = boton_panel("battery-full", "Energía")
        self.boton_energia.connect("clicked", lambda b: self.alternar(
            emergentes.Energia, lambda: emergentes.Energia(
                self.cerrado, self.inactividad.presentacion, self.inactividad.poner_presentacion), b))
        poner(self.boton_energia)
        self.boton_volumen = boton_panel(icono("audio-volume-medium", "audio-volume-high"), "Volumen")
        self.boton_volumen.connect("clicked", lambda b: self.alternar(
            emergentes.Volumen, lambda: emergentes.Volumen(self.cerrado), b))
        self.boton_volumen.connect("scroll-event", self.rueda_volumen)
        self.boton_volumen.connect("button-release-event", self.clic_volumen)
        poner(self.boton_volumen)
        self.bandeja = bandeja.Bandeja()
        poner(self.bandeja)
        proton = app_de_seguro(PROTON)
        if proton:
            poner(self.lanzador(proton))
        self.reloj = Gtk.Button()
        self.reloj.set_relief(Gtk.ReliefStyle.NONE)
        self.reloj.set_can_focus(False)
        self.reloj.get_style_context().add_class("reloj")
        self.reloj.add(Gtk.Label())
        self.reloj.connect("clicked", lambda b: self.alternar(
            emergentes.Calendario, lambda: emergentes.Calendario(self.cerrado), b))
        poner(self.reloj)

    # --- Lanzadores y menús ----------------------------------------------------

    def lanzador(self, app):
        b = boton_panel(consejo=app.get_display_name(), gicon=app.get_icon())
        if not app.get_icon():
            b.add(imagen("application-x-executable"))

        def abrir(_b):
            with contextlib.suppress(GLib.Error):
                app.launch([], None)
        b.connect("clicked", abrir)
        return b

    def clic_en_panel(self, _gesto, _n, x, y):
        """Un clic en el panel cierra la emergente abierta (como un clic fuera en XFCE).

        Si el clic es en el mismo botón que la abrió, se apunta: ese botón solo debe cerrarla."""
        if self.emergente:
            boton = self.boton_emergente
            dentro = False
            if boton is not None:
                bx, by = boton.translate_coordinates(self, 0, 0)
                dentro = (bx <= x < bx + boton.get_allocated_width()
                          and by <= y < by + boton.get_allocated_height())
            self.recien_cerrada = (type(self.emergente), time.monotonic()) if dentro else (None, 0)
            self.cerrar_emergente()

    def alternar(self, tipo, crear, junto_a):
        """Abre la emergente de un botón del panel; si ya estaba abierta, solo la cierra.

        Se mira antes de crearla: Volumen o el menú lanzan trabajo al construirse."""
        anterior = type(self.emergente) if self.emergente else None
        cerrada, hora = self.recien_cerrada
        if anterior is None and time.monotonic() - hora < 1:
            anterior = cerrada  # la cerró este mismo clic, al apretar el botón
        if anterior is tipo:
            self.recien_cerrada = (None, 0)
            self.cerrar_emergente()
            return
        self.abrir(crear(), junto_a)

    def abrir(self, ventana, junto_a=None):
        """Abre una emergente, cerrando la que hubiera."""
        self.recien_cerrada = (None, 0)
        self.cerrar_emergente()
        self.emergente = ventana
        self.boton_emergente = junto_a
        if junto_a is not None:
            ventana.junto_a(junto_a)
        ventana.show_all()

    def cerrado(self, ventana):
        if self.emergente is ventana:
            self.emergente = None
        if self.selector is ventana:
            self.selector = None

    def cerrar_emergente(self):
        if self.emergente:
            self.emergente.cerrar()
        return False

    def alternar_menu(self):
        self.alternar(emergentes.MenuApps,
                      lambda: emergentes.MenuApps(self.cerrado, self.salir, self.bloquear), self.boton_menu)

    def salir(self):
        self.abrir(emergentes.Salir(self.cerrado, self.bloquear,
                                    lambda: self.sway.pedir(COMANDO, b"exit")))

    @staticmethod
    def bloquear():
        sistema.lanzar(BLOQUEAR)

    # --- Notificaciones (mako) -------------------------------------------------

    def icono_avisos(self):
        if self.no_molestar:
            return icono("notifications-disabled", "notifications-disabled-symbolic",
                         "notification-disabled-symbolic", "preferences-system-notifications")
        return icono("notifications", "preferences-system-notifications",
                     "notification-symbolic", "dialog-information")

    def menu_avisos(self, b):
        menu = Gtk.Menu()
        molestar = Gtk.CheckMenuItem(label="No molestar")
        molestar.set_active(self.no_molestar)
        molestar.connect("toggled", self.alternar_no_molestar)
        menu.append(molestar)
        menu.append(Gtk.SeparatorMenuItem())
        for texto, argv in (("Ver la última otra vez", ["makoctl", "restore"]),
                            ("Quitar todas", ["makoctl", "dismiss", "--all"])):
            elemento = Gtk.MenuItem(label=texto)
            elemento.connect("activate", lambda _e, a=argv: sistema.lanzar(a))
            menu.append(elemento)
        menu.show_all()
        self.menu_abierto = menu  # que no lo recoja el recolector mientras está abierto
        menu.popup_at_widget(b, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def alternar_no_molestar(self, elemento):
        self.no_molestar = elemento.get_active()
        sistema.lanzar(["makoctl", "mode", "-a" if self.no_molestar else "-r", "no-molestar"])
        hijo = self.boton_avisos.get_child()
        hijo.set_from_icon_name(self.icono_avisos(), Gtk.IconSize.LARGE_TOOLBAR)
        hijo.set_pixel_size(ICONO)

    # --- Volumen ---------------------------------------------------------------

    def vigilar_volumen(self):
        """pactl subscribe avisa de cada cambio: así el icono sigue al volumen sin sondear."""
        self.refrescar_volumen()
        argv = ["pactl", "subscribe"]
        # Si el panel se cierra, pactl también: si no, deja la sesión de sway sin terminar al salir
        if shutil.which("setpriv"):
            argv = ["setpriv", "--pdeathsig", "TERM", "--"] + argv
        try:
            proceso = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE)
        except GLib.Error:
            return
        self.pactl = proceso
        flujo = Gio.DataInputStream.new(proceso.get_stdout_pipe())

        def linea(f, resultado):
            try:
                texto, _largo = f.read_line_finish_utf8(resultado)
            except GLib.Error:
                texto = None
            if texto is None:
                return  # pactl terminó (PipeWire reiniciado): el icono se pone al día con las teclas
            if (" sink " in texto or " server " in texto) and not self.pendiente:
                self.pendiente = True
                GLib.timeout_add(100, self.refrescar_volumen)
            f.read_line_async(GLib.PRIORITY_DEFAULT, None, linea)
        flujo.read_line_async(GLib.PRIORITY_DEFAULT, None, linea)

    def refrescar_volumen(self):
        self.pendiente = False
        sistema.en_segundo_plano(lambda: sistema.volumen("sink"), self._con_volumen)
        return False

    def _con_volumen(self, resultado):
        valor, mudo = resultado
        if valor is None:
            nombre, texto = icono("audio-volume-muted"), "Sin sonido"
        elif mudo or valor == 0:
            nombre, texto = icono("audio-volume-muted"), f"Volumen {valor} % (silenciado)"
        else:
            nombre = icono(f"audio-volume-{nivel_volumen(valor)}", "audio-volume-high")
            texto = f"Volumen {valor} %"
        hijo = self.boton_volumen.get_child()
        hijo.set_from_icon_name(nombre, Gtk.IconSize.LARGE_TOOLBAR)
        hijo.set_pixel_size(ICONO)
        self.boton_volumen.set_tooltip_text(texto)

    def rueda_volumen(self, _b, evento):
        paso = sentido_rueda(evento)
        if paso:
            sistema.lanzar(["sh", os.path.join(YORHA, "teclas.sh"), "subir" if paso > 0 else "bajar"])
        return True

    def clic_volumen(self, _b, evento):
        if evento.button == Gdk.BUTTON_MIDDLE:  # clic central: silencio, como en XFCE
            sistema.lanzar(["sh", os.path.join(YORHA, "teclas.sh"), "silencio"])
            return True
        return False

    def aviso_teclas(self):
        """SIGUSR2 de teclas.sh: el volumen o el brillo nuevo, como notificación con barra."""
        try:
            with open(os.path.join(comun.RUNTIME, "yorha-aviso")) as f:
                nombre, valor, mudo = f.read().split()
            valor = int(valor)
        except (OSError, ValueError):
            return True
        if nombre == "volumen":
            self.refrescar_volumen()
            titulo = "Volumen" + (" (silenciado)" if mudo == "si" else "")
            nombre_icono = icono("audio-volume-muted") if mudo == "si" or valor == 0 else \
                icono(f"audio-volume-{nivel_volumen(valor)}")
        else:
            titulo, nombre_icono = "Brillo", icono("display-brightness", "brightness-display-symbolic",
                                                  "video-display")
        self.aviso_siguiente = (nombre_icono, titulo, valor)
        if not self.aviso_en_vuelo:
            self._mandar_aviso()
        return True  # siempre True: con False GLib quitaría el manejador y SIGUSR2 mataría el panel

    def _mandar_aviso(self):
        """Uno a la vez y sin esperar a mako: si llegan teclas mientras tanto, va solo la última,
        ya con el id del aviso anterior para sustituirlo en vez de apilar otro."""
        if not self.aviso_siguiente:
            self.aviso_en_vuelo = False
            return
        nombre_icono, titulo, valor = self.aviso_siguiente
        self.aviso_siguiente = None
        self.aviso_en_vuelo = True

        def hecho(bus, resultado):
            with contextlib.suppress(GLib.Error):
                self.aviso_id = bus.call_finish(resultado).unpack()[0]
            self._mandar_aviso()
        try:
            Gio.bus_get_sync(Gio.BusType.SESSION, None).call(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", ("panel", self.aviso_id, nombre_icono, titulo,
                                                 f"{valor} %", [],
                                                 {"value": GLib.Variant("i", valor),
                                                  "urgency": GLib.Variant("y", 0)}, 1500)),
                GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000, None, hecho)
        except GLib.Error:
            self.aviso_en_vuelo = False

    # --- Reloj y batería -------------------------------------------------------

    def tic(self):
        ahora = time.localtime()
        self.reloj.get_child().set_text(time.strftime("%H:%M", ahora))
        self.reloj.set_tooltip_text(time.strftime("%A, %-d de %B de %Y", ahora).capitalize())
        estado = bateria()
        hijo = self.boton_energia.get_child()
        if estado:
            valor, cargando, descargando = estado
            nivel = "full" if valor >= 90 else "good" if valor >= 50 else "low" if valor >= 20 \
                else "caution" if valor >= 10 else "empty"
            sufijo = "-charging" if cargando else ""
            nombre = icono(f"battery-{nivel}{sufijo}", f"battery-{nivel}", "battery")
            estado_texto = "cargando" if cargando else "con batería" if descargando else "enchufado"
            self.boton_energia.set_tooltip_text(f"Batería {valor} % ({estado_texto})")
            self.alerta.revisar(valor, descargando)
        else:
            nombre = icono("ac-adapter", "battery-full-charged", "battery")
            self.boton_energia.set_tooltip_text("Enchufado")
        hijo.set_from_icon_name(nombre, Gtk.IconSize.LARGE_TOOLBAR)
        hijo.set_pixel_size(ICONO)
        GLib.timeout_add_seconds(60 - ahora.tm_sec, self.tic)
        return False

    # --- Sway: escritorios y ventanas ------------------------------------------

    def suscribir(self):
        s = self.sway._conectar()
        self.sway._enviar(s, SUSCRIBIR, b'["workspace", "window"]')
        self.sway._recibir(s)

        def leer(_fuente, _condicion):
            try:
                _tipo, cuerpo = self.sway._recibir(s)
            except (ConnectionError, OSError):
                Gtk.main_quit()  # sway se fue: se está cerrando la sesión
                return False
            try:
                evento = json.loads(cuerpo)
                if evento.get("change") == "new" and evento.get("container"):
                    # sway aún no ha aplicado el for_window que la hace flotar: un momento después
                    ident = evento["container"]["id"]
                    GLib.timeout_add(60, lambda: self.colocar(ident) and False)
                if evento.get("change") in ("new", "close", "move", "floating", "fullscreen_mode"):
                    self.pedir_reparto()
                if evento.get("change") == "focus" and evento.get("container"):
                    ident = evento["container"]["id"]
                    if ident in self.mru:
                        self.mru.remove(ident)
                    self.mru.insert(0, ident)
            except (ValueError, KeyError, TypeError):
                pass
            if not self.pendiente_arbol:
                self.pendiente_arbol = True
                GLib.idle_add(self.repasar)
            return True

        self.pendiente_arbol = False
        GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, leer)
        self.socket_eventos = s

    def mandar(self, orden):
        try:
            return self.sway.pedir(COMANDO, orden.encode())
        except (ConnectionError, OSError, ValueError):
            return None

    def repasar(self):
        """Relee escritorios y ventanas de sway y pone al día los botones."""
        self.pendiente_arbol = False
        try:
            escritorios = self.sway.pedir(OBTENER_ESCRITORIOS)
            arbol = self.sway.pedir(OBTENER_ARBOL)
        except (ConnectionError, OSError, ValueError):
            return False
        self.actual = next((w["name"] for w in escritorios if w.get("focused")), self.actual)
        ventanas = []
        pendientes = [(arbol, None)]
        while pendientes:
            nodo, escritorio = pendientes.pop()
            if nodo.get("type") == "workspace":
                escritorio = nodo["name"]
            if nodo.get("type") in ("con", "floating_con") and (nodo.get("app_id") or nodo.get("window")
                                                              or nodo.get("pid")) and not nodo.get("nodes"):
                marca = next((m for m in nodo.get("marks", []) if m.startswith("_min")), None)
                minimizada = escritorio == "__i3_scratch"
                ventanas.append({
                    "id": nodo["id"],
                    "titulo": nodo.get("name") or "",
                    "icono": self.iconos.de(nodo.get("app_id")
                                            or (nodo.get("window_properties") or {}).get("class") or ""),
                    "escritorio": marca.split("_")[2] if minimizada and marca else escritorio,
                    "minimizada": minimizada,
                    "marca": marca,
                    "enfocada": nodo.get("focused", False),
                    "rect": nodo.get("rect"),
                    "deco": nodo.get("deco_rect"),
                })
            pendientes += [(n, escritorio) for n in nodo.get("floating_nodes", []) + nodo.get("nodes", [])]
        vivas = {v["id"] for v in ventanas}
        self.mru = [i for i in self.mru if i in vivas]
        self.mru += [v["id"] for v in ventanas if v["id"] not in self.mru]
        orden_arbol = {v["id"]: n for n, v in enumerate(ventanas)}
        self.ventanas = sorted(ventanas, key=lambda v: orden_arbol[v["id"]])
        for i in list(self.maximizadas):
            if i not in vivas:
                del self.maximizadas[i]
        self.dialogos &= vivas
        self.pintar_tareas()
        for n, b in enumerate(self.paginas, 1):
            ctx = b.get_style_context()
            (ctx.add_class if str(n) == self.actual else ctx.remove_class)("actual")
            ocupado = any(v["escritorio"] == str(n) for v in ventanas)
            (ctx.add_class if ocupado else ctx.remove_class)("ocupado")
        return False

    def del_escritorio(self):
        """Ventanas del escritorio actual (también las minimizadas desde él), en orden estable."""
        return [v for v in self.ventanas if v["escritorio"] == self.actual]

    def pintar_tareas(self):
        lista = sorted(self.del_escritorio(), key=lambda v: v["id"])  # orden de apertura
        vistas = set()
        for v in lista:
            vistas.add(v["id"])
            b = self.botones_tareas.get(v["id"])
            if not b:
                b = Gtk.Button()
                b.set_relief(Gtk.ReliefStyle.NONE)
                b.set_can_focus(False)
                b.get_style_context().add_class("tarea")
                b.add(imagen(v["icono"]))
                b.connect("button-release-event", self.clic_tarea)
                self.botones_tareas[v["id"]] = b
                self.tareas.pack_start(b, False, False, 0)
                b.show_all()
            b.ventana = v
            b.set_tooltip_text(v["titulo"])
            ctx = b.get_style_context()
            (ctx.add_class if v["enfocada"] else ctx.remove_class)("enfocada")
            (ctx.add_class if v["minimizada"] else ctx.remove_class)("minimizada")
            self.tareas.reorder_child(b, -1)
        for ident in [i for i in self.botones_tareas if i not in vistas]:
            self.botones_tareas.pop(ident).destroy()

    def clic_tarea(self, b, evento):
        v = b.ventana
        if evento.button == Gdk.BUTTON_PRIMARY:
            if v["minimizada"]:
                self.restaurar(v)
            elif v["enfocada"]:
                self.minimizar(v)
            else:
                self.mandar(f"[con_id={v['id']}] focus")
        elif evento.button == Gdk.BUTTON_MIDDLE:
            self.mandar(f"[con_id={v['id']}] kill")
        elif evento.button == Gdk.BUTTON_SECONDARY:
            menu = Gtk.Menu()
            for texto, accion in (("Restaurar" if v["minimizada"] else "Minimizar",
                                   self.restaurar if v["minimizada"] else self.minimizar),
                                  ("Restaurar tamaño" if v["id"] in self.maximizadas else "Maximizar",
                                   self.maximizar),
                                  (None, None),
                                  ("Cerrar", lambda v: self.mandar(f"[con_id={v['id']}] kill"))):
                if texto is None:
                    menu.append(Gtk.SeparatorMenuItem())
                    continue
                elemento = Gtk.MenuItem(label=texto)
                elemento.connect("activate", lambda _e, a=accion: a(v))
                menu.append(elemento)
            menu.show_all()
            self.menu_abierto = menu
            menu.popup_at_widget(b, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)
        return False  # GtkButton termina su clic; si no, el botón se queda pulsado

    def ir_a(self, numero):
        self.mandar(f"workspace number {int(numero)}")

    def rueda_paginador(self, _b, evento):
        paso = sentido_rueda(evento)
        if paso:
            actual = int(self.actual) if self.actual.isdigit() else 1
            self.ir_a((actual - 1 - paso) % ESCRITORIOS + 1)
        return True

    # --- Órdenes de los atajos (orden.sh) --------------------------------------

    def ordenes(self):
        leyendo = ORDEN + ".leyendo"
        try:
            os.rename(ORDEN, leyendo)  # lo que llegue mientras tanto va a un archivo nuevo
            with open(leyendo) as f:
                lineas = f.read().split()
            os.remove(leyendo)
        except OSError:
            return True
        acciones = {
            "menu": self.alternar_menu,
            "alttab": lambda: self.alt_tab(1),
            "alttab-atras": lambda: self.alt_tab(-1),
            "minimizar": self.minimizar,
            "maximizar": self.maximizar,
            "izquierda": lambda: self.media_pantalla("izquierda"),
            "derecha": lambda: self.media_pantalla("derecha"),
            "salir": self.salir,
        }
        for linea in lineas:
            accion = acciones.get(linea)
            if accion:
                try:
                    accion()
                except Exception:  # noqa: BLE001 — una orden rara no debe tumbar el panel
                    print(f"panel: {linea}:", file=sys.stderr)
                    traceback.print_exc()
        return True


def main():
    GLib.set_prgname("panel")
    proveedor = Gtk.CssProvider()
    proveedor.load_from_path(os.path.join(CARPETA, "panel.css"))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), proveedor,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    panel = Panel()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, panel.aviso_teclas)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGWINCH, panel.ordenes)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, lambda: True)  # tema.sh: no aplica
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda: Gtk.main_quit() or False)
    comun.apuntar_pid("barra")  # teclas.sh y orden.sh avisan al PID de la barra
    panel.show_all()
    try:
        Gtk.main()
    finally:
        panel.inactividad.parar()
        comun.soltar_pid("barra")


if __name__ == "__main__":
    main()
