"""Panel lateral de ARCHBOX: Lugares, Marcadores y Dispositivos, como el de Thunar.

Lugares sale de las carpetas de usuario de XDG (GLib.get_user_special_dir), los marcadores de
~/.config/gtk-3.0/bookmarks (los mismos que usan Thunar y los diálogos de GTK) y los
dispositivos de Gio.VolumeMonitor, que monta, desmonta y expulsa sin programas externos.
Cada dispositivo montado y el sistema de archivos llevan una barra de espacio ocupado.
"""
import os
import threading

from gi.repository import Gdk, Gio, GLib, Gtk

import archivos
import dialogos
from vista import OBJETIVOS, ACCIONES_DND, accion_soltar, consultar_rapido

MARCADORES = os.path.join(GLib.get_user_config_dir(), "gtk-3.0", "bookmarks")
CARPETAS_XDG = (GLib.UserDirectory.DIRECTORY_DESKTOP, GLib.UserDirectory.DIRECTORY_DOCUMENTS,
                GLib.UserDirectory.DIRECTORY_DOWNLOAD, GLib.UserDirectory.DIRECTORY_MUSIC,
                GLib.UserDirectory.DIRECTORY_PICTURES, GLib.UserDirectory.DIRECTORY_VIDEOS)
ICONOS_XDG = ("user-desktop", "folder-documents", "folder-download", "folder-music",
              "folder-pictures", "folder-videos")
SECCIONES = {"lugares": "Lugares", "marcadores": "Marcadores", "dispositivos": "Dispositivos"}


def leer_marcadores():
    """[(uri, nombre o None)] del archivo de marcadores de GTK."""
    try:
        with open(MARCADORES, encoding="utf-8") as f:
            lineas = f.read().splitlines()
    except OSError:
        return []
    marcadores = []
    for linea in lineas:
        uri, _, nombre = linea.strip().partition(" ")
        if uri:
            marcadores.append((uri, nombre or None))
    return marcadores


def guardar_marcadores(marcadores):
    """Reescribe el archivo de marcadores de forma atómica (otro archivo y renombrar)."""
    os.makedirs(os.path.dirname(MARCADORES), exist_ok=True)
    temporal = f"{MARCADORES}.archbox-{os.getpid()}"
    with open(temporal, "w", encoding="utf-8") as f:
        for uri, nombre in marcadores:
            f.write(f"{uri} {nombre}\n" if nombre else f"{uri}\n")
    os.replace(temporal, MARCADORES)


def anadir_marcador(archivo):
    marcadores = leer_marcadores()
    if archivo.get_uri() not in [u for u, _ in marcadores]:
        guardar_marcadores(marcadores + [(archivo.get_uri(), None)])


class Fila(Gtk.ListBoxRow):
    """Una entrada del panel; destino es un Gio.File o "papelera"."""

    def __init__(self, seccion, nombre, gicon, destino=None, volumen=None, montaje=None, marcador=None):
        super().__init__()
        self.seccion, self.nombre, self.destino = seccion, nombre, destino
        self.volumen, self.montaje, self.marcador = volumen, montaje, marcador
        caja = Gtk.Box(spacing=8)
        caja.get_style_context().add_class("lugar")
        self.icono = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.MENU)
        self.icono.set_pixel_size(16)
        caja.pack_start(self.icono, False, False, 0)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        textos.set_valign(Gtk.Align.CENTER)
        etiqueta = Gtk.Label(label=nombre, xalign=0, ellipsize=3)
        textos.pack_start(etiqueta, False, False, 0)
        self.barra = Gtk.LevelBar(min_value=0, max_value=1)
        self.barra.set_no_show_all(True)
        self.barra.get_style_context().add_class("espacio")
        # Marrón hasta el 90 % ocupado; por encima, «full» (en rojo en YoRHa)
        self.barra.remove_offset_value(Gtk.LEVEL_BAR_OFFSET_LOW)
        self.barra.add_offset_value(Gtk.LEVEL_BAR_OFFSET_HIGH, 0.9)
        textos.pack_start(self.barra, False, False, 0)
        caja.pack_start(textos, True, True, 0)
        expulsar = montaje and (montaje.can_eject() or montaje.can_unmount())
        if expulsar:
            boton = Gtk.Button.new_from_icon_name("media-eject-symbolic", Gtk.IconSize.MENU)
            boton.set_relief(Gtk.ReliefStyle.NONE)
            boton.set_tooltip_text("Expulsar" if montaje.can_eject() else "Desmontar")
            boton.get_style_context().add_class("expulsar")
            boton.set_valign(Gtk.Align.CENTER)
            self.boton_expulsar = boton
            caja.pack_end(boton, False, False, 0)
        self.add(caja)
        self.set_tooltip_text(archivos.ruta_bonita(destino) if isinstance(destino, Gio.File) else nombre)
        raiz = destino if isinstance(destino, Gio.File) and (montaje or seccion == "raiz") else None
        if raiz:
            raiz.query_filesystem_info_async("filesystem::size,filesystem::free", GLib.PRIORITY_LOW,
                                             None, self._espacio)

    def _espacio(self, raiz, resultado):
        try:
            info = raiz.query_filesystem_info_finish(resultado)
        except GLib.Error:
            return
        total = info.get_attribute_uint64("filesystem::size")
        libre = info.get_attribute_uint64("filesystem::free")
        if total:
            self.barra.set_value((total - libre) / total)
            self.barra.show()
            self.set_tooltip_text(f"{self.get_tooltip_text()}\n{GLib.format_size(libre)} libres de "
                                  f"{GLib.format_size(total)}")


class Lugares(Gtk.ScrolledWindow):
    """El panel lateral. ventana: la ventana de ARCHBOX (navegar, soltar, propiedades...)."""

    def __init__(self, ventana):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.ventana = ventana
        self.get_style_context().add_class("lugares")
        self.lista = Gtk.ListBox()
        self.lista.get_style_context().add_class("navigation-sidebar")
        self.lista.set_header_func(self._cabecera)
        self.lista.connect("row-activated", lambda _l, fila: self.abrir(fila))
        self.lista.connect("button-press-event", self._pulsar)
        self.add(self.lista)
        self.monitor_volumenes = Gio.VolumeMonitor.get()
        for senal in ("mount-added", "mount-removed", "mount-changed", "volume-added",
                      "volume-removed", "volume-changed", "drive-connected", "drive-disconnected"):
            self.monitor_volumenes.connect(senal, lambda *_: self.pedir_relleno())
        self.monitores = []
        for ruta in [MARCADORES] + archivos.carpetas_archivos_papelera():
            f = Gio.File.new_for_path(ruta)
            try:
                vigilar = f.monitor_directory if ruta != MARCADORES else f.monitor_file
                m = vigilar(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            m.connect("changed", lambda *_: self.pedir_relleno())
            self.monitores.append(m)
        self.connect("destroy", lambda *_: [m.cancel() for m in self.monitores])
        self.lista.drag_dest_set(0, OBJETIVOS, ACCIONES_DND)
        self.lista.connect("drag-motion", self._sobre)
        self.lista.connect("drag-leave", lambda *_: self.lista.drag_unhighlight_row())
        self.lista.connect("drag-drop", self._dejar)
        self.lista.connect("drag-data-received", self._recibido)
        self.relleno = 0
        self.actual = None
        self.rellenar()

    # Contenido

    def pedir_relleno(self):
        if not self.relleno:
            self.relleno = GLib.timeout_add(300, self.rellenar)

    def rellenar(self):
        self.relleno = 0
        for fila in self.lista.get_children():
            fila.destroy()
        casa = GLib.get_home_dir()
        self._poner(Fila("lugares", "Carpeta personal", Gio.ThemedIcon.new("user-home"),
                         Gio.File.new_for_path(casa)))
        for clave, icono in zip(CARPETAS_XDG, ICONOS_XDG, strict=True):
            ruta = GLib.get_user_special_dir(clave)
            if ruta and os.path.realpath(ruta) != os.path.realpath(casa) and os.path.isdir(ruta):
                self._poner(Fila("lugares", os.path.basename(ruta.rstrip("/")),
                                 Gio.ThemedIcon.new_with_default_fallbacks(icono),
                                 Gio.File.new_for_path(ruta)))
        papelera = Fila("lugares", "Papelera", Gio.ThemedIcon.new("user-trash"), "papelera")
        self._poner(papelera)
        # Mirar si está vacía toca los discos montados (uno de red colgado congelaría la ventana)
        def mirar():
            vacia = archivos.papelera_vacia()
            GLib.idle_add(lambda: papelera.icono.set_from_icon_name(
                "user-trash" if vacia else "user-trash-full", Gtk.IconSize.MENU) and False)
        threading.Thread(target=mirar, daemon=True).start()
        self._poner(Fila("raiz", "Sistema de archivos", Gio.ThemedIcon.new("drive-harddisk"),
                         Gio.File.new_for_path("/")))
        for i, (uri, nombre) in enumerate(leer_marcadores()):
            f = Gio.File.new_for_uri(uri)
            icono = "folder" if f.is_native() else "folder-remote"
            self._poner(Fila("marcadores", nombre or f.get_basename() or uri,
                             Gio.ThemedIcon.new(icono), f, marcador=i))
        self._dispositivos()
        self.lista.show_all()
        self.marcar(self.actual)
        return False

    def _dispositivos(self):
        montajes_vistos = set()
        for volumen in self.monitor_volumenes.get_volumes():
            montaje = volumen.get_mount()
            if montaje:
                montajes_vistos.add(montaje.get_root().get_uri())
            destino = montaje.get_root() if montaje else None
            self._poner(Fila("dispositivos", volumen.get_name(), volumen.get_icon(), destino,
                             volumen=volumen, montaje=montaje))
        for montaje in self.monitor_volumenes.get_mounts():
            if montaje.is_shadowed() or montaje.get_root().get_uri() in montajes_vistos:
                continue
            self._poner(Fila("dispositivos", montaje.get_name(), montaje.get_icon(), montaje.get_root(),
                             montaje=montaje))

    def _poner(self, fila):
        self.lista.add(fila)
        if getattr(fila, "boton_expulsar", None):
            fila.boton_expulsar.connect("clicked", lambda _b: self.expulsar(fila))

    def _cabecera(self, fila, anterior):
        seccion = "lugares" if fila.seccion == "raiz" else fila.seccion
        previa = None if anterior is None else ("lugares" if anterior.seccion == "raiz" else anterior.seccion)
        if seccion != previa:
            titulo = Gtk.Label(label=SECCIONES[seccion], xalign=0)
            titulo.get_style_context().add_class("seccion")
            fila.set_header(titulo)
        else:
            fila.set_header(None)

    def marcar(self, destino):
        """Resalta el lugar que se está viendo (si está en el panel)."""
        self.actual = destino
        for fila in self.lista.get_children():
            igual = (fila.destino == destino == "papelera") or (
                isinstance(fila.destino, Gio.File) and isinstance(destino, Gio.File)
                and fila.destino.equal(destino))
            if igual:
                self.lista.select_row(fila)
                return
        self.lista.unselect_all()

    # Acciones

    def abrir(self, fila, donde="aqui"):
        if fila.destino is not None:
            self.ventana.ir_a(fila.destino, donde)
        elif fila.volumen:
            self.montar(fila, donde)

    def montar(self, fila, donde="aqui"):
        operacion = Gtk.MountOperation.new(self.ventana)

        def montado(volumen, resultado):
            try:
                volumen.mount_finish(resultado)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    dialogos.mensaje(self.ventana, f"No se pudo montar «{fila.nombre}»", e.message)
                return
            montaje = volumen.get_mount()
            if montaje:
                self.ventana.ir_a(montaje.get_root(), donde)

        fila.volumen.mount(Gio.MountMountFlags.NONE, operacion, None, montado)

    def expulsar(self, fila, solo_desmontar=False):
        operacion = Gtk.MountOperation.new(self.ventana)
        montaje = fila.montaje
        expulsar = montaje.can_eject() and not solo_desmontar

        def hecho(m, resultado):
            try:
                (m.eject_with_operation_finish if expulsar else m.unmount_with_operation_finish)(resultado)
            except GLib.Error as e:
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                    dialogos.mensaje(self.ventana, f"No se pudo {'expulsar' if expulsar else 'desmontar'} "
                                     f"«{fila.nombre}»", e.message)

        # Antes de desmontar, las pestañas que lo enseñan se van a la carpeta personal
        self.ventana.app.soltar_montaje(montaje.get_root())
        if expulsar:
            montaje.eject_with_operation(Gio.MountUnmountFlags.NONE, operacion, None, hecho)
        else:
            montaje.unmount_with_operation(Gio.MountUnmountFlags.NONE, operacion, None, hecho)

    def renombrar_marcador(self, fila):
        def poner(nombre):
            marcadores = leer_marcadores()
            uri, _ = marcadores[fila.marcador]
            marcadores[fila.marcador] = (uri, nombre)
            guardar_marcadores(marcadores)
            self.rellenar()
        dialogos.pedir_nombre(self.ventana, "Renombrar marcador", "Renombrar", fila.nombre, poner,
                              carpeta_inicial=True)

    def quitar_marcador(self, fila):
        marcadores = leer_marcadores()
        del marcadores[fila.marcador]
        guardar_marcadores(marcadores)
        self.rellenar()

    def mover_marcador(self, fila, paso):
        marcadores = leer_marcadores()
        i, j = fila.marcador, fila.marcador + paso
        if 0 <= j < len(marcadores):
            marcadores[i], marcadores[j] = marcadores[j], marcadores[i]
            guardar_marcadores(marcadores)
            self.rellenar()

    # Menú contextual

    def _pulsar(self, _l, evento):
        fila = self.lista.get_row_at_y(int(evento.y))
        if fila is None or evento.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if evento.button == Gdk.BUTTON_MIDDLE:
            self.abrir(fila, "pestana")
            return True
        if evento.button != Gdk.BUTTON_SECONDARY:
            return False
        self._abrir_menu(fila, evento)
        return True

    def _abrir_menu(self, fila, evento):
        """Menú contextual de un lugar: abrir, montar, papelera, marcador, desmontar y propiedades."""
        menu = Gtk.Menu()
        menu.get_style_context().add_class("archbox")

        def item(texto, funcion, activo=True):
            m = Gtk.MenuItem(label=texto)
            m.set_sensitive(activo)
            m.connect("activate", lambda _m: funcion())
            menu.append(m)

        if fila.destino is not None:
            item("Abrir", lambda: self.abrir(fila))
            item("Abrir en una pestaña nueva", lambda: self.abrir(fila, "pestana"))
            item("Abrir en una ventana nueva", lambda: self.abrir(fila, "ventana"))
        elif fila.volumen:
            item("Montar", lambda: self.montar(fila))
        if fila.destino == "papelera":
            menu.append(Gtk.SeparatorMenuItem())
            item("Vaciar la papelera", self.ventana.vaciar_papelera, not archivos.papelera_vacia())
        if fila.marcador is not None:
            menu.append(Gtk.SeparatorMenuItem())
            item("Renombrar marcador…", lambda: self.renombrar_marcador(fila))
            item("Subir", lambda: self.mover_marcador(fila, -1), fila.marcador > 0)
            item("Bajar", lambda: self.mover_marcador(fila, 1))
            item("Quitar marcador", lambda: self.quitar_marcador(fila))
        if fila.montaje:
            menu.append(Gtk.SeparatorMenuItem())
            if fila.montaje.can_unmount():
                item("Desmontar", lambda: self.expulsar(fila, solo_desmontar=True))
            if fila.montaje.can_eject():
                item("Expulsar", lambda: self.expulsar(fila))
        if isinstance(fila.destino, Gio.File):
            menu.append(Gtk.SeparatorMenuItem())
            item("Propiedades", lambda: self.ventana.propiedades_de([fila.destino]))
        menu.show_all()
        menu.attach_to_widget(self.lista, None)
        menu.popup_at_pointer(evento)

    # Soltar archivos en un lugar

    def _fila_destino(self, y):
        fila = self.lista.get_row_at_y(y)
        if fila is None or fila.destino is None:
            return None
        if isinstance(fila.destino, Gio.File) and not consultar_rapido(fila.destino, "access::can-write"):
            return None
        return fila

    def _sobre(self, _l, ctx, _x, y, t):
        fila = self._fila_destino(y)
        if fila is None:
            self.lista.drag_unhighlight_row()
            Gdk.drag_status(ctx, 0, t)
            return True
        self.lista.drag_highlight_row(fila)
        accion = Gdk.DragAction.MOVE if fila.destino == "papelera" else accion_soltar(ctx, fila.destino)
        Gdk.drag_status(ctx, accion, t)
        return True

    def _dejar(self, _l, ctx, _x, y, t):
        self.fila_soltar = self._fila_destino(y)
        objetivo = self.lista.drag_dest_find_target(ctx, None)
        if self.fila_soltar is None or objetivo is None:
            return False
        self.lista.drag_get_data(ctx, objetivo, t)
        return True

    def _recibido(self, _l, ctx, _x, _y, datos, _info, t):
        uris = datos.get_uris() or []
        fila = getattr(self, "fila_soltar", None)
        Gtk.drag_finish(ctx, bool(uris and fila), False, t)
        self.lista.drag_unhighlight_row()
        if not (uris and fila):
            return
        if fila.destino == "papelera":
            self.ventana.app.ops.papelera(uris)
        else:
            self.ventana.soltar(uris, fila.destino, accion_soltar(ctx, fila.destino))
