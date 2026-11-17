"""Contenido de una carpeta (o de la papelera, o de una búsqueda) y sus tres vistas, con miniaturas.

El contenido se lee con Gio de forma asíncrona, a tandas, y se mantiene al día con un
Gio.FileMonitor; el orden lo pone Python sobre el ListStore (más rápido que una función de
orden de GTK con miles de filas). Las miniaturas siguen la especificación de freedesktop.org
(~/.cache/thumbnails/normal|large, MD5 del URI, PNG con Thumb::URI y Thumb::MTime): se
aprovechan las que ya hizo cualquier programa y las que faltan se hacen con GdkPixbuf, que
decodifica en un hilo de Gio; nunca se bloquea la interfaz.
"""
import contextlib
import hashlib
import os
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk

import archivos

(C_NOMBRE, C_ICONO, C_TAMANO_TXT, C_TIPO, C_FECHA_TXT, C_UBICACION, C_ES_DIR, C_TAMANO,
 C_FECHA, C_CLAVE, C_ARCHIVO, C_INFO, C_URI) = range(13)
TIPOS = (str, GdkPixbuf.Pixbuf, str, str, str, str, bool, GObject.TYPE_INT64,
         GObject.TYPE_INT64, str, GObject.TYPE_OBJECT, GObject.TYPE_OBJECT, str)

VISTAS = ("iconos", "compacta", "detallada")
TAMANOS = {"iconos": (32, 48, 64, 96, 128, 192, 256),
           "compacta": (16, 24, 32, 48),
           "detallada": (16, 24, 32, 48)}
ZOOM_INICIAL = {"iconos": 2, "compacta": 0, "detallada": 0}
ORDENES = {"nombre": "Nombre", "tamano": "Tamaño", "tipo": "Tipo", "fecha": "Fecha de modificación"}
TANDA = 250                 # archivos por cada lectura asíncrona
MAX_RESULTADOS = 5000       # de una búsqueda en subcarpetas
OBJETIVOS = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
ACCIONES_DND = (Gdk.DragAction.COPY | Gdk.DragAction.MOVE | Gdk.DragAction.LINK
                | Gdk.DragAction.ASK)
ARRASTRANDO = []            # URIs que se arrastran desde una vista de ARCHBOX


# --- Iconos y miniaturas -------------------------------------------------------

class Iconos:
    """Pixbuf de cada icono del tema por tamaño (con caché; se vacía si cambia el tema)."""

    def __init__(self):
        self.cache = {}
        Gtk.IconTheme.get_default().connect("changed", lambda *_: self.cache.clear())

    def de(self, gicon, tam):
        clave = (gicon.to_string() if gicon else "", tam)
        if clave not in self.cache:
            tema = Gtk.IconTheme.get_default()
            info = tema.lookup_by_gicon(gicon, tam, Gtk.IconLookupFlags.FORCE_SIZE) if gicon else None
            info = info or tema.lookup_icon("text-x-generic", tam, Gtk.IconLookupFlags.FORCE_SIZE)
            try:
                self.cache[clave] = info.load_icon() if info else None
            except GLib.Error:
                self.cache[clave] = None
        return self.cache[clave]


def escalar(pixbuf, tam):
    """El pixbuf reducido para caber en tam × tam (nunca se agranda)."""
    ancho, alto = pixbuf.get_width(), pixbuf.get_height()
    factor = min(tam / ancho, tam / alto, 1)
    if factor == 1:
        return pixbuf
    return pixbuf.scale_simple(max(1, round(ancho * factor)), max(1, round(alto * factor)),
                               GdkPixbuf.InterpType.BILINEAR)


class Miniaturas:
    """Cola de miniaturas: dos a la vez, siempre con llamadas asíncronas."""

    CACHE = os.path.join(GLib.get_user_cache_dir(), "thumbnails")
    MAX_BYTES = 50 * 1024 * 1024
    MAX_PIXELES = 100_000_000   # imágenes más grandes no se abren (bombas de descompresión)
    PROHIBIDAS = ("/proc/", "/sys/", "/dev/", "/run/")
    FALLO = "fail/archbox-1"

    def __init__(self):
        self.tipos = {m for f in GdkPixbuf.Pixbuf.get_formats() if not f.is_disabled()
                      for m in f.get_mime_types()}
        self.cola = []
        self.activas = 0
        self.programada = False  # _siguiente pendiente en el bucle de GTK

    def pedir(self, gfile, info, grande, al_terminar, cancelable):
        """Pide la miniatura; al_terminar(pixbuf) llega desde el bucle de GTK si la hay."""
        ruta = gfile.get_path()
        if (not ruta or info.get_file_type() != Gio.FileType.REGULAR
                or (ruta + "/").startswith(self.PROHIBIDAS) or ruta.startswith(self.CACHE + "/")):
            return
        self.cola.append((gfile, info, grande, al_terminar, cancelable))
        self._siguiente()

    def _siguiente(self):
        while self.activas < 2 and self.cola:
            trabajo = self.cola.pop(0)
            if not trabajo[4].is_cancelled():
                self.activas += 1
                self._buscar(*trabajo)

    def _hecho(self):
        """Libera el hueco; la siguiente se busca desde el bucle de GTK y no desde aquí, para que
        una cola larga de archivos sin miniatura no se vacíe por recursión."""
        self.activas -= 1
        if not self.programada:
            self.programada = True
            GLib.idle_add(self._reanudar)
        return False

    def _reanudar(self):
        self.programada = False
        self._siguiente()
        return False

    def _buscar(self, gfile, info, grande, al_terminar, cancelable):
        uri = gfile.get_uri()
        mtime = str(archivos.segundos(info))
        nombre = hashlib.md5(uri.encode()).hexdigest() + ".png"
        guardada = os.path.join(self.CACHE, "large" if grande else "normal", nombre)
        fallo = os.path.join(self.CACHE, self.FALLO, nombre)

        def leida(pixbuf):
            if cancelable.is_cancelled():  # cancelada no es fallida: no se marca
                return self._hecho()
            if pixbuf and pixbuf.get_option("tEXt::Thumb::MTime") == mtime:
                al_terminar(pixbuf)
                return self._hecho()
            if not self._se_puede_hacer(info) or self._ya_fallo(fallo, mtime):
                return self._hecho()
            self._hacer(gfile, uri, mtime, 256 if grande else 128, guardada, fallo,
                        al_terminar, cancelable)
            return False

        if os.path.exists(guardada):
            leer_pixbuf(Gio.File.new_for_path(guardada), cancelable, leida)
        else:
            leida(None)

    def _se_puede_hacer(self, info):
        return (info.get_content_type() in self.tipos and info.get_size() <= self.MAX_BYTES
                and info.get_size() > 0)

    @staticmethod
    def _ya_fallo(fallo, mtime):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(fallo)
        except GLib.Error:
            return False
        return pixbuf.get_option("tEXt::Thumb::MTime") == mtime

    def _hacer(self, gfile, uri, mtime, lado, guardada, fallo, al_terminar, cancelable):
        ruta = gfile.get_path()

        def medida(_o, resultado):
            try:
                formato, ancho, alto = GdkPixbuf.Pixbuf.get_file_info_finish(resultado)
            except GLib.Error:
                formato = None
            if cancelable.is_cancelled():  # se salió de la carpeta: no es un fallo de la imagen
                return self._hecho()
            if not formato or ancho * alto > self.MAX_PIXELES:
                self._guardar(GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1),
                              uri, mtime, fallo)
                return self._hecho()
            escala = (lado, lado) if max(ancho, alto) > lado else None
            leer_pixbuf(gfile, cancelable, hecha, escala)

        def hecha(pixbuf):
            if pixbuf:
                pixbuf = pixbuf.apply_embedded_orientation() or pixbuf
                self._guardar(pixbuf, uri, mtime, guardada)
                al_terminar(pixbuf)
            elif not cancelable.is_cancelled():
                self._guardar(GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1),
                              uri, mtime, fallo)
            return self._hecho()

        GdkPixbuf.Pixbuf.get_file_info_async(ruta, cancelable, medida)

    @staticmethod
    def _guardar(pixbuf, uri, mtime, destino):
        """PNG con los datos de la especificación, escrito aparte y renombrado (atómico)."""
        carpeta = os.path.dirname(destino)
        temporal = f"{destino}.archbox-{os.getpid()}"
        try:
            os.makedirs(carpeta, mode=0o700, exist_ok=True)
            pixbuf.savev(temporal, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Software"],
                         [uri, mtime, "ARCHBOX"])
            os.chmod(temporal, 0o600)
            os.replace(temporal, destino)
        except (GLib.Error, OSError):
            with contextlib.suppress(OSError):
                os.remove(temporal)


def leer_pixbuf(gfile, cancelable, al_terminar, escala=None):
    """Lee una imagen sin bloquear (GdkPixbuf decodifica en un hilo); al_terminar(pixbuf o None)."""
    def abierto(_f, resultado):
        try:
            flujo = gfile.read_finish(resultado)
        except GLib.Error:
            al_terminar(None)
            return
        if escala:
            GdkPixbuf.Pixbuf.new_from_stream_at_scale_async(flujo, escala[0], escala[1], True,
                                                            cancelable, leido, flujo)
        else:
            GdkPixbuf.Pixbuf.new_from_stream_async(flujo, cancelable, leido, flujo)

    def leido(_o, resultado, flujo):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_finish(resultado)
        except GLib.Error:
            pixbuf = None
        flujo.close_async(GLib.PRIORITY_DEFAULT, None, None)
        al_terminar(pixbuf)

    gfile.read_async(GLib.PRIORITY_LOW, cancelable, abierto)


# --- Contenido -----------------------------------------------------------------

class Contenido(GObject.Object):
    """Las filas de lo que enseña una pestaña: una carpeta, la papelera o una búsqueda."""

    __gsignals__ = {
        "cargado": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "error": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "perdido": (GObject.SignalFlags.RUN_FIRST, None, ()),   # se borró la carpeta abierta
        "cambiado": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "nueva": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self, iconos, miniaturas):
        super().__init__()
        self.iconos, self.miniaturas = iconos, miniaturas
        self.store = Gtk.ListStore(*TIPOS)
        self.filas = {}             # URI → fila del store (solo las que se ven)
        self.todas = {}             # URI → (archivo, info, ubicación, fecha), también las ocultas
        self.ocultos, self.texto = False, ""
        self.miniaturas_hechas = {}
        self.tam = 16
        self.orden = ("nombre", False)
        self.modo = "carpeta"
        self.ubicacion = None
        self.cargando = False
        self.cancelable = Gio.Cancellable()
        self.monitores = []
        self.reordenar = self.recarga = 0

    # Carga

    def _empezar(self, modo, ubicacion):
        self.cancelable.cancel()
        self.cancelable = Gio.Cancellable()
        for m in self.monitores:
            m.cancel()
        self.monitores = []
        self.store.clear()
        self.filas.clear()
        self.todas.clear()
        self.miniaturas_hechas.clear()
        self.modo, self.ubicacion, self.cargando = modo, ubicacion, True

    def _terminar(self):
        self.cargando = False
        self.ordenar()
        self.emit("cargado")

    def cargar(self, carpeta):
        self._empezar("carpeta", carpeta)
        cancelable = self.cancelable
        carpeta.enumerate_children_async(archivos.ATRIBUTOS, Gio.FileQueryInfoFlags.NONE,
                                         GLib.PRIORITY_DEFAULT, cancelable,
                                         self._enumerado, (carpeta, cancelable, None))

    def _enumerado(self, carpeta, resultado, datos):
        _raiz, cancelable, _ = datos
        try:
            enumerador = carpeta.enumerate_children_finish(resultado)
        except GLib.Error as e:
            if not cancelable.is_cancelled():
                self.cargando = False
                self.emit("error", e.message)
            return
        self._vigilar(carpeta, cancelable)
        enumerador.next_files_async(TANDA, GLib.PRIORITY_DEFAULT, cancelable, self._tanda,
                                    (carpeta, cancelable))

    def _tanda(self, enumerador, resultado, datos):
        carpeta, cancelable = datos
        try:
            infos = enumerador.next_files_finish(resultado)
        except GLib.Error:
            infos = []
        if cancelable.is_cancelled():
            return
        for info in infos:
            self.poner(carpeta.get_child(info.get_name()), info, avisar=False)
        if infos:
            enumerador.next_files_async(TANDA, GLib.PRIORITY_DEFAULT, cancelable, self._tanda, datos)
        else:
            enumerador.close_async(GLib.PRIORITY_DEFAULT, None, None)
            self._terminar()

    def _vigilar(self, carpeta, cancelable):
        try:
            monitor = carpeta.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, cancelable)
        except GLib.Error:
            return
        monitor.set_rate_limit(500)
        monitor.connect("changed", self._cambio)
        self.monitores.append(monitor)

    def _cambio(self, _monitor, archivo, otro, evento):
        E = Gio.FileMonitorEvent
        if self.modo == "papelera":
            self._pedir_recarga_papelera()
            return
        if archivo.equal(self.ubicacion):
            if evento in (E.DELETED, E.MOVED_OUT, E.RENAMED):
                self.emit("perdido")
            return
        if evento in (E.DELETED, E.MOVED_OUT, E.RENAMED):
            self.quitar(archivo.get_uri())
        if evento == E.RENAMED and otro:
            self.consultar(otro)
        elif evento in (E.CREATED, E.MOVED_IN, E.CHANGED, E.CHANGES_DONE_HINT, E.ATTRIBUTE_CHANGED):
            self.consultar(archivo)

    def consultar(self, archivo):
        """Lee de nuevo un archivo y pone o actualiza su fila."""
        cancelable = self.cancelable

        def leido(_f, resultado):
            try:
                info = archivo.query_info_finish(resultado)
            except GLib.Error:
                return
            if not cancelable.is_cancelled() and archivo.get_parent() \
                    and archivo.get_parent().equal(self.ubicacion):
                self.poner(archivo, info)

        archivo.query_info_async(archivos.ATRIBUTOS, Gio.FileQueryInfoFlags.NONE,
                                 GLib.PRIORITY_DEFAULT, cancelable, leido)

    # Papelera

    def cargar_papelera(self):
        self._empezar("papelera", None)
        cancelable = self.cancelable

        def leer():
            filas = []
            for e in archivos.elementos_papelera():
                archivo = Gio.File.new_for_path(e.archivo)
                try:
                    info = archivo.query_info(archivos.ATRIBUTOS,
                                              Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
                except GLib.Error:
                    continue
                filas.append((archivo, info, e))
            GLib.idle_add(llenar, filas)

        def llenar(filas):
            if cancelable.is_cancelled():
                return False
            for archivo, info, e in filas:
                origen = os.path.dirname(e.original) if e.original else ""
                self.poner(archivo, info, archivos.ruta_bonita(Gio.File.new_for_path(origen))
                           if origen else "", e.fecha, avisar=False)
            for carpeta in archivos.carpetas_archivos_papelera():
                self._vigilar(Gio.File.new_for_path(carpeta), cancelable)
            self._terminar()
            return False

        threading.Thread(target=leer, daemon=True).start()

    def _pedir_recarga_papelera(self):
        if not self.recarga:
            self.recarga = GLib.timeout_add(400, self._recargar_papelera)

    def _recargar_papelera(self):
        self.recarga = 0
        if self.modo == "papelera":
            self.cargar_papelera()
        return False

    # Búsqueda en subcarpetas

    def buscar(self, raiz, texto, ocultos):
        """Busca texto en los nombres de raiz y de sus subcarpetas, a lo ancho y sin bloquear."""
        self._empezar("busqueda", raiz)
        self.buscado, self.pendientes = archivos.plegar(texto), [raiz]
        self._buscar_siguiente(self.cancelable)

    def _buscar_siguiente(self, cancelable):
        if cancelable.is_cancelled():
            return
        if not self.pendientes or len(self.filas) >= MAX_RESULTADOS:
            self._terminar()
            return
        carpeta = self.pendientes.pop(0)
        carpeta.enumerate_children_async(
            "standard::name,standard::display-name,standard::type,standard::is-hidden,"
            "standard::is-backup", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, GLib.PRIORITY_LOW,
            cancelable, self._buscar_enumerado, cancelable)

    def _buscar_enumerado(self, carpeta, resultado, cancelable):
        try:
            enumerador = carpeta.enumerate_children_finish(resultado)
        except GLib.Error:
            self._buscar_siguiente(cancelable)
            return
        enumerador.next_files_async(TANDA, GLib.PRIORITY_LOW, cancelable, self._buscar_tanda,
                                    (carpeta, cancelable))

    def _buscar_tanda(self, enumerador, resultado, datos):
        carpeta, cancelable = datos
        try:
            infos = enumerador.next_files_finish(resultado)
        except GLib.Error:
            infos = []
        if cancelable.is_cancelled():
            return
        for info in infos:
            if not self.ocultos and archivos.es_oculto(info):
                continue
            hijo = carpeta.get_child(info.get_name())
            if info.get_file_type() == Gio.FileType.DIRECTORY:
                self.pendientes.append(hijo)
            if self.buscado in archivos.plegar(info.get_display_name() or ""):
                self._resultado(hijo, carpeta, cancelable)
        if infos:
            enumerador.next_files_async(TANDA, GLib.PRIORITY_LOW, cancelable, self._buscar_tanda, datos)
        else:
            enumerador.close_async(GLib.PRIORITY_DEFAULT, None, None)
            self._buscar_siguiente(cancelable)

    def _resultado(self, hijo, carpeta, cancelable):
        relativa = self.ubicacion.get_relative_path(carpeta) or "."

        def leido(_f, resultado):
            try:
                info = hijo.query_info_finish(resultado)
            except GLib.Error:
                return
            if not cancelable.is_cancelled():
                self.poner(hijo, info, relativa)

        hijo.query_info_async(archivos.ATRIBUTOS, Gio.FileQueryInfoFlags.NONE,
                              GLib.PRIORITY_LOW, cancelable, leido)

    def cancelar(self):
        self.cancelable.cancel()
        for m in self.monitores:
            m.cancel()
        self.monitores = []
        if self.cargando:
            self.cargando = False
            self.emit("cargado")

    # Filas

    # Filtro: los ocultos y el texto de la búsqueda en la carpeta. Las filas que no se ven no
    # están en el store (sin Gtk.TreeModelFilter, que con el IconView daba avisos al borrar)

    def filtrar(self, ocultos, texto):
        """Cambia lo que se ve y pone o quita las filas que hagan falta."""
        self.ocultos, self.texto = ocultos, archivos.plegar(texto)
        for uri, datos in list(self.todas.items()):
            visible = self._visible(*datos[:2])
            if visible and uri not in self.filas:
                self.poner(*datos, avisar=False)
            elif not visible and uri in self.filas:
                self.store.remove(self.filas.pop(uri))
        self.ordenar()

    def _visible(self, _archivo, info):
        if self.modo != "carpeta":
            return True  # la búsqueda ya respeta los ocultos y la papelera los enseña todos
        if not self.ocultos and archivos.es_oculto(info):
            return False
        return not self.texto or self.texto in archivos.plegar(info.get_display_name() or "")

    def poner(self, archivo, info, ubicacion="", fecha=None, avisar=True):
        """Añade la fila de archivo o la actualiza si ya estaba."""
        uri = archivo.get_uri()
        self.todas[uri] = (archivo, info, ubicacion, fecha)
        if not self._visible(archivo, info):
            if uri in self.filas:
                self.store.remove(self.filas.pop(uri))
                self.emit("cambiado")
            return
        es_dir = archivos.es_carpeta(info)
        tamano = info.get_size() if not es_dir else 0
        nombre = info.get_display_name() or info.get_name() or "?"
        fecha = archivos.segundos(info) if fecha is None else fecha
        icono = self.iconos.de(info.get_icon(), self.tam)
        if uri in self.miniaturas_hechas:
            icono = escalar(self.miniaturas_hechas[uri], self.tam)
        valores = {C_NOMBRE: nombre, C_ICONO: icono,
                   C_TAMANO_TXT: "" if es_dir else archivos.tamano_texto(tamano),
                   C_TIPO: archivos.tipo_texto(info), C_FECHA_TXT: archivos.fecha_texto(fecha),
                   C_UBICACION: ubicacion, C_ES_DIR: es_dir,
                   # insert_with_valuesv pasa los int de Python como gint: con 2 GiB o más fallaría
                   C_TAMANO: GObject.Value(GObject.TYPE_INT64, tamano),
                   C_FECHA: GObject.Value(GObject.TYPE_INT64, fecha),
                   C_CLAVE: GLib.utf8_collate_key_for_filename(nombre, -1), C_ARCHIVO: archivo,
                   C_INFO: info, C_URI: uri}
        columnas, datos = list(valores), list(valores.values())
        if uri in self.filas:
            self.store.set(self.filas[uri], columnas, datos)
        else:
            self.filas[uri] = self.store.insert_with_valuesv(-1, columnas, datos)
        self._pedir_miniatura(archivo, info)
        if avisar:
            self._pedir_orden()
            self.emit("nueva", uri)

    def quitar(self, uri):
        self.todas.pop(uri, None)
        fila = self.filas.pop(uri, None)
        if fila:
            self.store.remove(fila)
            self.emit("cambiado")

    def _pedir_miniatura(self, archivo, info):
        uri = archivo.get_uri()
        cancelable = self.cancelable
        grande = self.tam > 128

        def lista(pixbuf):
            self.miniaturas_hechas[uri] = pixbuf
            fila = self.filas.get(uri)
            if fila and not cancelable.is_cancelled():
                self.store.set_value(fila, C_ICONO, escalar(pixbuf, self.tam))

        self.miniaturas.pedir(archivo, info, grande, lista, cancelable)

    def poner_tamano(self, tam):
        """Cambia el tamaño de los iconos de todas las filas (a tandas en reposo)."""
        if tam == self.tam:
            return
        mas_grande = tam > 128 >= self.tam
        self.tam = tam
        filas = list(self.filas.items())

        def tanda():
            for uri, fila in filas[:400]:
                if self.filas.get(uri) is not fila:
                    continue
                info = self.store.get_value(fila, C_INFO)
                if uri in self.miniaturas_hechas and not mas_grande:
                    icono = escalar(self.miniaturas_hechas[uri], tam)
                else:
                    icono = self.iconos.de(info.get_icon(), tam)
                    if mas_grande:
                        self.miniaturas_hechas.pop(uri, None)
                        self._pedir_miniatura(self.store.get_value(fila, C_ARCHIVO), info)
                self.store.set_value(fila, C_ICONO, icono)
            del filas[:400]
            return bool(filas) and self.tam == tam

        if tanda():
            GLib.idle_add(tanda)

    # Orden

    def _pedir_orden(self):
        if not self.reordenar and not self.cargando:
            self.reordenar = GLib.timeout_add(120, self._orden_pedido)

    def _orden_pedido(self):
        self.reordenar = 0
        self.ordenar()
        self.emit("cambiado")
        return False

    def ordenar(self, campo=None, descendente=None):
        """Ordena las filas; las carpetas siempre van primero, también al revés, como en Thunar."""
        if campo:
            self.orden = (campo, descendente)
        campo, descendente = self.orden
        indice = {"nombre": C_CLAVE, "tamano": C_TAMANO, "tipo": C_TIPO, "fecha": C_FECHA}[campo]
        claves, carpetas = [], []
        for fila in self.store:
            nombre = fila[C_CLAVE]
            valor = fila[indice]
            claves.append(nombre if campo == "nombre" else
                          (valor.casefold() if campo == "tipo" else valor, nombre))
            carpetas.append(fila[C_ES_DIR])
        orden = sorted(range(len(claves)), key=claves.__getitem__, reverse=descendente)
        orden.sort(key=lambda i: not carpetas[i])
        if orden != list(range(len(orden))):
            self.store.reorder(orden)


# --- Vistas --------------------------------------------------------------------

def modificadores():
    """Teclas modificadoras pulsadas ahora mismo (sirve también durante un arrastre)."""
    return Gdk.Keymap.get_for_display(Gdk.Display.get_default()).get_modifier_state()


class Vista:
    """Una de las tres vistas sobre el modelo filtrado, con la misma interfaz para el panel.

    dueno es el panel: recibe activar(ruta), menu(evento, sobre_elemento), clic_medio(ruta),
    seleccion_cambiada(), uris_seleccion(), destino_soltar(ruta) y soltar(uris, destino, accion).
    """

    def __init__(self, tipo, modelo, tam, dueno):
        self.tipo, self.modelo, self.tam, self.dueno = tipo, modelo, tam, dueno
        self.retenida = None        # elemento pulsado de una selección múltiple (puede ser un arrastre)
        self.columnas = {}
        self.es_lista = tipo == "detallada"
        self.w = self._lista() if self.es_lista else self._iconos()
        self.w.get_style_context().add_class("vista")
        self.w.get_style_context().add_class(f"vista-{tipo}")
        self.w.connect("button-press-event", self._pulsar)
        self.w.connect("button-release-event", self._soltar_boton)
        self.w.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, OBJETIVOS, ACCIONES_DND)
        self.w.drag_dest_set(0, OBJETIVOS, ACCIONES_DND)
        self.w.connect("drag-begin", self._empieza_arrastre)
        self.w.connect("drag-end", self._acaba_arrastre)
        self.w.connect("drag-data-get", self._datos_arrastre)
        # Mover sin que la vista borre la fila del modelo: de eso se ocupa el monitor
        self.w.connect("drag-data-delete", lambda w, _c: w.stop_emission_by_name("drag-data-delete"))
        self.w.connect("drag-motion", self._sobre)
        self.w.connect("drag-leave", lambda *_: self.resaltar(None))
        self.w.connect("drag-drop", self._dejar)
        self.w.connect("drag-data-received", self._recibido)

    def _iconos(self):
        v = Gtk.IconView()
        if self.modelo is not None:
            v.set_model(self.modelo)
        v.set_selection_mode(Gtk.SelectionMode.MULTIPLE)
        imagen = Gtk.CellRendererPixbuf()
        texto = Gtk.CellRendererText()
        v.pack_start(imagen, False)
        v.pack_start(texto, True)
        v.add_attribute(imagen, "pixbuf", C_ICONO)
        v.add_attribute(texto, "text", C_NOMBRE)
        if self.tipo == "compacta":
            v.set_item_orientation(Gtk.Orientation.HORIZONTAL)
            v.set_item_width(self.tam + 200)
            v.set_row_spacing(0)
            v.set_column_spacing(6)
            v.set_margin(6)
            v.set_item_padding(2)
            texto.set_property("ellipsize", 3)
            texto.set_property("xalign", 0)
        else:
            ancho = max(self.tam + 48, 120)
            v.set_item_width(ancho)
            v.set_row_spacing(6)
            v.set_column_spacing(6)
            v.set_margin(10)
            v.set_item_padding(4)
            texto.set_property("wrap-mode", 2)      # PANGO_WRAP_WORD_CHAR
            texto.set_property("wrap-width", ancho - 8)
            texto.set_property("alignment", 2)      # PANGO_ALIGN_CENTER
            texto.set_property("xalign", 0.5)
            imagen.set_property("height", self.tam)  # alinea los nombres aunque haya miniaturas
        v.connect("item-activated", lambda _v, ruta: self.dueno.activar(ruta))
        v.connect("selection-changed", lambda _v: self.dueno.seleccion_cambiada())
        return v

    def _lista(self):
        v = Gtk.TreeView(model=self.modelo)
        v.set_enable_search(False)
        v.set_rubber_banding(True)
        v.set_fixed_height_mode(True)
        seleccion = v.get_selection()
        seleccion.set_mode(Gtk.SelectionMode.MULTIPLE)
        seleccion.set_select_function(lambda *_: self.retenida is None)
        seleccion.connect("changed", lambda _s: self.dueno.seleccion_cambiada())
        for campo, titulo, ancho, columna_texto in (("nombre", "Nombre", 300, C_NOMBRE),
                                                    ("ubicacion", "Ubicación", 220, C_UBICACION),
                                                    ("tamano", "Tamaño", 90, C_TAMANO_TXT),
                                                    ("tipo", "Tipo", 170, C_TIPO),
                                                    ("fecha", "Fecha de modificación", 190, C_FECHA_TXT)):
            col = Gtk.TreeViewColumn(title=titulo)
            col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            col.set_fixed_width(ancho)
            col.set_resizable(True)
            if campo == "nombre":
                imagen = Gtk.CellRendererPixbuf()
                imagen.set_fixed_size(self.tam + 4, self.tam + 2)
                col.pack_start(imagen, False)
                col.add_attribute(imagen, "pixbuf", C_ICONO)
                col.set_expand(True)
            texto = Gtk.CellRendererText(ellipsize=3)
            if campo == "tamano":
                texto.set_property("xalign", 1)
            col.pack_start(texto, True)
            col.add_attribute(texto, "text", columna_texto)
            if campo in ORDENES:
                col.set_clickable(True)
                col.connect("clicked", lambda _c, c=campo: self.dueno.ordenar_por(c))
            else:
                col.set_visible(False)
            self.columnas[campo] = col
            v.append_column(col)
        v.connect("row-activated", lambda _v, ruta, _c: self.dueno.activar(ruta))
        return v

    # Lo que el panel necesita de cualquier vista

    def modo(self, modo):
        """Columna de ubicación en búsquedas y en la papelera (con la fecha de borrado)."""
        if not self.es_lista:
            return
        self.columnas["ubicacion"].set_visible(modo != "carpeta")
        self.columnas["ubicacion"].set_title("Ubicación original" if modo == "papelera" else "Ubicación")
        self.columnas["fecha"].set_title("Fecha de borrado" if modo == "papelera"
                                         else "Fecha de modificación")

    def mostrar_orden(self, campo, descendente):
        for c, col in self.columnas.items():
            col.set_sort_indicator(c == campo)
            col.set_sort_order(Gtk.SortType.DESCENDING if descendente else Gtk.SortType.ASCENDING)

    def seleccion(self):
        if self.es_lista:
            return self.w.get_selection().get_selected_rows()[1]
        return self.w.get_selected_items()

    def esta_seleccionada(self, ruta):
        if self.es_lista:
            return self.w.get_selection().path_is_selected(ruta)
        return self.w.path_is_selected(ruta)

    def seleccionar(self, rutas, cursor=True):
        sel = self.w.get_selection() if self.es_lista else self.w
        sel.unselect_all()
        for ruta in rutas:
            sel.select_path(ruta)
        if rutas and cursor:
            self.poner_cursor(rutas[0], seleccionar=False)
            self.desplazar(rutas[0])

    def seleccionar_todo(self):
        (self.w.get_selection() if self.es_lista else self.w).select_all()

    def poner_cursor(self, ruta, seleccionar=True):
        if self.es_lista:
            if seleccionar:
                self.w.set_cursor(ruta, None, False)
            else:
                # Mover el cursor de un TreeView cambia la selección: se guarda y se repone
                rutas = self.seleccion()
                self.w.set_cursor(ruta, None, False)
                self.seleccionar(rutas, cursor=False)
        else:
            self.w.set_cursor(ruta, None, False)
            if seleccionar:
                self.seleccionar([ruta], cursor=False)

    def desplazar(self, ruta):
        if self.es_lista:
            self.w.scroll_to_cell(ruta, None, False, 0, 0)
        else:
            self.w.scroll_to_path(ruta, False, 0, 0)

    def ruta_en(self, x, y, del_widget=False):
        """Ruta del elemento en (x, y); del_widget si las coordenadas son del widget."""
        if del_widget:
            x, y = self.w.convert_widget_to_bin_window_coords(x, y)
        if self.es_lista:
            r = self.w.get_path_at_pos(x, y)
            return r[0] if r else None
        return self.w.get_path_at_pos(x, y)

    def resaltar(self, ruta):
        if self.es_lista:
            self.w.set_drag_dest_row(ruta, Gtk.TreeViewDropPosition.INTO_OR_AFTER)
        else:
            self.w.set_drag_dest_item(ruta, Gtk.IconViewDropPosition.DROP_INTO)

    # Ratón

    def _pulsar(self, _w, evento):
        if evento.type != Gdk.EventType.BUTTON_PRESS:
            return False
        if self.es_lista and evento.window != self.w.get_bin_window():
            return False  # cabecera de las columnas
        self.dueno.enfocar()
        ruta = self.ruta_en(evento.x, evento.y)
        mods = evento.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        if evento.button == Gdk.BUTTON_SECONDARY:
            self._abrir_menu(evento, ruta, mods)
            return True
        if evento.button == Gdk.BUTTON_MIDDLE and ruta is not None:
            self.dueno.clic_medio(ruta)
            return True
        if evento.button == Gdk.BUTTON_PRIMARY:
            if ruta is None and not mods and self.es_lista:
                self.seleccionar([])
            elif ruta is not None and not mods and self.esta_seleccionada(ruta) \
                    and len(self.seleccion()) > 1:
                self.retenida = ruta  # puede empezar un arrastre de toda la selección
        return False

    def _abrir_menu(self, evento, ruta, mods):
        """Clic derecho: el menú es de lo pulsado, así que antes se selecciona (si no lo estaba)."""
        if ruta is None:
            if not mods:
                self.seleccionar([])
        elif not self.esta_seleccionada(ruta):
            self.seleccionar([ruta], cursor=False)
            self.poner_cursor(ruta, seleccionar=False)
        self.w.grab_focus()
        self.dueno.menu(evento, ruta is not None)

    def _soltar_boton(self, _w, evento):
        if evento.button == Gdk.BUTTON_PRIMARY and self.retenida is not None:
            ruta, self.retenida = self.retenida, None
            self.seleccionar([ruta], cursor=False)
        return False

    # Arrastrar y soltar

    def _empieza_arrastre(self, _w, _ctx):
        self.retenida = None
        ARRASTRANDO[:] = self.dueno.uris_seleccion()

    def _acaba_arrastre(self, _w, _ctx):
        ARRASTRANDO.clear()

    def _datos_arrastre(self, _w, _ctx, datos, _info, _t):
        datos.set_uris(self.dueno.uris_seleccion())

    def _sobre(self, _w, ctx, x, y, t):
        ruta = self.ruta_en(x, y, del_widget=True)
        destino, sobre_carpeta = self.dueno.destino_soltar(ruta)
        if destino is None:
            self.resaltar(None)
            Gdk.drag_status(ctx, 0, t)
            return True
        self.resaltar(ruta if sobre_carpeta else None)
        Gdk.drag_status(ctx, accion_soltar(ctx, destino), t)
        return True

    def _dejar(self, _w, ctx, x, y, t):
        self.ruta_soltar = self.ruta_en(x, y, del_widget=True)
        objetivo = self.w.drag_dest_find_target(ctx, None)
        if objetivo is None:
            return False
        self.w.drag_get_data(ctx, objetivo, t)
        return True

    def _recibido(self, _w, ctx, _x, _y, datos, _info, t):
        uris = datos.get_uris() or []
        destino, _ = self.dueno.destino_soltar(getattr(self, "ruta_soltar", None))
        accion = accion_soltar(ctx, destino) if destino else 0
        self.resaltar(None)
        Gtk.drag_finish(ctx, bool(uris and destino), False, t)
        if uris and destino:
            self.dueno.soltar(uris, destino, accion)


_CONSULTAS = {}  # (uri, atributo) -> (momento, valor): el arrastre pregunta decenas de veces por segundo


def consultar_rapido(gfile, atributo):
    """Valor de un atributo de texto o booleano, guardado unos segundos para que arrastrar sobre
    una carpeta (incluso en un montaje de red) no repita la consulta en cada movimiento."""
    clave = (gfile.get_uri(), atributo)
    ahora = GLib.get_monotonic_time()
    guardado = _CONSULTAS.get(clave)
    if guardado and ahora - guardado[0] < 5_000_000:
        return guardado[1]
    try:
        info = gfile.query_info(atributo, Gio.FileQueryInfoFlags.NONE, None)
        if info.get_attribute_type(atributo) == Gio.FileAttributeType.BOOLEAN:
            valor = info.get_attribute_boolean(atributo)
        else:
            valor = info.get_attribute_string(atributo)
    except GLib.Error:
        valor = None
    if len(_CONSULTAS) > 256:
        _CONSULTAS.clear()
    _CONSULTAS[clave] = (ahora, valor)
    return valor


def mismo_sistema(uris, destino):
    """True si el primer archivo arrastrado está en el mismo sistema de archivos que destino."""
    a = consultar_rapido(Gio.File.new_for_uri(uris[0]), "id::filesystem")
    return a is not None and a == consultar_rapido(destino, "id::filesystem")


def accion_soltar(ctx, destino):
    """Acción al soltar como en Thunar: Ctrl copia, Mayús mueve, Ctrl+Mayús enlaza, Alt pregunta;
    sin teclas, mover dentro del mismo sistema de archivos y copiar entre sistemas distintos."""
    M = Gdk.ModifierType
    mods = modificadores()
    D = Gdk.DragAction
    if mods & M.MOD1_MASK:
        accion = D.ASK
    elif mods & M.CONTROL_MASK and mods & M.SHIFT_MASK:
        accion = D.LINK
    elif mods & M.CONTROL_MASK:
        accion = D.COPY
    elif mods & M.SHIFT_MASK:
        accion = D.MOVE
    else:
        accion = D.MOVE if ARRASTRANDO and mismo_sistema(ARRASTRANDO, destino) else D.COPY
    posibles = ctx.get_actions()
    if not posibles & accion:
        accion = D.COPY if posibles & D.COPY else ctx.get_suggested_action()
    return accion
