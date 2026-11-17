#!/usr/bin/env python3
"""Barra YoRHa para sway: siete pestañas como las del menú de NieR:Automata.

Seis son escritorios (UNO … SEIS) y la séptima, SISTEMA, muestra la hora y la
batería y abre el panel de ajustes al pulsarla.

Uso: python3 -I barra.py [--tema claro|oscuro]
Sin --tema usa el guardado por tema.sh en $XDG_RUNTIME_DIR/yorha-tema. Con SIGUSR1
vuelve a leer ese archivo. Su PID queda en $XDG_RUNTIME_DIR/yorha-barra.pid, y
tema.sh, teclas.sh y escritorio.sh solo le mandan señales a ese proceso (nunca con
pkill -f).

Habla con sway por su socket (protocolo i3-ipc): solo se redibuja cuando cambia
de escritorio. No sondea, no usa red y no lanza ninguna shell (para el deslizamiento
lanza grim directamente).

Además, todo en este mismo proceso para no tener más programas abiertos:
- hora y batería en la pestaña SISTEMA (lee /sys una vez por minuto); sin cargador,
  avisa al 10 % e hiberna al 5 % (o suspende, si no se puede hibernar);
- aviso NieR de volumen y brillo: teclas.sh escribe el valor en
  $XDG_RUNTIME_DIR/yorha-aviso y manda SIGUSR2;
- al abrir una ventana, dos trazos que se despliegan sobre sus bordes de arriba
  y abajo y se desvanecen (solo cubren los bordes, no se tragan los clics de dentro);
- al cambiar de escritorio (Mod+1…6 o las pestañas), el escritorio actual sale
  deslizándose hacia un lado, como en el original (ver Deslizamiento).
Sway no anima nada de esto: son capas aparte que se ocultan al terminar.
"""
import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA)  # con python3 -I la carpeta del script no está en sys.path
sys.path.insert(1, os.path.join(CARPETA, "xfce"))  # la bandeja es la misma que la del panel de trabajo
import comun  # noqa: E402

NOMBRES = ["UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS"]
ALTO = 95             # alto reservado arriba; la franja NieR del fondo queda justo debajo
PUNTERO = (40, 26)    # tamaño del puntero en px
TRAZO = 10            # alto de los trazos del efecto de ventana nueva
SEGMENTOS = 20        # segmentos del aviso de volumen y brillo
BATERIA_BAJA = 15     # % sin cargador: la batería de la barra se pinta en rojo
BATERIA_AVISO = 10    # % sin cargador: notificación
BATERIA_HIBERNAR = 5  # % sin cargador: hiberna para no perder el trabajo

MAGIA = b"i3-ipc"
COMANDO, OBTENER_ESCRITORIOS, SUSCRIBIR, OBTENER_ARBOL = 0, 1, 2, 4
EVENTO_ESCRITORIO, EVENTO_VENTANA = 0x80000000, 0x80000003
# De los eventos de ventana solo interesa "new"; sway escribe "change" primero, así que
# basta mirar el principio en vez de decodificar el árbol entero de cada foco o título
VENTANA_NUEVA = re.compile(rb'\s*\{\s*"change"\s*:\s*"new"')


class Sway:
    """Cliente mínimo del protocolo i3-ipc sobre el socket de sway."""

    def __init__(self):
        self.ruta = os.environ.get("SWAYSOCK") or os.environ["I3SOCK"]
        self.socket = None  # el de las órdenes se abre una vez y se reutiliza

    def _conectar(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.ruta)
        return s

    @staticmethod
    def _enviar(s, tipo, carga=b""):
        s.sendall(MAGIA + struct.pack("=II", len(carga), tipo) + carga)

    @staticmethod
    def _leer(s, n):
        datos = b""
        while len(datos) < n:
            trozo = s.recv(n - len(datos))
            if not trozo:
                raise ConnectionError("sway cerró el socket")
            datos += trozo
        return datos

    def _recibir(self, s):
        """(tipo, cuerpo sin decodificar) del siguiente mensaje."""
        largo, tipo = struct.unpack("=II", self._leer(s, 14)[6:])
        return tipo, self._leer(s, largo)

    def pedir(self, tipo, carga=b""):
        # Si sway se reinició o cerró el socket, se reconecta una vez y se repite
        for intento in (1, 2):
            try:
                if not self.socket:
                    self.socket = self._conectar()
                self._enviar(self.socket, tipo, carga)
                return json.loads(self._recibir(self.socket)[1])
            except (ConnectionError, OSError):
                if self.socket:
                    self.socket.close()
                    self.socket = None
                if intento == 2:
                    raise

    def escritorios(self):
        return self.pedir(OBTENER_ESCRITORIOS)

    def ir_a(self, numero):
        self.pedir(COMANDO, f"workspace number {int(numero)}".encode())

    def rect_de(self, ident):
        """(origen de su pantalla, x, y, ancho, alto) del contenedor ident, con x e y
        relativas a esa pantalla (las capas layer-shell se colocan por pantalla), o None."""
        pendientes = [(self.pedir(OBTENER_ARBOL), None)]
        while pendientes:
            nodo, salida = pendientes.pop()
            if nodo.get("type") == "output":
                salida = nodo
            if nodo.get("id") == ident and salida:
                r, o = nodo["rect"], salida["rect"]
                return (o["x"], o["y"]), r["x"] - o["x"], r["y"] - o["y"], r["width"], r["height"]
            pendientes += [(n, salida) for n in nodo.get("nodes", []) + nodo.get("floating_nodes", [])]
        return None

    def suscribir(self, al_cambiar, al_abrir_ventana):
        s = self._conectar()
        self._enviar(s, SUSCRIBIR, b'["workspace", "window"]')
        self._recibir(s)

        def leer(_fuente, _condicion):
            try:
                tipo, cuerpo = self._recibir(s)
            except (ConnectionError, OSError):
                Gtk.main_quit()  # sway se fue: la sesión se está cerrando
                return False
            # Un evento raro (un título con bytes inválidos, sway sin responder a tiempo)
            # se ignora: si la excepción saliera de aquí, GLib quitaría el vigilante
            # y la barra se quedaría congelada sin enterarse de nada más
            try:
                if tipo == EVENTO_ESCRITORIO:
                    al_cambiar(json.loads(cuerpo))
                elif tipo == EVENTO_VENTANA and VENTANA_NUEVA.match(cuerpo):
                    al_abrir_ventana(json.loads(cuerpo)["container"]["id"])
            except (ConnectionError, OSError, ValueError, KeyError, TypeError):
                pass
            return True

        GLib.io_add_watch(s.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, leer)
        self.socket_eventos = s


class Escritorio(Gtk.Box):
    """Puntero + botón largo. El puntero aparece al pasar el ratón o si está activo.

    Al pulsarla llama a al_pulsar (cambiar a ese escritorio, o abrir el panel en SISTEMA).
    """

    def __init__(self, numero, nombre, al_pulsar):
        super().__init__(spacing=4)
        self.numero = numero
        self.get_style_context().add_class("escritorio")
        self.puntero = Gtk.Image()
        self.puntero.set_size_request(*PUNTERO)
        self.puntero.get_style_context().add_class("puntero")
        # Pestaña como las del menú del juego: cuadrado de icono y nombre
        pestana = Gtk.Box(spacing=8)
        pestana.get_style_context().add_class("pestana")
        cuadro = Gtk.Box()
        cuadro.set_valign(Gtk.Align.CENTER)
        cuadro.get_style_context().add_class("cuadro")
        pestana.pack_start(cuadro, False, False, 0)
        self.etiqueta = Gtk.Label(label=nombre, xalign=0)
        self.etiqueta.set_ellipsize(3)  # Pango.EllipsizeMode.END: nunca ensancha la pestaña
        pestana.pack_start(self.etiqueta, True, True, 0)
        self.boton = Gtk.Button()
        self.boton.add(pestana)
        self.boton.get_style_context().add_class("largo")
        self.boton.set_relief(Gtk.ReliefStyle.NONE)
        self.boton.set_can_focus(False)
        self.boton.connect("clicked", lambda *_: al_pulsar())
        self.boton.connect("enter-notify-event", lambda *_: self._marcar("encima", True))
        self.boton.connect("leave-notify-event", lambda *_: self._marcar("encima", False))
        self.pack_start(self.puntero, False, False, 0)
        self.pack_start(self.boton, True, True, 0)

    def _marcar(self, clase, si):
        ctx = self.get_style_context()
        (ctx.add_class if si else ctx.remove_class)(clase)

    def poner_puntero(self, pixbuf):
        if pixbuf:
            self.puntero.set_from_pixbuf(pixbuf)
        else:
            self.puntero.clear()


def leer_numero(ruta):
    with open(ruta) as f:
        return int(f.read())


def bateria():
    """(porcentaje, cargando, descargando) sumando todas las baterías (el T480 puede tener
    dos), o None. Ni cargando ni descargando = enchufado y parado en el límite de carga."""
    ahora = lleno = 0
    cargando = descargando = False
    for ruta in glob.glob("/sys/class/power_supply/BAT*"):
        tipo = "energy" if os.path.exists(f"{ruta}/energy_now") else "charge"
        try:
            # Se leen las dos cifras antes de sumar, para no contar media batería
            esta, total = leer_numero(f"{ruta}/{tipo}_now"), leer_numero(f"{ruta}/{tipo}_full")
            with open(f"{ruta}/status") as f:
                estado = f.read().strip()
        except (OSError, ValueError):
            continue
        ahora, lleno = ahora + esta, lleno + total
        cargando |= estado == "Charging"
        descargando |= estado == "Discharging"
    return (min(100, round(100 * ahora / lleno)), cargando, descargando) if lleno else None


def notificar(titulo, texto, urgente=False):
    """Notificación por D-Bus (la muestra mako), sin esperar respuesta ni lanzar notify-send."""
    with contextlib.suppress(GLib.Error):
        Gio.bus_get_sync(Gio.BusType.SESSION, None).call(
            "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
            "org.freedesktop.Notifications", "Notify",
            GLib.Variant("(susssasa{sv}i)", ("YoRHa", 0, "battery-caution", titulo, texto, [],
                                             {"urgency": GLib.Variant("y", 2 if urgente else 1)},
                                             -1)),
            None, Gio.DBusCallFlags.NONE, -1, None, None, None)


def lanzar(argv, al_terminar=None):
    """Arranca argv sin shell y recoge al hijo al salir; al_terminar(correcto) si se pasa."""
    try:
        hijo = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        if al_terminar:
            al_terminar(False)
        return
    GLib.child_watch_add(GLib.PRIORITY_DEFAULT, hijo.pid,
                         lambda _pid, estado: al_terminar and al_terminar(estado == 0))


def hibernar():
    """Hiberna; si no se puede (sin swap o sin resume configurado), suspende."""
    lanzar(["systemctl", "hibernate"],
           lambda bien: bien or lanzar(["systemctl", "suspend"]))


class AlertaBateria:
    """Sin cargador: un aviso al 10 % y, al 5 %, hibernar (una vez por descarga).
    La usan esta barra y el panel de trabajo (xfce/panel.py)."""

    def __init__(self):
        self.dada = 0  # alerta ya dada en esta descarga: 0, BATERIA_AVISO o BATERIA_HIBERNAR

    def revisar(self, valor, descargando):
        if not descargando:
            self.dada = 0  # al enchufarlo se rearman los avisos
        elif valor <= BATERIA_HIBERNAR and self.dada != BATERIA_HIBERNAR:
            self.dada = BATERIA_HIBERNAR
            notificar(f"Batería al {valor} %", "Hibernando en 10 segundos. Enchufa el cargador "
                      "para cancelarlo.", urgente=True)
            GLib.timeout_add_seconds(10, self._hibernar_si_sigue)
        elif valor <= BATERIA_AVISO and not self.dada:
            self.dada = BATERIA_AVISO
            notificar(f"Batería al {valor} %", "Conecta el cargador: al "
                      f"{BATERIA_HIBERNAR} % el equipo hibernará.", urgente=True)

    @staticmethod
    def _hibernar_si_sigue():
        estado = bateria()
        if estado and estado[2]:  # si se ha enchufado durante la espera, no hiberna
            hibernar()
        return False


def monitor_en(x, y):
    """Monitor de GDK que contiene el punto (x, y) en coordenadas globales de sway."""
    return Gdk.Display.get_default().get_monitor_at_point(x, y)


def capa(ventana, nombre, capa_gls=GtkLayerShell.Layer.OVERLAY):
    """Prepara una superficie layer-shell transparente que no coge el teclado."""
    visual = Gdk.Screen.get_default().get_rgba_visual()
    if visual:
        ventana.set_visual(visual)
    GtkLayerShell.init_for_window(ventana)
    GtkLayerShell.set_namespace(ventana, nombre)
    GtkLayerShell.set_layer(ventana, capa_gls)
    GtkLayerShell.set_keyboard_mode(ventana, GtkLayerShell.KeyboardMode.NONE)
    GtkLayerShell.set_exclusive_zone(ventana, -1)
    ventana.connect("realize", sin_clics)


def sin_clics(ventana):
    """Deja pasar los clics a lo de debajo si hay pycairo; si no, la capa dura poco."""
    try:
        import cairo
        ventana.get_window().input_shape_combine_region(cairo.Region(), 0, 0)
    except (ImportError, TypeError, AttributeError, KeyError):
        pass  # KeyError: hay pycairo pero falta su puente con GObject (python3-gi-cairo)


class Trazo(Gtk.Window):
    """Trazo NieR sobre el borde de arriba o de abajo de una ventana recién abierta."""

    def __init__(self, monitor, x, y, ancho, tema):
        super().__init__(title="efecto-yorha")
        capa(self, "yorha-efecto")
        if monitor:
            GtkLayerShell.set_monitor(self, monitor)
        self.get_style_context().add_class("efecto")
        self.get_style_context().add_class(tema)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.TOP, y)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.LEFT, x)
        self.set_size_request(ancho, TRAZO)
        self.linea = Gtk.Box()
        self.linea.get_style_context().add_class("trazo")
        # Empieza como un punto en el centro y se despliega hasta las esquinas
        self.inicio = Gtk.CssProvider()
        self.inicio.load_from_data(f".trazo {{ margin: 0 {ancho // 2 - 4}px; }}".encode())
        self.linea.get_style_context().add_provider(self.inicio, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.add(self.linea)
        self.show_all()
        GLib.timeout_add(40, self._abrir)
        GLib.timeout_add(420, lambda: self.get_style_context().add_class("fuera") and False)
        GLib.timeout_add(800, lambda: self.destroy() and False)

    def _abrir(self):
        self.linea.get_style_context().remove_provider(self.inicio)
        self.linea.get_style_context().add_class("abierto")
        return False


class Aviso(Gtk.Window):
    """Aviso de volumen o brillo abajo en el centro: nombre, segmentos y cifra."""

    def __init__(self):
        super().__init__(title="aviso-yorha")
        capa(self, "yorha-aviso")
        self.get_style_context().add_class("aviso")
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 140)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.get_style_context().add_class("tarjeta")
        cabecera = Gtk.Box(spacing=12)
        cuadro = Gtk.Box()
        cuadro.set_valign(Gtk.Align.CENTER)
        cuadro.get_style_context().add_class("cuadro")
        self.nombre = Gtk.Label(xalign=0)
        self.cifra = Gtk.Label(xalign=1)
        cabecera.pack_start(cuadro, False, False, 0)
        cabecera.pack_start(self.nombre, True, True, 0)
        cabecera.pack_end(self.cifra, False, False, 0)
        fila = Gtk.Box(spacing=3, homogeneous=True)
        self.segmentos = []
        for _ in range(SEGMENTOS):
            seg = Gtk.Box()
            seg.get_style_context().add_class("segmento")
            fila.pack_start(seg, True, True, 0)
            self.segmentos.append(seg)
        caja.pack_start(cabecera, False, False, 0)
        caja.pack_start(fila, False, False, 0)
        self.add(caja)
        self.ocultar = None

    def mostrar(self, tema, nombre, valor, silencio):
        ctx = self.get_style_context()
        for t in ("claro", "oscuro"):
            (ctx.add_class if t == tema else ctx.remove_class)(t)
        (ctx.add_class if silencio else ctx.remove_class)("silencio")
        self.nombre.set_text(nombre)
        self.cifra.set_text("SILENCIO" if silencio else f"{valor}%")
        llenos = round(valor * SEGMENTOS / 100)
        for i, seg in enumerate(self.segmentos):
            (seg.get_style_context().add_class if i < llenos
             else seg.get_style_context().remove_class)("lleno")
        if not self.get_visible():
            self.show_all()
        GLib.idle_add(lambda: ctx.add_class("visible") and False)
        if self.ocultar:
            GLib.source_remove(self.ocultar)
        self.ocultar = GLib.timeout_add(1300, self._apagar)

    def _apagar(self):
        self.get_style_context().remove_class("visible")
        self.ocultar = GLib.timeout_add(250, self._esconder)
        return False

    def _esconder(self):
        self.ocultar = None
        self.hide()
        return False


def curva(x, x1=0.65, y1=-0.01, x2=0.0, y2=0.95):
    """Curva cúbica de Bézier (como bezier = in-out,0.65,-0.01,0,0.95 del original): y para x."""
    def punto(t, a, b):
        return 3 * a * t * (1 - t) ** 2 + 3 * b * t ** 2 * (1 - t) + t ** 3
    bajo, alto = 0.0, 1.0
    for _ in range(20):  # la x de la curva crece con t: búsqueda binaria
        t = (bajo + alto) / 2
        bajo, alto = (t, alto) if punto(t, x1, x2) < x else (bajo, t)
    return punto((bajo + alto) / 2, y1, y2)


# grim saca PPM (sin comprimir, lo más rápido) si GdkPixbuf sabe leerlo; si no, PNG sin comprimir
GRIM = shutil.which("grim")
FORMATO = (["-t", "ppm"] if any(f.get_name() == "pnm" for f in GdkPixbuf.Pixbuf.get_formats())
           else ["-t", "png", "-l", "0"])


class Deslizamiento(Gtk.Window):
    """Cambio de escritorio como el del original (animation = workspaces,1,5,in-out,slide).

    Sway no anima nada, así que se imita: antes de cambiar se captura el escritorio actual
    con grim (a memoria, sin archivo), se pone encima en una capa, se cambia de escritorio
    por debajo y la captura sale por un lado dejando ver el nuevo. La capa respeta la barra
    (zona exclusiva 0), así que la barra no se mueve. Va en la capa más alta para que se vea
    también sobre una ventana a pantalla completa. Mientras dura (un cuarto de segundo), la capa
    se traga los clics.
    """

    DURACION = 0.25  # la mitad que workspaces,1,5 del original: el cambio se nota menos lento

    def __init__(self):
        super().__init__(title="deslizamiento-yorha")
        capa(self, "yorha-deslizamiento")
        GtkLayerShell.set_exclusive_zone(self, 0)  # debajo de la barra, no encima
        for borde in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                      GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, borde, True)
        self.get_style_context().add_class("deslizamiento")
        # Gtk.Layout y no Gtk.Fixed: no pide más tamaño al mover la imagen fuera de la capa
        self.fijo = Gtk.Layout()
        self.imagen = Gtk.Image()
        self.fijo.put(self.imagen, 0, 0)
        self.add(self.fijo)
        self.fijo.show_all()
        self.tick = self.limite = None
        self.al_cubrir = None

    def lanzar(self, pixbuf, monitor, a_la_izquierda, al_cubrir):
        """Pone la captura encima; cuando ya se ve, llama a al_cubrir() (que cambia de
        escritorio por debajo) y la desliza fuera: a la izquierda si se va a uno mayor."""
        self.terminar()
        if monitor:
            GtkLayerShell.set_monitor(self, monitor)
        self.imagen.set_from_pixbuf(pixbuf)
        self.fijo.move(self.imagen, 0, 0)
        self.ancho = pixbuf.get_width()
        self.signo = -1 if a_la_izquierda else 1
        self.al_cubrir = al_cubrir
        self.pintados = 0
        self.inicio = None
        self.show()
        self.tick = self.add_tick_callback(self._avanzar)
        # Si sway no llega a mostrarla (pantalla apagada), se cambia igual y se quita
        self.limite = GLib.timeout_add(250, self._sin_mostrar)

    def _cubrir(self):
        """Ya tapa el escritorio viejo: ahora se puede cambiar sin que se vea el salto."""
        if self.limite:
            GLib.source_remove(self.limite)
            self.limite = None
        al_cubrir, self.al_cubrir = self.al_cubrir, None
        if al_cubrir:
            al_cubrir()

    def _sin_mostrar(self):
        self.limite = None
        self.terminar()
        return GLib.SOURCE_REMOVE

    def activo(self):
        return self.get_visible()

    def terminar(self, cambiar=True):
        """Quita la capa; si aún no se había cambiado de escritorio, cambia ya (o, con
        cambiar=False porque se ha pedido otro escritorio, ya no)."""
        if not cambiar:
            self.al_cubrir = None
        self._cubrir()
        if self.tick:
            self.remove_tick_callback(self.tick)
            self.tick = None
        self.hide()
        self.imagen.clear()  # suelta la captura (unos 6 MB)

    def _avanzar(self, _widget, reloj):
        if not self.get_mapped():
            return GLib.SOURCE_CONTINUE
        # GTK pinta después de este paso: en el segundo fotograma la captura ya está en pantalla
        self.pintados += 1
        if self.pintados < 2:
            return GLib.SOURCE_CONTINUE
        ahora = reloj.get_frame_time() / 1e6
        if self.inicio is None:
            self.inicio = ahora
            self._cubrir()
            return GLib.SOURCE_CONTINUE
        t = min(1.0, (ahora - self.inicio) / self.DURACION)
        self.fijo.move(self.imagen, round(self.signo * curva(t) * self.ancho), 0)
        if t < 1.0:
            return GLib.SOURCE_CONTINUE
        self.tick = None  # GTK la quita al devolver SOURCE_REMOVE
        self.terminar()
        return GLib.SOURCE_REMOVE


class Barra(Gtk.Window):
    def __init__(self, sway, tema):
        super().__init__(title="barra-yorha")
        self.sway = sway
        self.set_decorated(False)
        self.get_style_context().add_class("yorha")

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, "yorha-barra")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        for borde in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, borde, True)
        GtkLayerShell.set_exclusive_zone(self, ALTO)
        self.set_size_request(-1, ALTO)

        fila = Gtk.Box(spacing=6)
        fila.get_style_context().add_class("fila")
        fila.set_valign(Gtk.Align.START)
        escritorios = Gtk.Box(spacing=6, homogeneous=True)
        self.escritorios = []
        for i, nombre in enumerate(NOMBRES, start=1):
            e = Escritorio(i, nombre, al_pulsar=lambda n=i: self.cambiar_a(n))
            escritorios.pack_start(e, True, True, 0)
            self.escritorios.append(e)
        # Séptima pestaña, como SISTEMA en el juego: hora y batería; abre el panel de ajustes
        self.sistema = Escritorio(0, "", al_pulsar=abrir_ajustes)
        self.sistema.get_style_context().add_class("sistema")
        self.sistema._marcar("ocupado", True)  # nunca se atenúa como un escritorio vacío
        escritorios.pack_start(self.sistema, True, True, 0)
        fila.pack_start(escritorios, True, True, 0)
        # Bandeja (Claude Desktop, Proton VPN...): a la derecha, solo si hay algún icono
        self.bandeja = None
        try:
            import bandeja
            self.bandeja = bandeja.Bandeja(al_cambiar=self.mostrar_bandeja)
        except (ImportError, GLib.Error) as error:
            print(f"barra: sin bandeja: {error}", file=sys.stderr)
        if self.bandeja:
            self.bandeja.get_style_context().add_class("bandeja-yorha")
            self.bandeja.set_valign(Gtk.Align.START)
            self.bandeja.set_no_show_all(True)
            fila.pack_end(self.bandeja, False, False, 0)
        self.add(fila)

        self.tema = None
        self.aviso = None
        self.deslizamiento = None
        self.capturando = None  # Gio.Cancellable de la captura en curso
        self.pedidos = 0        # cuenta de cambios pedidos: solo cuenta el último
        self.pendiente = None  # actualización ya programada (agrupa ráfagas de eventos)
        self.alerta = AlertaBateria()
        self.cambiar_tema(tema)
        self.actualizar()
        self.reloj()
        sway.suscribir(self.cambio_escritorio, self.ventana_nueva)

    def mostrar_bandeja(self):
        self.bandeja.set_visible(bool(self.bandeja.iconos))

    def reloj(self):
        """Hora y batería; se vuelve a llamar al empezar el minuto siguiente."""
        texto = time.strftime("%H:%M")
        estado = bateria()
        bajo = False
        if estado:  # sin batería (o sin poder leerla) solo se muestra la hora
            valor, cargando, descargando = estado
            texto += f" · {valor}%{'↑' if cargando else ''}"  # ↑ = cargando
            bajo = valor <= BATERIA_BAJA and not cargando
            self.alerta.revisar(valor, descargando)
        self.sistema.etiqueta.set_text(texto)
        self.sistema._marcar("bajo", bajo)
        GLib.timeout_add_seconds(60 - time.localtime().tm_sec, self.reloj)
        return False

    def cambio_escritorio(self, datos):
        # Mover una ventana o cambiar de escritorio manda varios eventos seguidos
        # (init, focus, empty...): se pide la lista a sway una sola vez al terminar la ráfaga
        if not self.pendiente:
            self.pendiente = GLib.idle_add(self.actualizar)

    def cambiar_a(self, numero):
        """Va al escritorio numero deslizando el actual hacia fuera (Mod+1…6 y las pestañas).

        Solo se anima si el escritorio nuevo se verá en la misma pantalla; si no, si la
        captura falla o tarda, o si llega mientras otro deslizamiento sigue en marcha
        (pulsaciones seguidas), se cambia sin más.
        """
        def orden():
            with contextlib.suppress(ConnectionError, OSError, ValueError):
                self.sway.ir_a(numero)

        self.pedidos += 1
        pedido = self.pedidos
        en_marcha = self.parar_cambio_en_curso()
        actual, monitor = self.monitor_para_deslizar(numero)
        # Con otra animación a medias, grim podría capturar su imagen en vez del escritorio
        if en_marcha or not monitor:
            orden()
            return
        # Se captura lo que tapará la capa: la pantalla entera menos la barra de arriba
        g = monitor.get_geometry()
        try:
            proceso = Gio.Subprocess.new(
                [GRIM, "-s", "1", *FORMATO, "-g", f"{g.x},{g.y + ALTO} {g.width}x{g.height - ALTO}", "-"],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            orden()
            return
        cancelar = self.capturando = Gio.Cancellable()
        # Si grim tarda, no se hace esperar al cambio de escritorio
        GLib.timeout_add(150, lambda: cancelar.cancel() or False)

        def capturada(_proceso, resultado):
            if pedido != self.pedidos:
                return  # ya se pidió otro escritorio: ese cambio manda
            self.capturando = None
            pixbuf = self.leer_captura(proceso, resultado)
            if not pixbuf:
                orden()
                return
            if not self.deslizamiento:
                self.deslizamiento = Deslizamiento()
            self.deslizamiento.lanzar(pixbuf, monitor, numero > (actual.get("num") or 0), orden)

        proceso.communicate_async(None, cancelar, capturada)

    def parar_cambio_en_curso(self):
        """Corta la captura y el deslizamiento que estén en marcha; dice si había alguno."""
        en_marcha = bool(self.capturando) or bool(self.deslizamiento and self.deslizamiento.activo())
        if self.capturando:
            self.capturando.cancel()
            self.capturando = None
        if self.deslizamiento:
            self.deslizamiento.terminar(cambiar=False)
        return en_marcha

    def monitor_para_deslizar(self, numero):
        """(escritorio actual, monitor) si el cambio a numero se puede animar; si no, el monitor es None."""
        try:
            escritorios = self.sway.escritorios()
        except (ConnectionError, OSError, ValueError):
            escritorios = []
        actual = next((e for e in escritorios if e.get("focused")), None)
        destino = next((e for e in escritorios if e.get("num") == numero), None)
        monitor = actual and monitor_en(actual["rect"]["x"], actual["rect"]["y"])
        if not GRIM or not monitor or actual.get("num") == numero \
                or (destino and destino.get("output") != actual.get("output")):
            return actual, None
        return actual, monitor

    @staticmethod
    def leer_captura(proceso, resultado):
        """Pixbuf con la salida de grim, o None si falló o tardó demasiado."""
        try:
            _, salida, _ = proceso.communicate_finish(resultado)
            if proceso.get_successful() and salida:
                lector = GdkPixbuf.PixbufLoader()
                lector.write_bytes(salida)
                lector.close()
                return lector.get_pixbuf()
        except GLib.Error:
            pass  # grim falló o tardó demasiado: se cambia sin animación
        return None

    def escritorio_pedido(self):
        """SIGWINCH: escritorio.sh dejó el número pedido en yorha-escritorio. Se borra al
        leerlo, para que otro SIGWINCH (p. ej. de una terminal) no repita un cambio viejo."""
        ruta = os.path.join(comun.RUNTIME, "yorha-escritorio")
        try:
            with open(ruta) as f:
                numero = int(f.read().strip())
            os.remove(ruta)
        except (OSError, ValueError):
            return GLib.SOURCE_CONTINUE
        self.cambiar_a(numero)
        return GLib.SOURCE_CONTINUE

    def mostrar_aviso(self):
        """SIGUSR2: teclas.sh dejó "volumen|brillo VALOR si|no" en yorha-aviso."""
        try:
            with open(os.path.join(comun.RUNTIME, "yorha-aviso")) as f:
                tipo, valor, silencio = f.read().split()
            valor = max(0, min(100, int(valor)))
        except (OSError, ValueError):
            return GLib.SOURCE_CONTINUE
        if not self.aviso:
            self.aviso = Aviso()
        self.aviso.mostrar(self.tema, "BRILLO" if tipo == "brillo" else "VOLUMEN",
                           valor, silencio == "si")
        return GLib.SOURCE_CONTINUE

    def ventana_nueva(self, ident):
        # Espera un instante a que sway coloque la ventana y toma su tamaño final
        def lanzar():
            try:
                rect = self.sway.rect_de(ident)
            except (ConnectionError, OSError, ValueError):
                rect = None
            if rect and rect[3] > 20 and rect[4] > 20:
                origen, x, y, ancho, alto = rect
                monitor = monitor_en(*origen)
                Trazo(monitor, x, y, ancho, self.tema)
                Trazo(monitor, x, y + alto - TRAZO, ancho, self.tema)
            return False
        GLib.timeout_add(60, lanzar)

    def cambiar_tema(self, tema):
        ctx = self.get_style_context()
        if self.tema:
            ctx.remove_class(self.tema)
        self.tema = tema
        ctx.add_class(tema)
        ruta = os.path.join(CARPETA, "fondos", f"puntero-{tema}.png")
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(ruta, *PUNTERO)
        except GLib.Error:
            pixbuf = None  # sin fondos/ generados la barra funciona igual, sin puntero
        for e in self.escritorios + [self.sistema]:
            e.poner_puntero(pixbuf)

    def releer_tema(self):
        """SIGUSR1: tema.sh ya guardó el tema nuevo; la barra lo lee en vez de adivinarlo."""
        self.cambiar_tema(comun.tema_actual())
        return GLib.SOURCE_CONTINUE

    def actualizar(self):
        self.pendiente = None
        try:
            activos = {e["num"]: e for e in self.sway.escritorios()}
        except (ConnectionError, OSError, ValueError):
            return False
        for e in self.escritorios:
            info = activos.get(e.numero, {})
            for clase, si in (("activo", info.get("focused")), ("urgente", info.get("urgent")),
                              ("ocupado", e.numero in activos)):
                e._marcar(clase, bool(si))
        return False


def abrir_ajustes():
    """Abre (o cierra, si ya está abierto) el panel, igual que pulsar solo Super; sin shell."""
    lanzar(["python3", "-I", os.path.join(CARPETA, "ajustes.py")])


def main():
    parser = argparse.ArgumentParser(description="Barra YoRHa para sway")
    parser.add_argument("--tema", choices=["claro", "oscuro"])
    args = parser.parse_args()

    proveedor = Gtk.CssProvider()
    proveedor.load_from_path(os.path.join(CARPETA, "barra.css"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    barra = Barra(Sway(), args.tema or comun.tema_actual())
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, barra.releer_tema)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2, barra.mostrar_aviso)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGWINCH, barra.escritorio_pedido)
    # El PID se publica solo cuando ya se atienden las señales: antes, nadie las manda
    comun.apuntar_pid("barra")
    barra.show_all()
    try:
        Gtk.main()
    finally:
        comun.soltar_pid("barra")


if __name__ == "__main__":
    main()
