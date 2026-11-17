#!/usr/bin/env python3
"""Visor de imágenes hecho a mano, en lugar de Ristretto (sway, solo Wayland).

Uso: python3 -I visor.py [archivo|carpeta]...
Con un archivo enseña esa imagen y recorre las de su carpeta (ordenadas por nombre como
en el gestor de archivos); con una carpeta empieza por su primera imagen; con varios
archivos recorre solo esos. Vigila la carpeta: lo que se añade o se borra aparece o se va.

Teclas:
  ← → RePág AvPág Inicio Fin, rueda      imagen anterior / siguiente / primera / última
  + - Ctrl+rueda (alrededor del ratón)   acercar / alejar; arrastrar mueve la imagen
  1 o Ctrl+0                             tamaño real; 0 ajusta a la ventana
  [ ] o Ctrl+← Ctrl+→                    girar (el archivo no cambia); H y V voltean
  T miniaturas · F11, F o doble clic pantalla completa · F5 pase de diapositivas (5 s)
  I o Ctrl+I información · O abrir con · Supr a la papelera · clic derecho: menú
  Esc sale de pantalla completa o cierra · Ctrl+Q o Ctrl+W cierra

Con el escritorio YoRHa (~/.config/sway/config enlazado a yorha/sway.conf) se viste con
visor.css y sigue el tema claro u oscuro de tema.sh; en trabajo usa el tema GTK del
sistema. Las imágenes se leen en otro hilo (GIO), así una foto grande o una carpeta con
miles no paran la ventana. Las aplicaciones se abren solo con Gio, nunca con un shell.
"""
import os
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA)  # con python3 -I la carpeta del script no está en sys.path
import comun  # noqa: E402

PASO = 1.25          # cada + o - multiplica o divide el zoom por esto
ZOOM = (0.02, 32.0)  # zoom mínimo y máximo
DIAPOSITIVA = 5000   # ms entre imágenes del pase
MINIATURA = 88       # lado de las miniaturas, en píxeles
YORHA = comun.es_yorha()
# En trabajo solo lo imprescindible; el resto lo pone el tema GTK del sistema
CSS_TRABAJO = """
.lienzo { background-color: shade(@theme_bg_color, 0.92); }
.miniatura { padding: 2px; border: 2px solid transparent; border-radius: 4px; }
.miniatura.actual { border-color: @theme_selected_bg_color; }
.ficha { padding: 10px 14px; border-radius: 6px; background-color: alpha(@theme_bg_color, 0.92); }
.ficha .clave { opacity: 0.65; }
"""


def extensiones():
    """Extensiones que sabe abrir GdkPixbuf aquí (con los cargadores instalados)."""
    return {e.lower() for f in GdkPixbuf.Pixbuf.get_formats() if not f.is_disabled()
            for e in f.get_extensions()}


EXTENSIONES = extensiones()


def es_imagen(nombre):
    return os.path.splitext(nombre)[1][1:].lower() in EXTENSIONES


def imagenes_de(carpeta):
    """Imágenes de la carpeta (sin las ocultas), en orden natural: foto2 antes que foto10."""
    try:
        with os.scandir(carpeta) as entradas:
            nombres = [e.name for e in entradas if not e.name.startswith(".")
                       and es_imagen(e.name) and e.is_file()]
    except OSError:
        return []
    nombres.sort(key=lambda n: GLib.utf8_collate_key_for_filename(n, -1))
    return [os.path.join(carpeta, n) for n in nombres]


def lista_inicial(argumentos):
    """(rutas, índice de la primera que se enseña, carpeta que se vigila o None)."""
    rutas = [os.path.abspath(Gio.File.new_for_commandline_arg(a).get_path() or a)
             for a in argumentos] or [os.getcwd()]
    if len(rutas) == 1:
        ruta = rutas[0]
        if os.path.isdir(ruta):
            return imagenes_de(ruta), 0, ruta
        carpeta = os.path.dirname(ruta)
        lista = imagenes_de(carpeta)
        if ruta not in lista:  # sin extensión conocida u oculta: se intenta igual
            lista.append(ruta)
        return lista, lista.index(ruta), carpeta
    lista = []
    for ruta in rutas:
        lista += imagenes_de(ruta) if os.path.isdir(ruta) else [ruta]
    return lista, 0, None


def leer(ruta, cancelable, al_terminar, lado=0):
    """Carga ruta fuera del hilo de GTK y entrega al_terminar(resultado, error) en él.

    Con lado, una miniatura (Pixbuf) que cabe en un cuadrado de ese lado; sin él, la
    imagen entera como PixbufAnimation (también las fijas).
    """
    def leido(archivo, res, _dato):
        try:
            flujo = archivo.read_finish(res)
        except GLib.Error as e:
            return al_terminar(None, e)
        if lado:
            GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(flujo, lado, lado, True, cancelable,
                                                            cargado, flujo)
        else:
            GdkPixbuf.PixbufAnimation.new_from_stream_async(flujo, cancelable, cargado, flujo)

    def cargado(_origen, res, flujo):
        flujo.close_async(GLib.PRIORITY_LOW, None, None, None)
        try:
            tipo = GdkPixbuf.Pixbuf if lado else GdkPixbuf.PixbufAnimation
            al_terminar(tipo.new_from_stream_finish(res), None)
        except GLib.Error as e:
            al_terminar(None, e)

    Gio.File.new_for_path(ruta).read_async(GLib.PRIORITY_DEFAULT, cancelable, leido, None)


def cancelado(error):
    return error and error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)


def vestir(ventana, tema):
    """Clases de estilo de una ventana del visor (también los diálogos) según el tema."""
    if YORHA:
        ctx = ventana.get_style_context()
        ctx.add_class("visor")
        for t in ("claro", "oscuro"):
            (ctx.add_class if t == tema else ctx.remove_class)(t)


def boton(icono, ayuda, al_pulsar):
    """Botón de barra con icono simbólico (el color lo pone el CSS); no se queda el foco."""
    b = Gtk.Button(image=Gtk.Image.new_from_icon_name(icono, Gtk.IconSize.LARGE_TOOLBAR),
                   tooltip_text=ayuda, relief=Gtk.ReliefStyle.NONE, can_focus=False)
    b.connect("clicked", lambda _b: al_pulsar())
    return b


# --- Lienzo: la imagen con zoom, giro y arrastre -----------------------------

class Lienzo(Gtk.Layout):
    """Zona de la imagen. Pinta solo el trozo visible, escalado con GdkPixbuf (sin cairo):
    acercarse mucho a una foto grande no crea una copia enorme en memoria.

    El giro y el volteo se aplican al fotograma (rotate_simple/flip), nunca al archivo.
    Gtk.Layout no pide tamaño por lo que lleva dentro: la ventana se puede encoger.
    """

    def __init__(self, al_cambiar):
        super().__init__()
        self.al_cambiar = al_cambiar   # avisa al visor (el zoom va en la barra)
        self.foto = Gtk.Image()
        self.put(self.foto, 0, 0)
        self.fuente = None             # fotograma tal como viene del archivo
        self.orientada = None          # el mismo, girado y volteado
        self.iterador = None           # de las animaciones (GIF, WebP)
        self.reloj = self.pendiente = 0
        self.zoom, self.tamano, self.arrastre = 1.0, (0, 0), None
        # giro, espejo, ajustado y px, py (esquina del trozo visible, ya escalado): mostrar()
        self.mostrar(None)
        m = Gdk.EventMask
        self.add_events(m.BUTTON_PRESS_MASK | m.BUTTON_RELEASE_MASK | m.POINTER_MOTION_MASK
                        | m.SCROLL_MASK | m.SMOOTH_SCROLL_MASK)
        self.connect("size-allocate", self._al_cambiar_tamano)
        self.connect("button-press-event", self._al_pulsar)
        self.connect("button-release-event", self._al_soltar)
        self.connect("motion-notify-event", self._al_mover)

    # Imagen y fotogramas

    def mostrar(self, animacion):
        """Pone una imagen nueva (o ninguna) con el zoom ajustado y sin giros."""
        if self.reloj:
            GLib.source_remove(self.reloj)
            self.reloj = 0
        self.iterador = None
        self.giro, self.espejo, self.ajustado, self.px, self.py = 0, False, True, 0.0, 0.0
        if animacion is None:
            self.fuente = None
        elif animacion.is_static_image():
            # Las fotos del móvil vienen tumbadas con la orientación en el EXIF
            self.fuente = animacion.get_static_image().apply_embedded_orientation()
        else:
            self.iterador = animacion.get_iter(None)
            self.fuente = self.iterador.get_pixbuf()
            self._programar()
        self._orientar()

    def _programar(self):
        espera = self.iterador.get_delay_time()
        if espera >= 0:  # -1: el último fotograma se queda para siempre
            self.reloj = GLib.timeout_add(max(espera, 20), self._fotograma)

    def _fotograma(self):
        self.reloj = 0
        self.iterador.advance(None)
        self.fuente = self.iterador.get_pixbuf()
        self._orientar()
        self._programar()
        return False

    def _orientar(self):
        p = self.fuente
        if p is not None:
            if self.espejo:
                p = p.flip(True)
            if self.giro:  # rotate_simple gira al contrario de las agujas del reloj
                p = p.rotate_simple(GdkPixbuf.PixbufRotation((360 - self.giro) % 360))
        self.orientada = p
        self.redibujar()

    def girar(self, grados):
        self.giro = (self.giro + grados) % 360
        self._orientar()

    def voltear(self, vertical=False):
        """Volteo en pantalla, con la imagen ya girada: el espejo va antes que el giro."""
        self.espejo = not self.espejo
        self.giro = ((180 if vertical else 0) - self.giro) % 360
        self._orientar()

    # Zoom y posición

    def zoom_actual(self):
        p = self.orientada
        if not p or not self.ajustado:
            return self.zoom
        ancho, alto = self.get_allocated_width(), self.get_allocated_height()
        # Ajustar nunca amplía: una imagen pequeña se queda a su tamaño
        return min(1.0, ancho / p.get_width(), alto / p.get_height())

    def ajustar(self):
        self.ajustado = True
        self.redibujar()

    def ampliar(self, factor=1.0, valor=None, x=None, y=None):
        """Cambia el zoom (por factor o a valor) dejando quieto el punto (x, y)."""
        p = self.orientada
        if not p:
            return
        ancho, alto = self.get_allocated_width(), self.get_allocated_height()
        x = ancho / 2 if x is None else x
        y = alto / 2 if y is None else y
        antes = self.zoom_actual()
        ahora = min(max(valor or antes * factor, ZOOM[0]), ZOOM[1])
        for eje, lado, punto, imagen in (("px", ancho, x, p.get_width()),
                                         ("py", alto, y, p.get_height())):
            # Punto de la imagen bajo el ratón, que tras el cambio debe seguir bajo él
            origen = (lado - min(lado, imagen * antes)) / 2 - getattr(self, eje)
            u = (punto - origen) / antes
            setattr(self, eje, (lado - min(lado, imagen * ahora)) / 2 + u * ahora - punto)
        self.ajustado, self.zoom = False, ahora
        self.redibujar()

    def _al_cambiar_tamano(self, _w, a):
        # Solo si cambia: poner la imagen vuelve a repartir el sitio y no debe ser un bucle
        if (a.width, a.height) != self.tamano:
            self.tamano = (a.width, a.height)
            self.redibujar()

    def redibujar(self):
        if not self.pendiente:
            self.pendiente = GLib.idle_add(self._pintar, priority=GLib.PRIORITY_HIGH_IDLE + 20)

    def _pintar(self):
        self.pendiente = 0
        p = self.orientada
        ancho, alto = self.get_allocated_width(), self.get_allocated_height()
        if p is None or ancho < 2 or alto < 2:
            self.foto.clear()
            self.al_cambiar()
            return False
        z = self.zoom_actual()
        dw, dh = p.get_width() * z, p.get_height() * z
        vw, vh = max(1, min(ancho, round(dw))), max(1, min(alto, round(dh)))
        self.px = min(max(self.px, 0.0), dw - vw)
        self.py = min(max(self.py, 0.0), dh - vh)
        if (vw, vh) == (p.get_width(), p.get_height()):
            trozo = p  # a tamaño real y entera: sin copia
        else:
            trozo = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, p.get_has_alpha(), 8, vw, vh)
            trozo.fill(0)
            # De cerca, píxeles nítidos; de lejos, suavizado
            filtro = GdkPixbuf.InterpType.NEAREST if z >= 3 else GdkPixbuf.InterpType.BILINEAR
            p.scale(trozo, 0, 0, vw, vh, -self.px, -self.py, z, z, filtro)
        self.foto.set_from_pixbuf(trozo)
        self.move(self.foto, (ancho - vw) // 2, (alto - vh) // 2)
        self._cursor()
        self.al_cambiar()
        return False

    def movible(self):
        p = self.orientada
        z = self.zoom_actual()
        return bool(p) and (p.get_width() * z > self.get_allocated_width() + 1
                            or p.get_height() * z > self.get_allocated_height() + 1)

    def _cursor(self):
        ventana = self.get_bin_window()
        if ventana:
            nombre = ("grabbing" if self.arrastre else "grab") if self.movible() else "default"
            ventana.set_cursor(Gdk.Cursor.new_from_name(self.get_display(), nombre))

    # Arrastrar para mover la imagen ampliada

    def _al_pulsar(self, _w, ev):
        if ev.button == Gdk.BUTTON_PRIMARY and ev.type == Gdk.EventType.BUTTON_PRESS and self.movible():
            self.arrastre = (ev.x_root, ev.y_root, self.px, self.py)
            self._cursor()
        return False

    def _al_soltar(self, _w, ev):
        if ev.button == Gdk.BUTTON_PRIMARY and self.arrastre:
            self.arrastre = None
            self._cursor()
        return False

    def _al_mover(self, _w, ev):
        if self.arrastre:
            x, y, px, py = self.arrastre
            self.px, self.py = px - (ev.x_root - x), py - (ev.y_root - y)
            self.redibujar()
        return False


# --- Tira de miniaturas ------------------------------------------------------

class Tira(Gtk.ScrolledWindow):
    """Miniaturas de la carpeta abajo. Se leen de una en una, en otro hilo y cuando GTK está
    libre, empezando por las más cercanas a la imagen actual; con la tira oculta no se lee
    ninguna. Un clic en una salta a esa imagen."""

    def __init__(self, al_elegir):
        super().__init__()
        self.get_style_context().add_class("tira")
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.al_elegir = al_elegir
        self.caja = Gtk.Box(spacing=4)
        self.caja.connect("size-allocate", lambda *_: self.centrar_pendiente and self._centrar())
        self.add(self.caja)
        self.rutas = []
        self.botones = {}       # ruta → botón
        self.cola = []          # rutas sin miniatura, la siguiente al final
        self.actual = None
        self.leyendo = None     # Gio.Cancellable de la miniatura en curso
        self.centrar_pendiente = False
        self.connect("map", lambda *_: self._seguir())

    def poner(self, rutas):
        """Rehace la tira para otra lista; los botones que ya existían se aprovechan."""
        for hijo in self.caja.get_children():
            self.caja.remove(hijo)
        botones = {}
        for ruta in rutas:
            botones[ruta] = self.botones.get(ruta) or self._boton(ruta)
            self.caja.pack_start(botones[ruta], False, False, 0)
        self.botones, self.rutas = botones, rutas
        self.caja.show_all()
        self.cola = [r for r in rutas if not self.botones[r].leida]

    def _boton(self, ruta):
        b = Gtk.Button(relief=Gtk.ReliefStyle.NONE, can_focus=False,
                       tooltip_text=os.path.basename(ruta))
        b.get_style_context().add_class("miniatura")
        b.leida = False
        b.imagen = Gtk.Image(width_request=MINIATURA, height_request=MINIATURA)
        b.add(b.imagen)
        b.connect("clicked", lambda _b: self.al_elegir(ruta))
        return b

    def marcar(self, ruta):
        """Resalta la imagen actual, la centra y lee primero las miniaturas de su alrededor."""
        for r, si in ((self.actual, False), (ruta, True)):
            if r in self.botones:
                ctx = self.botones[r].get_style_context()
                (ctx.add_class if si else ctx.remove_class)("actual")
        self.actual = ruta
        if ruta in self.botones:
            posicion = {r: i for i, r in enumerate(self.rutas)}
            centro = posicion[ruta]
            self.cola.sort(key=lambda r: -abs(posicion.get(r, 0) - centro))
            self._centrar()
        self._seguir()

    def _centrar(self):
        b = self.botones.get(self.actual)
        a = b.get_allocation() if b else None
        self.centrar_pendiente = not a or a.width <= 1
        if not self.centrar_pendiente:
            ajuste = self.get_hadjustment()
            ajuste.set_value(a.x + a.width / 2 - ajuste.get_page_size() / 2)

    def refrescar(self, ruta):
        """El archivo ha cambiado: su miniatura se vuelve a leer."""
        if ruta in self.botones and ruta not in self.cola:
            self.botones[ruta].leida = False
            self.cola.append(ruta)
            self._seguir()

    def _seguir(self):
        if self.leyendo or not self.cola or not self.get_mapped():
            return
        ruta = self.cola.pop()
        self.leyendo = Gio.Cancellable()

        def lista(pixbuf, error):
            self.leyendo = None
            if cancelado(error):  # tira oculta: se leerá al volver a enseñarla
                self.cola.append(ruta)
                return
            b = self.botones.get(ruta)
            if b:
                b.leida = True
                if pixbuf:
                    b.imagen.set_from_pixbuf(pixbuf.apply_embedded_orientation())
                else:
                    b.imagen.set_from_icon_name("image-missing", Gtk.IconSize.DIALOG)
            GLib.idle_add(lambda: self._seguir() and False, priority=GLib.PRIORITY_LOW)

        leer(ruta, self.leyendo, lista, MINIATURA)

    def parar(self):
        if self.leyendo:
            self.leyendo.cancel()


# --- Ventana ------------------------------------------------------------------

class Visor(Gtk.Window):
    def __init__(self, argumentos):
        super().__init__(title="Visor")
        self.set_default_size(1100, 760)
        self.set_icon_name("image-viewer")
        self.tema = comun.tema_actual()
        self.rutas, self.indice, self.carpeta = [], 0, None
        self.cargando = None          # Gio.Cancellable de la imagen que se está leyendo
        self.animacion = self.vigilante = None  # vigilante: Gio.FileMonitor de la carpeta
        self.releer_pendiente = self.diapositivas = 0
        self.rueda, self.completa, self.con_tira = 0.0, False, True
        self._estilo()

        self._construir_barra()
        capas = self._construir_capas()

        self.tira = Tira(self.ir_a)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.pack_start(self.barra, False, False, 0)
        caja.pack_start(capas, True, True, 0)
        caja.pack_start(self.tira, False, False, 0)
        self.add(caja)

        self.teclas = self._teclas()
        self.connect("key-press-event", self._al_teclear)
        self.connect("window-state-event", self._al_cambiar_estado)
        self.connect("destroy", lambda *_: Gtk.main_quit())
        self.cargar(argumentos)

    def _construir_barra(self):
        """Crea la barra de botones de arriba con la etiqueta de estado a la derecha."""
        self.barra = Gtk.Box(spacing=2)
        self.barra.get_style_context().add_class("barra")
        for icono, ayuda, accion in (
                ("go-previous-symbolic", "Anterior (←)", lambda: self.mover(-1)),
                ("go-next-symbolic", "Siguiente (→)", lambda: self.mover(1)),
                (None, None, None),
                ("zoom-out-symbolic", "Alejar (-)", lambda: self.lienzo.ampliar(1 / PASO)),
                ("zoom-in-symbolic", "Acercar (+)", lambda: self.lienzo.ampliar(PASO)),
                ("zoom-original-symbolic", "Tamaño real (1)", lambda: self.lienzo.ampliar(valor=1)),
                ("zoom-fit-best-symbolic", "Ajustar a la ventana (0)", lambda: self.lienzo.ajustar()),
                (None, None, None),
                ("object-rotate-left-symbolic", "Girar a la izquierda ([)", lambda: self.lienzo.girar(-90)),
                ("object-rotate-right-symbolic", "Girar a la derecha (])", lambda: self.lienzo.girar(90)),
                ("object-flip-horizontal-symbolic", "Voltear (H)", lambda: self.lienzo.voltear()),
                (None, None, None),
                ("view-grid-symbolic", "Miniaturas (T)", self.alternar_tira),
                ("view-fullscreen-symbolic", "Pantalla completa (F11)", self.alternar_completa),
                ("media-playback-start-symbolic", "Pase de diapositivas (F5)", self.alternar_pase),
                ("dialog-information-symbolic", "Información (I)", self.alternar_info),
                ("system-run-symbolic", "Abrir con… (O)", self.abrir_con),
                ("folder-open-symbolic", "Abrir la carpeta", self.abrir_carpeta),
                ("user-trash-symbolic", "Mover a la papelera (Supr)", self.a_la_papelera)):
            self.barra.pack_start(boton(icono, ayuda, accion) if icono
                                  else Gtk.Separator(orientation=Gtk.Orientation.VERTICAL),
                                  False, False, 4 if not icono else 0)
        self.estado = Gtk.Label(xalign=1)
        self.estado.get_style_context().add_class("estado")
        self.barra.pack_end(self.estado, False, False, 8)

    def _construir_capas(self):
        """Crea el lienzo con las fichas de aviso e información superpuestas."""
        self.lienzo = Lienzo(self._estado)
        self.lienzo.connect("scroll-event", self._al_rodar)
        self.lienzo.connect("button-press-event", self._al_pulsar)
        self.texto_aviso = Gtk.Label(wrap=True, max_width_chars=60, justify=Gtk.Justification.CENTER)
        self.aviso = self._ficha(Gtk.Align.CENTER, self.texto_aviso)
        self.rejilla = Gtk.Grid(column_spacing=14, row_spacing=4)
        self.info = self._ficha(Gtk.Align.START, self.rejilla)
        fondo = Gtk.Box()  # Gtk.Layout no pinta fondo con CSS: lo pone esta caja
        fondo.get_style_context().add_class("lienzo")
        fondo.pack_start(self.lienzo, True, True, 0)
        capas = Gtk.Overlay()
        capas.add(fondo)
        capas.add_overlay(self.info)
        capas.add_overlay(self.aviso)
        for ficha in (self.info, self.aviso):
            capas.set_overlay_pass_through(ficha, True)
        return capas

    # Aspecto

    def _estilo(self):
        self.proveedor = Gtk.CssProvider()
        if YORHA:
            self.proveedor.load_from_path(os.path.join(CARPETA, "visor.css"))
            # tema.sh deja el tema en un archivo: si cambia, el visor cambia con él
            try:
                self.vigilante_tema = Gio.File.new_for_path(os.path.join(
                    comun.RUNTIME, "yorha-tema")).monitor_file(Gio.FileMonitorFlags.NONE, None)
                self.vigilante_tema.connect("changed", lambda *_: self.poner_tema(comun.tema_actual()))
            except GLib.Error:
                pass
        else:
            self.proveedor.load_from_data(CSS_TRABAJO.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), self.proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        vestir(self, self.tema)

    def poner_tema(self, tema):
        self.tema = tema
        vestir(self, tema)

    def _ficha(self, alineacion, hijo):
        """Recuadro sobre la imagen (información o aviso); empieza oculto."""
        ficha = Gtk.Box(halign=alineacion, valign=alineacion, margin_start=16, margin_top=16,
                        no_show_all=True)
        ficha.get_style_context().add_class("ficha")
        ficha.add(hijo)
        hijo.show()
        return ficha

    # Lista de imágenes

    def cargar(self, argumentos):
        """Abre lo de la línea de órdenes y vigila su carpeta."""
        self.rutas, indice, self.carpeta = lista_inicial(argumentos)
        # La que se abrió a mano aunque la carpeta no la liste (oculta o extensión rara)
        self.extra = None
        if self.carpeta and self.rutas and self.rutas[-1] not in imagenes_de(self.carpeta):
            self.extra = self.rutas[-1]
        if self.carpeta:
            self.vigilante = Gio.File.new_for_path(self.carpeta).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
            self.vigilante.connect("changed", self._carpeta_cambiada)
        self.tira.poner(self.rutas)
        self.ir_a(self.rutas[indice] if self.rutas else None)

    def _carpeta_cambiada(self, _vigilante, archivo, _otro, evento):
        ruta = archivo.get_path()
        if evento == Gio.FileMonitorEvent.CHANGES_DONE_HINT:
            self.tira.refrescar(ruta)
            if ruta == self.ruta():
                self.ir_a(ruta)  # se ha guardado encima: se vuelve a leer
        elif evento != Gio.FileMonitorEvent.CHANGED and not self.releer_pendiente:
            # Creado, borrado, movido o renombrado; espera a que acaben las ráfagas
            self.releer_pendiente = GLib.timeout_add(400, self._releer)

    def _releer(self):
        self.releer_pendiente = 0
        rutas = imagenes_de(self.carpeta)
        if self.extra and os.path.isfile(self.extra) and self.extra not in rutas:
            rutas.append(self.extra)  # sigue al final, como al abrirla
        actual = self.ruta()
        if rutas != self.rutas:
            self.cambiar_lista(actual, rutas)
        return False

    def cambiar_lista(self, actual, rutas):
        """Cambia la lista; si la imagen actual ya no está, enseña la que ocupa su sitio."""
        self.rutas = rutas
        self.tira.poner(rutas)
        if actual in rutas:
            self.indice = rutas.index(actual)
            self.tira.marcar(actual)
            self._estado()
        else:
            self.ir_a(rutas[min(self.indice, len(rutas) - 1)] if rutas else None)

    def ruta(self):
        return self.rutas[self.indice] if self.rutas else None

    # Navegación y carga

    def ir_a(self, ruta):
        """Enseña ruta (de self.rutas) o, con None, que no hay imágenes."""
        if self.cargando:
            self.cargando.cancel()
            self.cargando = None
        if ruta is None:
            self.animacion = None
            self.lienzo.mostrar(None)
            self.set_title("Visor")
            self.avisar("No hay imágenes aquí.")
            self._rellenar_info()
            return
        self.indice = self.rutas.index(ruta)
        self.set_title(f"{os.path.basename(ruta)} — Visor")
        self.tira.marcar(ruta)
        self.cargando = cancelable = Gio.Cancellable()

        def cargada(animacion, error):
            if cancelado(error) or cancelable is not self.cargando:
                return
            self.cargando = None
            self.animacion = animacion
            self.lienzo.mostrar(animacion)
            self.avisar(f"No se puede abrir «{os.path.basename(ruta)}»:\n{error.message}"
                        if error else None)
            self._rellenar_info()

        # Mientras se lee, la imagen anterior sigue a la vista: pasar rápido no parpadea
        leer(ruta, cancelable, cargada)

    def mover(self, paso, dar_vuelta=False):
        if not self.rutas:
            return
        i = self.indice + paso
        i = i % len(self.rutas) if dar_vuelta else min(max(i, 0), len(self.rutas) - 1)
        if i != self.indice:
            self.ir_a(self.rutas[i])

    def avisar(self, texto):
        self.texto_aviso.set_text(texto or "")
        self.aviso.set_visible(bool(texto))

    def _estado(self):
        partes = []
        if self.rutas:
            partes.append(f"{self.indice + 1} / {len(self.rutas)}")
        p = self.lienzo.orientada
        if p:
            partes.append(f"{p.get_width()} × {p.get_height()}")
            partes.append(f"{round(self.lienzo.zoom_actual() * 100)} %")
        if self.diapositivas:
            partes.append("▶ pase")
        self.estado.set_text("   ".join(partes))

    # Información

    def alternar_info(self):
        self.info.set_visible(not self.info.get_visible())
        self._rellenar_info()

    def _rellenar_info(self):
        if not self.info.get_visible():
            return
        for hijo in self.rejilla.get_children():
            hijo.destroy()
        ruta = self.ruta()
        filas = [("Nombre", os.path.basename(ruta) if ruta else "—")]
        if ruta:
            a = self.animacion
            if a:
                filas.append(("Tamaño", f"{a.get_width()} × {a.get_height()} px"
                              + ("" if a.is_static_image() else ", animada")))
            try:
                datos = Gio.File.new_for_path(ruta).query_info(
                    "standard::size,standard::content-type,time::modified",
                    Gio.FileQueryInfoFlags.NONE, None)
                fecha = datos.get_modification_date_time()
                filas += [("Archivo", GLib.format_size(datos.get_size())),
                          ("Fecha", fecha.to_local().format("%d/%m/%Y %H:%M") if fecha else "—"),
                          ("Tipo", Gio.content_type_get_description(datos.get_content_type() or ""))]
            except GLib.Error:
                pass
            filas.append(("Carpeta", os.path.dirname(ruta)))
        for fila, (clave, valor) in enumerate(filas):
            k = Gtk.Label(label=clave, xalign=1)
            k.get_style_context().add_class("clave")
            self.rejilla.attach(k, 0, fila, 1, 1)
            self.rejilla.attach(Gtk.Label(label=valor, xalign=0, max_width_chars=50,
                                          ellipsize=3), 1, fila, 1, 1)  # 3: Pango ...END
        self.rejilla.show_all()

    # Pantalla completa, tira y pase

    def alternar_completa(self):
        (self.unfullscreen if self.completa else self.fullscreen)()

    def _al_cambiar_estado(self, _w, ev):
        self.completa = bool(ev.new_window_state & Gdk.WindowState.FULLSCREEN)
        self.barra.set_visible(not self.completa)
        self.tira.set_visible(self.con_tira and not self.completa)
        ctx = self.get_style_context()
        (ctx.add_class if self.completa else ctx.remove_class)("completa")
        return False

    def alternar_tira(self):
        self.con_tira = not self.con_tira
        self.tira.set_visible(self.con_tira and not self.completa)
        if not self.con_tira:
            self.tira.parar()

    def alternar_pase(self):
        if self.diapositivas:
            GLib.source_remove(self.diapositivas)
            self.diapositivas = 0
        else:
            self.diapositivas = GLib.timeout_add(DIAPOSITIVA, lambda: self.mover(1, True) or True)
        self._estado()

    # Archivo: abrir con, carpeta, papelera

    def _dialogo(self, dialogo, si=None, dato=lambda _d: True):
        """Diálogo modal vestido como el visor: dato(dialogo) si se responde si; si no, None."""
        vestir(dialogo, self.tema)
        resultado = dato(dialogo) if dialogo.run() == si else None
        dialogo.destroy()
        return resultado

    def error(self, texto, detalle):
        self._dialogo(Gtk.MessageDialog(transient_for=self, modal=True, text=texto,
                                        message_type=Gtk.MessageType.ERROR, secondary_text=detalle,
                                        buttons=Gtk.ButtonsType.CLOSE))

    def contexto(self):
        return self.get_display().get_app_launch_context()

    def abrir_con(self):
        ruta = self.ruta()
        if not ruta:
            return
        archivo = Gio.File.new_for_path(ruta)
        dialogo = Gtk.AppChooserDialog.new(self, Gtk.DialogFlags.MODAL, archivo)
        dialogo.set_heading(f"Abrir «{os.path.basename(ruta)}» con:")
        app = self._dialogo(dialogo, Gtk.ResponseType.OK, Gtk.AppChooser.get_app_info)
        if app:
            try:
                app.launch([archivo], self.contexto())
            except GLib.Error as e:
                self.error(f"No se ha podido abrir {app.get_display_name()}.", e.message)

    def abrir_carpeta(self):
        carpeta = os.path.dirname(self.ruta()) if self.rutas else self.carpeta
        if carpeta:
            try:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(carpeta).get_uri(),
                                                   self.contexto())
            except GLib.Error as e:
                self.error("No se ha podido abrir la carpeta.", e.message)

    def a_la_papelera(self):
        ruta = self.ruta()
        if not ruta:
            return
        dialogo = Gtk.MessageDialog(transient_for=self, modal=True,
                                    message_type=Gtk.MessageType.QUESTION,
                                    text=f"¿Mover «{os.path.basename(ruta)}» a la papelera?",
                                    secondary_text="Se puede recuperar desde la papelera.")
        dialogo.add_buttons("Cancelar", Gtk.ResponseType.CANCEL,
                            "Mover a la papelera", Gtk.ResponseType.ACCEPT)
        dialogo.set_default_response(Gtk.ResponseType.ACCEPT)
        if not self._dialogo(dialogo, Gtk.ResponseType.ACCEPT):
            return
        try:
            Gio.File.new_for_path(ruta).trash(None)
        except GLib.Error as e:
            self.error(f"No se ha podido mover «{os.path.basename(ruta)}» a la papelera.", e.message)
            return
        self.cambiar_lista(None, [r for r in self.rutas if r != ruta])

    # Ratón y teclado

    def _al_rodar(self, _w, ev):
        d = Gdk.ScrollDirection
        _, dx, dy = (ev.get_scroll_deltas() if ev.direction == d.SMOOTH else
                     (0, *{d.UP: (0, -1), d.DOWN: (0, 1), d.LEFT: (-1, 0), d.RIGHT: (1, 0)}[ev.direction]))
        if ev.state & Gdk.ModifierType.CONTROL_MASK:
            if dy:
                self.lienzo.ampliar(PASO ** -dy, x=ev.x, y=ev.y)
            return True
        # El panel táctil manda pasos pequeños: se cambia de imagen al sumar uno entero
        self.rueda += dy or dx
        if abs(self.rueda) >= 1:
            self.mover(1 if self.rueda > 0 else -1)
            self.rueda = 0.0
        return True

    def _al_pulsar(self, _w, ev):
        if ev.type == Gdk.EventType.DOUBLE_BUTTON_PRESS and ev.button == Gdk.BUTTON_PRIMARY:
            self.alternar_completa()
        elif ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == Gdk.BUTTON_SECONDARY:
            self._menu().popup_at_pointer(ev)
        return False

    def _menu(self):
        menu = Gtk.Menu()
        for texto, accion in (("Abrir con…", self.abrir_con), ("Abrir la carpeta", self.abrir_carpeta),
                              ("Información", self.alternar_info),
                              ("Mover a la papelera", self.a_la_papelera)):
            item = Gtk.MenuItem(label=texto)
            item.connect("activate", lambda _i, a=accion: a())
            menu.append(item)
        menu.attach_to_widget(self)
        vestir(menu.get_toplevel(), self.tema)
        menu.show_all()
        return menu

    def _teclas(self):
        """{(tecla, con Ctrl): acción}."""
        lienzo, k = self.lienzo, Gdk
        sin = {
            (k.KEY_Left, k.KEY_Page_Up, k.KEY_BackSpace, k.KEY_KP_Left, k.KEY_KP_Page_Up):
                lambda: self.mover(-1),
            (k.KEY_Right, k.KEY_Page_Down, k.KEY_space, k.KEY_KP_Right, k.KEY_KP_Page_Down):
                lambda: self.mover(1),
            (k.KEY_Home, k.KEY_KP_Home): lambda: self.mover(-len(self.rutas)),
            (k.KEY_End, k.KEY_KP_End): lambda: self.mover(len(self.rutas)),
            (k.KEY_plus, k.KEY_KP_Add, k.KEY_equal): lambda: lienzo.ampliar(PASO),
            (k.KEY_minus, k.KEY_KP_Subtract): lambda: lienzo.ampliar(1 / PASO),
            (k.KEY_1, k.KEY_KP_1): lambda: lienzo.ampliar(valor=1),
            (k.KEY_0, k.KEY_KP_0): lienzo.ajustar,
            (k.KEY_bracketleft,): lambda: lienzo.girar(-90),
            (k.KEY_bracketright,): lambda: lienzo.girar(90),
            (k.KEY_h,): lienzo.voltear,
            (k.KEY_v,): lambda: lienzo.voltear(vertical=True),
            (k.KEY_t,): self.alternar_tira,
            (k.KEY_F11, k.KEY_f): self.alternar_completa,
            (k.KEY_F5,): self.alternar_pase,
            (k.KEY_Delete, k.KEY_KP_Delete): self.a_la_papelera,
            (k.KEY_i,): self.alternar_info,
            (k.KEY_o,): self.abrir_con,
            (k.KEY_Escape,): self.escape,
        }
        con = {
            (k.KEY_Left,): lambda: lienzo.girar(-90),
            (k.KEY_Right,): lambda: lienzo.girar(90),
            (k.KEY_0, k.KEY_KP_0): lambda: lienzo.ampliar(valor=1),
            (k.KEY_plus, k.KEY_KP_Add, k.KEY_equal): lambda: lienzo.ampliar(PASO),
            (k.KEY_minus, k.KEY_KP_Subtract): lambda: lienzo.ampliar(1 / PASO),
            (k.KEY_i,): self.alternar_info,
            (k.KEY_q, k.KEY_w): self.destroy,
        }
        return {(tecla, ctrl): accion for ctrl, tabla in ((False, sin), (True, con))
                for teclas, accion in tabla.items() for tecla in teclas}

    def _al_teclear(self, _w, ev):
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        accion = self.teclas.get((Gdk.keyval_to_lower(ev.keyval), ctrl))
        if accion:
            accion()
            return True
        return False

    def escape(self):
        if self.completa:
            self.unfullscreen()
        elif self.diapositivas:
            self.alternar_pase()
        else:
            self.destroy()


def main():
    GLib.set_prgname("visor")  # app_id de Wayland: el de visor.desktop
    GLib.set_application_name("Visor de imágenes")
    ventana = Visor(sys.argv[1:])
    ventana.show_all()
    Gtk.main()


if __name__ == "__main__":
    main()
