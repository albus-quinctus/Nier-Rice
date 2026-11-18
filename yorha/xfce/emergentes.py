"""Ventanas que abre panel.py: menú de aplicaciones, volumen, energía, calendario, salir
y el selector de Alt+Tab.

Cada una es una capa layer-shell que ocupa la pantalla menos el panel y es transparente
salvo su tarjeta: un clic fuera de la tarjeta o Esc la cierran (como los menús de XFCE).
Mientras está abierta se queda con el teclado.
"""
import contextlib
import os
import time

from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell  # versiones ya fijadas por panel.py

import sistema  # orden, en_segundo_plano, volumen, aplicaciones... (el panel ya lo ha cargado)


def icono(*nombres):
    """El primer nombre de icono que tiene el tema (los de Gruvbox y Adwaita no coinciden)."""
    tema = Gtk.IconTheme.get_default()
    return next((n for n in nombres if tema.has_icon(n)), nombres[-1])


def boton(texto=None, nombre_icono=None, al_pulsar=None, *args):
    b = Gtk.Button()
    caja = Gtk.Box(spacing=8)
    if nombre_icono:
        caja.pack_start(Gtk.Image.new_from_icon_name(nombre_icono, Gtk.IconSize.MENU), False, False, 0)
    if texto:
        caja.pack_start(Gtk.Label(label=texto, xalign=0), True, True, 0)
    b.add(caja)
    if al_pulsar:
        b.connect("clicked", lambda _b: al_pulsar(*args))
    return b


def etiqueta(texto, *clases):
    e = Gtk.Label(label=texto, xalign=0)
    for c in clases:
        e.get_style_context().add_class(c)
    return e


class Emergente(Gtk.Window):
    """Capa a pantalla completa (menos el panel) con una tarjeta pegada al botón que la abrió."""

    def __init__(self, nombre, al_cerrar, centrada=False):
        super().__init__()
        self.al_cerrar = al_cerrar
        self.cerrada = False
        visual = Gdk.Screen.get_default().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, f"panel-{nombre}")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP if not centrada
                                else GtkLayerShell.Layer.OVERLAY)
        for borde in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                      GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, borde, True)
        GtkLayerShell.set_exclusive_zone(self, -1 if centrada else 0)  # 0: no tapa el panel
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        self.get_style_context().add_class("capa-emergente")
        if centrada:
            self.get_style_context().add_class("oscurecida")

        fondo = Gtk.EventBox()
        fondo.connect("button-release-event", lambda _w, ev: ev.button in (1, 3) and self.cerrar())
        self.tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.tarjeta.get_style_context().add_class("emergente")
        self.tarjeta.get_style_context().add_class(nombre)
        marco = Gtk.EventBox()  # los clics dentro de la tarjeta no llegan al fondo
        marco.add(self.tarjeta)
        if centrada:
            marco.set_halign(Gtk.Align.CENTER)
            marco.set_valign(Gtk.Align.CENTER)
        else:
            marco.set_valign(Gtk.Align.START)  # bajo el panel, que va arriba
            marco.set_margin_top(4)
        self.marco = marco
        capas = Gtk.Overlay()
        capas.add(fondo)
        capas.add_overlay(marco)
        self.add(capas)
        self.connect("key-press-event", self.tecla)

    def junto_a(self, widget):
        """Pega la tarjeta debajo de widget (un botón del panel), hacia el lado con sitio."""
        ventana = widget.get_toplevel()
        x, _y = widget.translate_coordinates(ventana, 0, 0)
        ancho_panel = ventana.get_allocated_width()
        margen = GtkLayerShell.get_margin(ventana, GtkLayerShell.Edge.LEFT)
        if x + widget.get_allocated_width() / 2 < ancho_panel / 2:
            self.marco.set_halign(Gtk.Align.START)
            self.marco.set_margin_start(margen + x)
        else:
            self.marco.set_halign(Gtk.Align.END)
            self.marco.set_margin_end(margen + ancho_panel - x - widget.get_allocated_width())
        return self

    def tecla(self, _w, evento):
        if evento.keyval == Gdk.KEY_Escape:
            self.cerrar()
            return True
        return False

    def cerrar(self, *_):
        if not self.cerrada:
            self.cerrada = True
            self.destroy()
            self.al_cerrar(self)
        return False


# --- Menú de aplicaciones (como whiskermenu) ---------------------------------

class MenuApps(Emergente):
    MAXIMO = 80

    def __init__(self, al_cerrar, al_salir, al_bloquear):
        super().__init__("menu", al_cerrar)
        self.tarjeta.set_size_request(560, 520)
        cabecera = Gtk.Box(spacing=6)
        nombre = GLib.get_real_name()
        if not nombre or nombre == "Unknown":
            nombre = GLib.get_user_name()
        cabecera.pack_start(Gtk.Image.new_from_icon_name(icono("avatar-default", "user-info", "system-users"),
                                                         Gtk.IconSize.DND), False, False, 0)
        cabecera.pack_start(etiqueta(nombre, "usuario"), True, True, 0)
        for texto, nombre_icono, accion in (
                ("Bloquear", icono("system-lock-screen-symbolic", "changes-prevent-symbolic"), al_bloquear),
                ("Salir", icono("system-shutdown-symbolic", "system-log-out-symbolic"), al_salir)):
            b = boton(None, nombre_icono, lambda a=accion: (self.cerrar(), a()))
            b.set_tooltip_text(texto)
            b.set_relief(Gtk.ReliefStyle.NONE)
            cabecera.pack_start(b, False, False, 0)
        self.tarjeta.pack_start(cabecera, False, False, 0)

        self.buscar = Gtk.SearchEntry(placeholder_text="Buscar aplicaciones")
        self.buscar.connect("search-changed", lambda *_: self.filtrar())
        self.buscar.connect("activate", lambda *_: self.abrir_primera())
        self.buscar.connect("key-press-event", self.flechas)
        self.tarjeta.pack_start(self.buscar, False, False, 0)

        cuerpo = Gtk.Box(spacing=6)
        desplazable = Gtk.ScrolledWindow()
        desplazable.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.lista = Gtk.ListBox()
        self.lista.set_activate_on_single_click(True)
        self.lista.connect("row-activated", lambda _l, fila: self.abrir(fila.app))
        desplazable.add(self.lista)
        cuerpo.pack_start(desplazable, True, True, 0)
        self.categorias = Gtk.ListBox()
        self.categorias.get_style_context().add_class("categorias")
        self.categorias.connect("row-selected", lambda *_: self.filtrar())
        cuerpo.pack_start(self.categorias, False, False, 0)
        self.tarjeta.pack_start(cuerpo, True, True, 0)

        self.apps = sistema.aplicaciones()
        self.grupos = {"Todas": self.apps}
        self.grupos.update({n: lista for n, _ayuda, lista in sistema.agrupar_apps(self.apps)})
        for n in self.grupos:
            fila = Gtk.ListBoxRow()
            fila.nombre = n
            fila.add(etiqueta(n, "categoria"))
            self.categorias.add(fila)
        self.categorias.select_row(self.categorias.get_row_at_index(0))  # row-selected ya filtra

    def filtrar(self):
        texto = self.buscar.get_text().lower().strip()
        if texto:  # buscar mira en todas, como whiskermenu
            apps = sistema.buscar_apps(self.apps, texto, self.MAXIMO)
        else:
            fila = self.categorias.get_selected_row()
            apps = self.grupos.get(fila.nombre if fila else "Todas", self.apps)
        for hijo in self.lista.get_children():
            hijo.destroy()
        for app in apps:
            fila = Gtk.ListBoxRow()
            fila.app = app
            caja = Gtk.Box(spacing=10)
            imagen = Gtk.Image()
            if app.get_icon():
                imagen.set_from_gicon(app.get_icon(), Gtk.IconSize.LARGE_TOOLBAR)
            imagen.set_pixel_size(24)
            caja.pack_start(imagen, False, False, 0)
            textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            textos.pack_start(etiqueta(app.get_display_name(), "app"), False, False, 0)
            if app.get_description():
                d = etiqueta(app.get_description(), "descripcion")
                d.set_ellipsize(3)
                d.set_max_width_chars(40)
                textos.pack_start(d, False, False, 0)
            caja.pack_start(textos, True, True, 0)
            fila.add(caja)
            fila.set_tooltip_text(app.get_description())
            self.lista.add(fila)
        self.lista.show_all()

    def flechas(self, _w, evento):
        if evento.keyval == Gdk.KEY_Down:
            fila = self.lista.get_row_at_index(0)
            if fila:
                self.lista.select_row(fila)
                fila.grab_focus()
            return True
        return False

    def abrir_primera(self):
        fila = self.lista.get_row_at_index(0)
        if fila:
            self.abrir(fila.app)

    def abrir(self, app):
        with contextlib.suppress(GLib.Error):
            app.launch([], None)
        self.cerrar()

    def show_all(self):
        super().show_all()
        self.buscar.grab_focus()


# --- Volumen (como el plugin de PulseAudio) ----------------------------------

class Volumen(Emergente):
    def __init__(self, al_cerrar):
        super().__init__("volumen", al_cerrar)
        self.tarjeta.set_size_request(340, -1)
        self.deslizadores = {}
        self.pendiente = {}  # último valor de cada deslizador aún por mandar
        for tipo, titulo, iconos in (
                ("sink", "Salida", ("audio-volume-high-symbolic",)),
                ("source", "Micrófono", ("audio-input-microphone-symbolic",))):
            self.tarjeta.pack_start(etiqueta(titulo, "titulo"), False, False, 0)
            fila = Gtk.Box(spacing=6)
            silencio = Gtk.ToggleButton()
            silencio.set_relief(Gtk.ReliefStyle.NONE)
            silencio.set_tooltip_text("Silenciar")
            silencio.add(Gtk.Image.new_from_icon_name(icono(*iconos, "audio-volume-high"),
                                                      Gtk.IconSize.MENU))
            escala = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
            escala.set_value_pos(Gtk.PositionType.RIGHT)
            escala.set_sensitive(False)
            fila.pack_start(silencio, False, False, 0)
            fila.pack_start(escala, True, True, 0)
            self.tarjeta.pack_start(fila, False, False, 0)
            self.deslizadores[tipo] = (escala, silencio)
            sistema.en_segundo_plano(lambda t=tipo: sistema.volumen(t),
                                     lambda r, t=tipo: self._con_volumen(t, r))
        self.tarjeta.pack_start(etiqueta("Dispositivo de salida", "titulo"), False, False, 0)
        self.salidas = Gtk.ComboBoxText()
        self.salidas.set_sensitive(False)
        self.tarjeta.pack_start(self.salidas, False, False, 0)
        sistema.en_segundo_plano(salidas, self._con_salidas)
        self.musica = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.tarjeta.pack_start(self.musica, False, False, 0)
        sistema.en_segundo_plano(sistema.reproductores, self._con_reproductores)
        self.tarjeta.pack_start(Gtk.Separator(), False, False, 4)
        mezclador = boton("Mezclador de audio…", icono("multimedia-volume-control", "audio-card"),
                          self._mezclador)
        mezclador.set_relief(Gtk.ReliefStyle.NONE)
        self.tarjeta.pack_start(mezclador, False, False, 0)

    def _con_volumen(self, tipo, resultado):
        if self.cerrada:
            return
        valor, mudo = resultado
        escala, silencio = self.deslizadores[tipo]
        if valor is None:
            return
        escala.set_value(min(valor, 100))
        silencio.set_active(mudo)
        escala.set_sensitive(True)
        escala.connect("value-changed", self._mover, tipo)
        silencio.connect("toggled", lambda b: sistema.en_segundo_plano(
            lambda: sistema.orden(["pactl", f"set-{tipo}-mute", sistema.nombre_pactl(tipo),
                                   "1" if b.get_active() else "0"]), lambda _r: None))

    def _mover(self, escala, tipo):
        # Un solo pactl a la vez; si se arrastra rápido, gana el último valor
        self.pendiente[tipo] = escala.get_value()
        if not getattr(self, f"ocupado_{tipo}", False):
            self._mandar(tipo)

    def _mandar(self, tipo):
        valor = self.pendiente.pop(tipo, None)
        if valor is None:
            setattr(self, f"ocupado_{tipo}", False)
            return
        setattr(self, f"ocupado_{tipo}", True)
        sistema.en_segundo_plano(lambda: sistema.poner_volumen(tipo, valor),
                                 lambda _r: self._mandar(tipo))

    def _con_salidas(self, lista):
        if self.cerrada or not lista:
            return
        defecto, opciones = lista
        for nombre, descripcion in opciones:
            self.salidas.append(nombre, descripcion)
        self.salidas.set_active_id(defecto)
        self.salidas.set_sensitive(len(opciones) > 1)
        self.salidas.connect("changed", lambda c: c.get_active_id() and sistema.en_segundo_plano(
            lambda: sistema.orden(["pactl", "set-default-sink", c.get_active_id()]), lambda _r: None))

    def _con_reproductores(self, lista):
        if self.cerrada or not lista:
            return
        for bus, titulo, artista, estado in lista[:3]:
            self.musica.pack_start(Gtk.Separator(), False, False, 4)
            self.musica.pack_start(etiqueta(titulo + (f" · {artista}" if artista else ""), "cancion"),
                                   False, False, 0)
            controles = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
            for nombre_icono, metodo in (("media-skip-backward", "Previous"),
                                         ("media-playback-pause" if estado == "Playing"
                                          else "media-playback-start", "PlayPause"),
                                         ("media-skip-forward", "Next")):
                b = boton(None, nombre_icono, self._musica, bus, metodo)
                b.set_relief(Gtk.ReliefStyle.NONE)
                controles.pack_start(b, False, False, 0)
            self.musica.pack_start(controles, False, False, 0)
        self.musica.show_all()

    def _musica(self, bus, metodo):
        sistema.en_segundo_plano(lambda: sistema.mandar_reproductor(bus, metodo), lambda _r: None)

    def _mezclador(self):
        sistema.lanzar(["pavucontrol"])
        self.cerrar()


def salidas():
    """(salida por defecto, [(nombre, descripción)]) de pactl, o None."""
    defecto = sistema.orden(["pactl", "get-default-sink"])
    texto = sistema.orden(["pactl", "list", "sinks"])
    if defecto is None or texto is None:
        return None
    opciones = []
    for bloque in texto.split("Sink #")[1:]:
        nombre = descripcion = None
        for linea in bloque.splitlines():
            linea = linea.strip()
            if linea.startswith("Name: "):
                nombre = linea[6:]
            elif linea.startswith("Description: "):
                descripcion = linea[13:]
        if nombre:
            opciones.append((nombre, descripcion or nombre))
    return defecto.strip(), opciones


# --- Energía (como el plugin del gestor de energía) --------------------------

def baterias():
    """[(nombre, %, estado en español, tiempo restante o "")] de cada batería."""
    estados = {"Charging": "cargando", "Discharging": "descargando", "Full": "llena",
               "Not charging": "en espera (límite de carga)"}
    lista = []
    for ruta in sorted(Gio.File.new_for_path("/sys/class/power_supply").enumerate_children(
            "standard::name", Gio.FileQueryInfoFlags.NONE, None), key=lambda i: i.get_name()):
        nombre = ruta.get_name()
        if not nombre.startswith("BAT"):
            continue
        base = f"/sys/class/power_supply/{nombre}"
        try:
            capacidad = int(_leer(f"{base}/capacity"))
            estado = _leer(f"{base}/status")
        except (OSError, ValueError):
            continue
        lista.append((("Batería interna" if nombre == "BAT0" else "Batería externa"), capacidad,
                      estados.get(estado, estado.lower()), _tiempo(base, estado)))
    return lista


def _leer(ruta):
    with open(ruta) as f:
        return f.read().strip()


def _tiempo(base, estado):
    """Tiempo hasta vaciarse (o llenarse) con el consumo de ahora, si el sistema lo da."""
    try:
        tipo = "energy" if os.path.exists(f"{base}/energy_now") else "charge"
        consumo = int(_leer(f"{base}/{'power' if tipo == 'energy' else 'current'}_now"))
        ahora = int(_leer(f"{base}/{tipo}_now"))
        lleno = int(_leer(f"{base}/{tipo}_full"))
    except (OSError, ValueError):
        return ""
    if consumo <= 0 or estado not in ("Charging", "Discharging"):
        return ""
    horas = (ahora if estado == "Discharging" else max(0, lleno - ahora)) / consumo
    return f"{int(horas)} h {int(horas * 60) % 60:02d} min"


def brillo():
    """(directorio en /sys, valor actual 0-100) de la pantalla interna, o None."""
    for ruta in sorted(os.listdir("/sys/class/backlight")) if os.path.isdir("/sys/class/backlight") else []:
        base = f"/sys/class/backlight/{ruta}"
        try:
            return base, round(100 * int(_leer(f"{base}/brightness")) / int(_leer(f"{base}/max_brightness")))
        except (OSError, ValueError, ZeroDivisionError):
            continue
    return None


def poner_brillo(base, porcentaje):
    """Brillo a través de logind, como teclas.sh: sin root ni programas extra."""
    try:
        maximo = int(_leer(f"{base}/max_brightness"))
    except (OSError, ValueError):
        return
    valor = max(1, round(maximo * porcentaje / 100))
    sistema.orden(["busctl", "call", "org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                   "org.freedesktop.login1.Session", "SetBrightness", "ssu", "backlight",
                   os.path.basename(base), str(valor)])


class Energia(Emergente):
    def __init__(self, al_cerrar, presentacion, al_presentacion):
        super().__init__("energia", al_cerrar)
        self.tarjeta.set_size_request(320, -1)
        lista = baterias()
        if not lista:
            self.tarjeta.pack_start(etiqueta("No hay batería.", "titulo"), False, False, 0)
        for nombre, capacidad, estado, tiempo in lista:
            self.tarjeta.pack_start(etiqueta(f"{nombre}: {capacidad} %", "titulo"), False, False, 0)
            self.tarjeta.pack_start(etiqueta(estado + (f", {tiempo}" if tiempo else ""), "descripcion"),
                                    False, False, 0)
        actual = brillo()
        if actual:
            self.tarjeta.pack_start(Gtk.Separator(), False, False, 4)
            self.tarjeta.pack_start(etiqueta("Brillo", "titulo"), False, False, 0)
            escala = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 5, 100, 5)
            escala.set_value(actual[1])
            escala.set_value_pos(Gtk.PositionType.RIGHT)
            escala.connect("value-changed", lambda e: self._brillo(actual[0], e.get_value()))
            self.tarjeta.pack_start(escala, False, False, 0)
            self.brillo_pendiente = None
        self.tarjeta.pack_start(Gtk.Separator(), False, False, 4)
        fila = Gtk.Box(spacing=8)
        fila.pack_start(etiqueta("Modo presentación", "titulo"), True, True, 0)
        interruptor = Gtk.Switch(active=presentacion)
        interruptor.set_tooltip_text("No bloquea ni apaga la pantalla por inactividad")
        interruptor.connect("notify::active", lambda s, _p: al_presentacion(s.get_active()))
        fila.pack_start(interruptor, False, False, 0)
        self.tarjeta.pack_start(fila, False, False, 0)

    def _brillo(self, base, valor):
        hubo = self.brillo_pendiente is not None
        self.brillo_pendiente = valor
        if not hubo:
            GLib.timeout_add(80, self._mandar_brillo, base)

    def _mandar_brillo(self, base):
        valor, self.brillo_pendiente = self.brillo_pendiente, None
        sistema.en_segundo_plano(lambda: poner_brillo(base, valor), lambda _r: None)
        return False


# --- Calendario (al pulsar el reloj) -----------------------------------------

class Calendario(Emergente):
    def __init__(self, al_cerrar):
        super().__init__("calendario", al_cerrar)
        self.tarjeta.pack_start(etiqueta(time.strftime("%A, %-d de %B de %Y").capitalize(), "titulo"),
                                False, False, 0)
        self.tarjeta.pack_start(Gtk.Calendar(), False, False, 0)


# --- Salir (como xfce4-session-logout) ---------------------------------------

class Salir(Emergente):
    def __init__(self, al_cerrar, al_bloquear, al_cerrar_sesion):
        super().__init__("salir", al_cerrar, centrada=True)
        self.tarjeta.pack_start(etiqueta(f"Salir de la sesión de {GLib.get_user_name()}", "titulo"),
                                False, False, 6)
        rejilla = Gtk.Box(spacing=8, homogeneous=True)
        for texto, nombre_icono, accion in (
                ("Bloquear", icono("system-lock-screen", "changes-prevent"), al_bloquear),
                ("Suspender", icono("system-suspend", "media-playback-pause"),
                 lambda: sistema.lanzar(["systemctl", "suspend"])),
                ("Hibernar", icono("system-hibernate", "system-suspend-hibernate", "drive-harddisk"),
                 lambda: sistema.lanzar(["systemctl", "hibernate"])),
                ("Reiniciar", icono("system-reboot", "view-refresh"),
                 lambda: sistema.lanzar(["systemctl", "reboot"])),
                ("Apagar", icono("system-shutdown", "application-exit"),
                 lambda: sistema.lanzar(["systemctl", "poweroff"])),
                ("Cerrar sesión", icono("system-log-out", "application-exit"), al_cerrar_sesion)):
            b = Gtk.Button()
            caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            imagen = Gtk.Image.new_from_icon_name(nombre_icono, Gtk.IconSize.DIALOG)
            imagen.set_pixel_size(48)
            caja.pack_start(imagen, False, False, 0)
            caja.pack_start(Gtk.Label(label=texto), False, False, 0)
            b.add(caja)
            b.connect("clicked", lambda _b, a=accion: (self.cerrar(), a()))
            rejilla.pack_start(b, True, True, 0)
        self.tarjeta.pack_start(rejilla, False, False, 0)
        cancelar = Gtk.Button(label="Cancelar", halign=Gtk.Align.END)
        cancelar.connect("clicked", self.cerrar)
        self.tarjeta.pack_start(cancelar, False, False, 6)


# --- Selector de Alt+Tab -------------------------------------------------------

class Selector(Emergente):
    """Iconos de las ventanas del escritorio; Tab avanza (lo manda sway) y al soltar Alt se elige."""

    def __init__(self, al_cerrar, ventanas, al_elegir):
        super().__init__("selector", al_cerrar, centrada=True)
        self.get_style_context().remove_class("oscurecida")
        self.ventanas, self.al_elegir = ventanas, al_elegir
        self.indice = 1 % len(ventanas)
        fila = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.casillas = []
        for v in ventanas:
            casilla = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            casilla.get_style_context().add_class("casilla")
            imagen = Gtk.Image.new_from_icon_name(v["icono"], Gtk.IconSize.DIALOG)
            imagen.set_pixel_size(48)
            if v["minimizada"]:
                imagen.set_opacity(0.5)
            casilla.pack_start(imagen, False, False, 6)
            fila.pack_start(casilla, False, False, 0)
            self.casillas.append(casilla)
        self.tarjeta.pack_start(fila, False, False, 0)
        self.titulo = etiqueta("", "titulo")
        self.titulo.set_xalign(0.5)
        self.titulo.set_ellipsize(3)
        self.titulo.set_max_width_chars(60)
        self.tarjeta.pack_start(self.titulo, False, False, 0)
        self.connect("key-release-event", self.soltar)
        # Al recibir el teclado llega también el estado de Alt: entonces se mira si sigue pulsado
        # (y, por si el teclado tardara, otra vez un poco después de aparecer)
        self.connect("focus-in-event", lambda *_: GLib.timeout_add(50, self.comprobar_alt) and False)
        self.connect("map-event", lambda *_: GLib.timeout_add(300, self.comprobar_alt) and False)
        self.marcar()

    def marcar(self):
        for i, casilla in enumerate(self.casillas):
            (casilla.get_style_context().add_class if i == self.indice
             else casilla.get_style_context().remove_class)("elegida")
        self.titulo.set_text(self.ventanas[self.indice]["titulo"])

    def mover(self, paso):
        self.indice = (self.indice + paso) % len(self.ventanas)
        self.marcar()

    def comprobar_alt(self):
        # Si Alt se soltó antes de que la capa tuviera el teclado, no llegará el evento
        estado = Gdk.Keymap.get_for_display(Gdk.Display.get_default()).get_modifier_state()
        if not estado & Gdk.ModifierType.MOD1_MASK:
            self.elegir()
        return False

    def soltar(self, _w, evento):
        if evento.keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Meta_L):
            self.elegir()
            return True
        return False

    def tecla(self, w, evento):
        if evento.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.elegir()
            return True
        if evento.keyval in (Gdk.KEY_Right, Gdk.KEY_Tab):
            self.mover(1)
            return True
        if evento.keyval in (Gdk.KEY_Left, Gdk.KEY_ISO_Left_Tab):
            self.mover(-1)
            return True
        return super().tecla(w, evento)

    def elegir(self):
        if not self.cerrada:
            elegida = self.ventanas[self.indice]
            self.cerrar()
            self.al_elegir(elegida)
