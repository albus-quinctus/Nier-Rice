"""Piezas NieR (paleta, Boton, Deslizador, títulos, la capa Pantalla, ejecutar): menú, buscador y ARCHBOX."""
import os
import signal

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, GtkLayerShell  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
# Sin tocar sys.path: quien importa piezas (ajustes.py, lanzador.py, ARCHBOX) ya ha puesto esta carpeta
import comun  # noqa: E402
from sistema import en_segundo_plano  # noqa: E402

PALETAS = {  # las de hyprland-yorha (color-light.scss y color-dark.scss)
    "claro": {"tan": "#c2bda6", "marron": "#48463d", "crema": "#f4f0e1", "acento": "#cd664d"},
    "oscuro": {"tan": "#57544a", "marron": "#dad4bb", "crema": "#413d34", "acento": "#cd664d"},
}
PUNTERO = (40, 26)


def raton_movido(ventana, evento):
    """True si el ratón se ha movido de verdad, no solo la lista bajo él al desplazarse.

    Un movimiento real saca a la ventana del modo teclado (ventana.por_teclado)."""
    pos = (round(evento.x_root), round(evento.y_root))
    antes = getattr(ventana, "ultimo_raton", None)
    ventana.ultimo_raton = pos
    if antes is not None and pos != antes:
        ventana.por_teclado = False
        return True
    return False


# --- Piezas NieR ------------------------------------------------------------

class Boton(Gtk.EventBox):
    """Botón NieR: puntero, bordes que aparecen arriba y abajo y relleno que barre.

    Como en el juego hay un solo cursor: pasar el ratón por encima o llegar con las
    flechas lo selecciona, y su ayuda sale en la barra de abajo. Enter o espacio lo pulsan.
    """

    def __init__(self, panel, texto, al_pulsar=None, valor=None, icono=None, grande=False, ayuda="",
                 dato=None):
        super().__init__()
        self.panel = panel
        self.ayuda = ayuda
        self.dato = dato  # lo que representa (la aplicación, en las listas de apps)
        self.al_pulsar = al_pulsar
        self.ocupado = False
        self.set_can_focus(True)
        self.get_style_context().add_class("boton")
        if grande:
            self.get_style_context().add_class("grande")
        fila = Gtk.Box(spacing=0)
        self.puntero = Gtk.Image()
        self.puntero.set_size_request(*PUNTERO)
        self.puntero.get_style_context().add_class("puntero")
        if panel.pixbuf_puntero:
            self.puntero.set_from_pixbuf(panel.pixbuf_puntero)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.get_style_context().add_class("caja")
        arriba, abajo = Gtk.Box(), Gtk.Box()
        arriba.get_style_context().add_class("borde")
        abajo.get_style_context().add_class("borde")
        cuerpo = Gtk.Box(spacing=12)
        cuerpo.get_style_context().add_class("cuerpo")
        cuadro = Gtk.Box()
        cuadro.set_valign(Gtk.Align.CENTER)
        cuadro.get_style_context().add_class("cuadro")
        cuerpo.pack_start(cuadro, False, False, 0)
        if icono:
            imagen = Gtk.Image.new_from_gicon(icono, Gtk.IconSize.LARGE_TOOLBAR)
            cuerpo.pack_start(imagen, False, False, 0)
        self.etiqueta = Gtk.Label(label=texto, xalign=0)
        self.etiqueta.set_ellipsize(3)  # Pango.EllipsizeMode.END
        cuerpo.pack_start(self.etiqueta, True, True, 0)
        self.valor = Gtk.Label(label=valor or "", xalign=1)
        self.valor.get_style_context().add_class("valor")
        cuerpo.pack_end(self.valor, False, False, 0)
        caja.pack_start(arriba, False, False, 0)
        caja.pack_start(cuerpo, False, False, 0)
        caja.pack_start(abajo, False, False, 0)
        fila.pack_start(self.puntero, False, False, 0)
        fila.pack_start(caja, True, True, 0)
        self.add(fila)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("enter-notify-event", self._al_entrar)
        self.connect("motion-notify-event", self._al_mover)
        self.connect("leave-notify-event", self._al_salir)
        self.connect("button-release-event", self._al_soltar)
        self.connect("focus-in-event", self._al_enfocar)
        self.connect("focus-out-event", lambda *_: self.marcar("encima", False))
        self.connect("key-press-event", self._al_teclear)

    def _al_entrar(self, _w, _evento):
        # Bajando con las flechas la lista se desplaza bajo el ratón quieto: eso no cuenta
        # como pasar por encima (si no, el cursor volvería atrás al botón bajo el ratón)
        if self.panel.por_teclado:
            return
        # Si se está escribiendo en el buscador, el ratón no le quita el foco
        if isinstance(self.get_toplevel().get_focus(), Gtk.Entry):
            self.marcar("encima", True)
            self.panel.describir(self)
        else:
            self.grab_focus()

    def _al_mover(self, _w, evento):
        if raton_movido(self.panel, evento) and not self.has_focus():
            self._al_entrar(self, evento)
        return False

    def _al_salir(self, _w, evento):
        if evento.detail != Gdk.NotifyType.INFERIOR and not self.has_focus():
            self.marcar("encima", False)

    def _al_enfocar(self, *_):
        self.marcar("encima", True)
        self.panel.describir(self)

    def _al_teclear(self, _w, evento):
        if evento.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if self.al_pulsar and not self.ocupado:
                self.al_pulsar(self)
            return True
        return False

    def _al_soltar(self, _w, evento):
        if evento.button == Gdk.BUTTON_PRIMARY and self.al_pulsar and not self.ocupado:
            self.al_pulsar(self)
        return True

    def marcar(self, clase, si):
        ctx = self.get_style_context()
        (ctx.add_class if si else ctx.remove_class)(clase)

    def poner_valor(self, texto):
        self.valor.set_text(texto)

    def esperar(self, trabajo, al_terminar):
        """Ejecuta trabajo en segundo plano; mientras tanto el botón no admite clics."""
        self.ocupado = True
        self.poner_valor("...")

        def fin(resultado):
            self.ocupado = False
            al_terminar(resultado)
        en_segundo_plano(trabajo, fin)


class Deslizador(Gtk.Box):
    """Fila de volumen: nombre, barra de segmentos NieR y porcentaje."""

    def __init__(self, texto, valor, al_cambiar, silencio=False, al_silenciar=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.get_style_context().add_class("deslizador")
        cabecera = Gtk.Box(spacing=12)
        cuadro = Gtk.Box()
        cuadro.set_valign(Gtk.Align.CENTER)
        cuadro.get_style_context().add_class("cuadro")
        nombre = Gtk.Label(label=texto, xalign=0)
        nombre.set_ellipsize(3)
        self.cifra = Gtk.Label(xalign=1)
        self.cifra.get_style_context().add_class("valor")
        cabecera.pack_start(cuadro, False, False, 0)
        cabecera.pack_start(nombre, True, True, 0)
        if al_silenciar:
            self.mudo = Gtk.Button(label="SILENCIO")
            self.mudo.set_relief(Gtk.ReliefStyle.NONE)
            self.mudo.get_style_context().add_class("mudo")
            self.mudo.connect("clicked", lambda *_: self._silenciar(al_silenciar))
            cabecera.pack_end(self.mudo, False, False, 0)
        cabecera.pack_end(self.cifra, False, False, 0)
        self.escala = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.escala.set_draw_value(False)
        self.escala.set_value(valor)
        self.escala.get_style_context().add_class("nier")
        self.escala.set_can_focus(False)  # las flechas mueven el cursor, no el volumen
        self.pack_start(cabecera, False, False, 0)
        self.pack_start(self.escala, False, False, 0)
        self.silencio = silencio
        self._pintar()
        self.al_cambiar = al_cambiar
        self.enviando = False
        self.escala.connect("value-changed", self._cambio)

    def _pintar(self):
        self.cifra.set_text(f"{int(self.escala.get_value())}%")
        ctx = self.get_style_context()
        (ctx.add_class if self.silencio else ctx.remove_class)("silenciado")

    def _silenciar(self, al_silenciar):
        self.mudo.set_sensitive(False)

        def fin(silencio):
            self.silencio = silencio  # el estado real que devuelve pactl
            self.mudo.set_sensitive(True)
            self._pintar()
        en_segundo_plano(al_silenciar, fin)

    def _cambio(self, *_):
        self._pintar()
        if not self.enviando:
            self._enviar()

    def _enviar(self):
        """Una sola orden en marcha: al arrastrar, los valores intermedios se saltan y el
        último siempre llega (y en orden, sin hilos que terminen al revés)."""
        valor = self.escala.get_value()
        self.enviando = True

        def fin(_r):
            self.enviando = False
            if self.escala.get_value() != valor:
                self._enviar()
        en_segundo_plano(lambda: self.al_cambiar(valor), fin)


def titulo(texto, nivel=1):
    etiqueta = Gtk.Label(label=texto, xalign=0)
    etiqueta.get_style_context().add_class("titulo")
    etiqueta.get_style_context().add_class(f"titulo{nivel}")
    return etiqueta


def aviso(texto):
    etiqueta = Gtk.Label(label=texto, xalign=0)
    etiqueta.set_line_wrap(True)
    etiqueta.get_style_context().add_class("aviso")
    return etiqueta


def nuevo_boton_app(panel, app, abrir):
    boton = Boton(panel, app.get_display_name(), lambda _b: abrir(app), icono=app.get_icon(),
                  ayuda=app.get_description() or "", dato=app)
    boton.get_style_context().add_class("app")
    return boton


def puntero(tema):
    try:
        return GdkPixbuf.Pixbuf.new_from_file_at_size(
            os.path.join(CARPETA, "fondos", f"puntero-{tema}.png"), *PUNTERO)
    except GLib.Error:
        return None  # sin fondos/ generados todo funciona igual, sin puntero


class Pantalla(Gtk.Window):
    """Capa a pantalla completa con el teclado para ella (el panel y el buscador).

    Zona exclusiva 0: ocupa la pantalla menos la barra de escritorios, que queda
    arriba como la fila de pestañas del menú del juego.
    """

    def __init__(self, nombre, clase, tema):
        super().__init__(title=f"{nombre}-yorha")
        self.tema = tema
        self.saliendo = False
        self.por_teclado = False  # con las flechas, el ratón quieto no mueve el cursor
        self.pixbuf_puntero = None
        self.buscar = None  # buscador al que van las letras que se escriben
        visual = Gdk.Screen.get_default().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.get_style_context().add_class(clase)
        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_namespace(self, f"yorha-{nombre}")
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        GtkLayerShell.set_exclusive_zone(self, 0)
        for borde in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                      GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(self, borde, True)
        self.proveedor = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("motion-notify-event", lambda _w, e: raton_movido(self, e) and False)
        self.connect("key-press-event", self._al_teclear)
        # Si sway cierra la capa (p. ej. se desconecta su pantalla), el programa termina
        # en vez de quedarse invisible con el pidfile puesto
        self.connect("destroy", lambda *_: Gtk.main_quit())

    def cargar_estilo(self, archivos, e, extra=""):
        """Paleta del tema + las hojas de estilo + tamaños de letra según la pantalla (e = alto/1080)."""
        css = "".join(f"@define-color {k} {v};\n" for k, v in PALETAS[self.tema].items())
        for nombre in archivos:
            with open(os.path.join(CARPETA, nombre)) as f:
                css += f.read()
        css += (f".titulo1 {{ font-size: {round(48 * e)}px; }}\n"
                f".titulo2 {{ font-size: {round(28 * e)}px; }}\n"
                f".ayuda label {{ font-size: {round(22 * e)}px; }}\n" + extra)
        self.proveedor.load_from_data(css.encode())
        self.pixbuf_puntero = puntero(self.tema)

    def barra_ayuda(self, acciones):
        """Barra de abajo como la del juego: qué hace lo seleccionado y qué teclas hay."""
        barra = Gtk.Box(spacing=20)
        barra.get_style_context().add_class("ayuda")
        barra.set_valign(Gtk.Align.END)
        barra.set_hexpand(True)
        self.texto_ayuda = Gtk.Label(xalign=0)
        self.texto_ayuda.set_ellipsize(3)
        barra.pack_start(self.texto_ayuda, True, True, 0)
        for tecla, accion in acciones:
            caja = Gtk.Box(spacing=8)
            t = Gtk.Label(label=tecla)
            t.get_style_context().add_class("tecla")
            caja.pack_start(t, False, False, 0)
            caja.pack_start(Gtk.Label(label=accion), False, False, 0)
            barra.pack_start(caja, False, False, 0)
        return barra

    def describir(self, boton):
        self.texto_ayuda.set_text(boton.ayuda or "")

    def _al_teclear(self, _w, evento):
        tecla = evento.keyval
        if tecla in (Gdk.KEY_Up, Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter,
                     Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Page_Up, Gdk.KEY_Page_Down):
            self.por_teclado = True
        if tecla == Gdk.KEY_Escape:
            self.atras()
            return True
        # Escribir con el cursor fuera del buscador lleva las letras a él, como en el
        # inventario (el espacio no: pulsa el botón seleccionado)
        if self.buscar and not self.buscar.has_focus() and tecla != Gdk.KEY_space \
                and Gdk.keyval_to_unicode(tecla) >= 32 \
                and not evento.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            self.buscar.grab_focus_without_selecting()
            return self.buscar.event(evento)
        return self.al_teclear(tecla)

    def al_teclear(self, _tecla):
        return False

    def atras(self):
        self.cerrar()

    def entrar(self):
        """Tras el primer dibujo, para que haya transición de entrada."""
        self.get_style_context().add_class("visible")
        return False

    def cerrar(self, espera=220):
        """Animación de salida (quitar "visible") y fin del programa."""
        if self.saliendo:
            return False
        self.saliendo = True
        self.get_style_context().remove_class("visible")
        GLib.timeout_add(espera, lambda: Gtk.main_quit() and False)
        return False


def ejecutar(nombre, crear):
    """Abre la ventana de crear(tema) o, si ya estaba abierta, la cierra (misma tecla)."""
    if comun.alternar_instancia(nombre):
        return
    try:
        GLib.set_prgname(f"yorha-{nombre}")
        ventana = crear(comun.tema_actual())
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, ventana.cerrar)
        ventana.show_all()
        GLib.timeout_add(30, ventana.entrar)
        Gtk.main()
    finally:
        comun.soltar_pid(nombre)
