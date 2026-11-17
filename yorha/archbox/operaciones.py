"""Operaciones con archivos de ARCHBOX: copiar, mover, enlazar, renombrar, papelera, borrar,
crear, restaurar; con conflictos, progreso, deshacer/rehacer y el portapapeles.

Cada operación es un Trabajo que corre en su propio hilo con las llamadas de Gio (como los
ThunarJob), y nunca toca GTK desde ese hilo: las preguntas (conflictos, errores) se hacen en
el bucle de GTK y el hilo espera la respuesta. La ventana de progreso aparece solo si la
operación tarda, con bytes, velocidad, tiempo restante, pausa y cancelar.

Todo lo que se puede deshacer se describe con un «registro» (un dict): ejecutar(registro)
hace la operación y deshacer() ejecuta la inversa; así deshacer y rehacer usan el mismo código.
"""
import contextlib
import os
import threading
import time
import traceback

from gi.repository import Gdk, Gio, GLib, Gtk

import archivos
import dialogos

MAX_DESHACER = 30
ESPERA_PROGRESO = 700   # ms antes de enseñar la ventana de progreso
NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
E = Gio.IOErrorEnum


class Cancelado(Exception):
    pass


def es_error(error, codigo):
    return isinstance(error, GLib.Error) and error.matches(Gio.io_error_quark(), codigo)


def tipo_de(archivo):
    try:
        return archivo.query_info("standard::type", NOFOLLOW, None).get_file_type()
    except GLib.Error:
        return None


def contar(lista):
    return "1 elemento" if len(lista) == 1 else f"{len(lista)} elementos"


# --- Trabajo -------------------------------------------------------------------

class Trabajo:
    """Una operación en su hilo. funcion(trabajo) devuelve el registro para deshacer (o None)."""

    def __init__(self, ops, titulo, funcion, al_terminar=None):
        self.ops, self.titulo, self.funcion, self.al_terminar = ops, titulo, funcion, al_terminar
        self.cancelable = Gio.Cancellable()
        self.seguir = threading.Event()
        self.seguir.set()
        self.total = self.hecho = self.base = 0
        self.archivos_total = self.archivos_hechos = 0
        self.actual = ""
        self.todos = {}          # respuestas con «Aplicar a todos»
        self.creados = []        # URIs nuevas, para seleccionarlas en la vista
        self.registro = None
        self.terminado = False
        self.esperando = False   # hay un diálogo abierto esperando al usuario
        self.inicio = time.monotonic()
        self.pausado_desde = None
        self.pausado = 0.0

    def empezar(self):
        threading.Thread(target=self._correr, daemon=True).start()

    def _correr(self):
        try:
            self.registro = self.funcion(self)
        except Cancelado:
            self.registro = None
        except GLib.Error as e:
            if not self.cancelable.is_cancelled():
                self.preguntar(dialogos.error_trabajo, self.titulo, e.message, False)
        except Exception as e:  # noqa: BLE001 — un fallo inesperado no debe dejar el trabajo colgado
            traceback.print_exc()
            self.registro = None
            with contextlib.suppress(Exception):
                self.preguntar(dialogos.error_trabajo, self.titulo, f"Error inesperado: {e}", False)
        finally:
            GLib.idle_add(self._fin)

    def _fin(self):
        self.terminado = True
        self.ops.trabajo_terminado(self)
        if self.al_terminar:
            self.al_terminar(self)
        return False

    # Control desde la interfaz

    def pausar(self, si):
        if si:
            self.seguir.clear()
            self.pausado_desde = time.monotonic()
        else:
            if self.pausado_desde:
                self.pausado += time.monotonic() - self.pausado_desde
                self.pausado_desde = None
            self.seguir.set()

    def cancelar(self):
        self.cancelable.cancel()
        self.seguir.set()

    # Desde el hilo

    def esperar(self):
        """Punto de pausa y de cancelación entre archivos."""
        self.seguir.wait()
        if self.cancelable.is_cancelled():
            raise Cancelado()

    def progreso(self, actuales, _total, *_):
        self.hecho = self.base + actuales
        if not self.seguir.is_set():
            self.seguir.wait()  # pausa en medio de un archivo grande

    def preguntar(self, dialogo, *args):
        """Abre dialogo(padre, *args, al_responder) en GTK y espera su respuesta en este hilo."""
        listo = threading.Event()
        respuesta = []

        def responder(valor):
            respuesta.append(valor)
            listo.set()

        def abrir():
            dialogo(self.ops.padre(), *args, responder)
            return False

        self.esperando = True
        GLib.idle_add(abrir)
        listo.wait()
        self.esperando = False
        return respuesta[0]

    def conflicto(self, origen, destino):
        """("reemplazar"|"saltar"|"renombrar", destino final) cuando destino ya existe."""
        es_dir = tipo_de(origen) == Gio.FileType.DIRECTORY
        clave = "carpeta" if es_dir and tipo_de(destino) == Gio.FileType.DIRECTORY else "archivo"
        if clave in self.todos:
            decision = self.todos[clave]
            nombre = None
        else:
            sugerido = archivos.nombre_libre(destino.get_parent(), destino.get_basename(), None, es_dir)
            decision, nombre, a_todos = self.preguntar(dialogos.conflicto, origen, destino, sugerido)
            if decision == "cancelar":
                self.cancelar()
                raise Cancelado()
            if a_todos:
                self.todos[clave] = decision
        if decision == "renombrar":
            nombre = nombre or archivos.nombre_libre(destino.get_parent(), destino.get_basename(),
                                                     None, es_dir)
            return decision, destino.get_parent().get_child(nombre)
        return decision, destino

    def fallo(self, archivo, error):
        """Error con un archivo: se salta (y se sigue) o se cancela todo."""
        if self.cancelable.is_cancelled():
            raise Cancelado()
        if "error" in self.todos:
            return
        saltar, a_todos = self.preguntar(dialogos.error_trabajo,
                                         f"{self.titulo}: {archivo.get_parse_name()}",
                                         error.message, True)
        if not saltar:
            self.cancelar()
            raise Cancelado()
        if a_todos:
            self.todos["error"] = True


# --- Funciones de los trabajos (corren en el hilo) ------------------------------------

def medir(t, lista):
    """Suma bytes y archivos de lista (recursivo, sin seguir enlaces) para el progreso."""
    pendientes = list(lista)
    while pendientes:
        t.esperar()
        f = pendientes.pop()
        try:
            info = f.query_info("standard::type,standard::size", NOFOLLOW, t.cancelable)
        except GLib.Error:
            continue
        t.archivos_total += 1
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            try:
                for hijo in f.enumerate_children("standard::name", NOFOLLOW, t.cancelable):
                    pendientes.append(f.get_child(hijo.get_name()))
            except GLib.Error:
                pass
        else:
            t.total += info.get_size()


def hijos(carpeta, cancelable):
    return [carpeta.get_child(i.get_name())
            for i in carpeta.enumerate_children("standard::name", NOFOLLOW, cancelable)]


def transferir(t, pares, mover):
    """Copia o mueve cada (origen, destino); devuelve el registro con lo que se hizo."""
    if not mover:
        medir(t, [o for o, _ in pares])
    else:
        t.archivos_total = len(pares)
    hechos = []  # lo que deshacer debe devolver o quitar: nunca lo que ya estaba en el destino
    with contextlib.suppress(Cancelado):  # cancelada a medias: lo ya hecho se puede deshacer
        for origen, destino in pares:
            t.esperar()
            try:
                # Al deshacer una mezcla, la carpeta de origen pudo desaparecer: se vuelve a crear
                destino.get_parent().make_directory_with_parents(t.cancelable)
            except GLib.Error as e:
                if not es_error(e, E.EXISTS):
                    t.fallo(origen, e)
                    continue
            try:
                final, _nuevo = transferir_uno(t, origen, destino, mover, hechos)
            except GLib.Error as e:
                t.fallo(origen, e)
                continue
            if final:
                t.creados.append(final.get_uri())
    if hechos:
        return {"tipo": "mover" if mover else "copiar", "pares": hechos}
    return None


def transferir_uno(t, origen, destino, mover, registro=None):
    """(destino final o None si se saltó, True si no existía antes).

    En registro (si se da) se apunta (origen, final) de cada cosa que deshacer debe tocar: lo
    nuevo, y al mover también lo reemplazado. Al mezclar carpetas se apuntan sus hijos uno a
    uno, nunca la carpeta que ya existía (deshacer se llevaría lo que había antes en ella)."""
    t.actual = origen.get_basename()
    es_dir = tipo_de(origen) == Gio.FileType.DIRECTORY
    if es_dir and destino.has_prefix(origen):
        raise GLib.Error.new_literal(Gio.io_error_quark(),
                                     "No se puede poner una carpeta dentro de sí misma.",
                                     E.INVALID_ARGUMENT)
    preparado = _preparar_destino(t, origen, destino, mover, es_dir)
    if preparado is None:
        return None, False
    destino, reemplazar, tipo_destino = preparado
    if es_dir:
        return _transferir_carpeta(t, origen, destino, mover, registro, tipo_destino)
    return _transferir_archivo(t, origen, destino, mover, registro, reemplazar, tipo_destino)


def _apuntar(registro, origen, final, nuevo, mover):
    """Apunta (origen, final) en registro si deshacer debe tocarlo; devuelve (final, nuevo)."""
    if registro is not None and final is not None and (nuevo or mover):
        registro.append((origen.get_uri(), final.get_uri()))
    return final, nuevo


def _preparar_destino(t, origen, destino, mover, es_dir):
    """(destino final, si se reemplaza, tipo de lo que ya hay allí); None si no hay nada que hacer.

    Sobre sí mismo, copiar busca un nombre «copia» y mover no hace nada; si el destino ya
    existe se pregunta (o se usa la respuesta de «Aplicar a todos»)."""
    if destino.equal(origen):
        if mover:
            return None
        carpeta = destino.get_parent()
        destino = carpeta.get_child(archivos.nombre_libre(carpeta, destino.get_basename(), "copia", es_dir))
    reemplazar = False
    tipo_destino = tipo_de(destino)
    if tipo_destino is not None:
        decision, destino = t.conflicto(origen, destino)
        if decision == "saltar":
            return None
        reemplazar = decision == "reemplazar"
        tipo_destino = tipo_de(destino)
    return destino, reemplazar, tipo_destino


def _mover_de_una_vez(t, origen, destino):
    """Mueve la carpeta entera con un solo cambio de sitio; False si hay que copiarla hijo a
    hijo (otro sistema de archivos)."""
    try:
        origen.move(destino,
                    Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE,
                    t.cancelable, None)
    except GLib.Error as e:
        if not (es_error(e, E.WOULD_RECURSE) or es_error(e, E.NOT_SUPPORTED)):
            raise
        return False
    t.archivos_hechos += 1
    return True


def _transferir_carpeta(t, origen, destino, mover, registro, tipo_destino):
    """Copia o mueve una carpeta: de una vez si se puede, si no creándola (o mezclándola con la
    que ya hay) y pasando sus hijos uno a uno."""
    nuevo = tipo_destino is None
    if tipo_destino not in (None, Gio.FileType.DIRECTORY):
        raise GLib.Error.new_literal(Gio.io_error_quark(),
                                     "Ya hay un archivo con ese nombre; no se reemplaza por una carpeta.",
                                     E.EXISTS)
    if mover and nuevo and _mover_de_una_vez(t, origen, destino):
        return _apuntar(registro, origen, destino, True, mover)
    if nuevo:
        destino.make_directory(t.cancelable)
        with contextlib.suppress(GLib.Error):
            origen.copy_attributes(destino, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, t.cancelable)
    t.archivos_hechos += 1
    # Carpeta nueva: se apunta entera. Mezcla con una que ya existía: se apunta cada hijo
    try:
        for hijo in hijos(origen, t.cancelable):
            t.esperar()
            try:
                transferir_uno(t, hijo, destino.get_child(hijo.get_basename()), mover,
                               None if nuevo else registro)
            except GLib.Error as e:
                t.fallo(hijo, e)
    except (Cancelado, GLib.Error):
        if nuevo:  # se quedó a medias, pero ya existe: deshacer también debe tocarla
            _apuntar(registro, origen, destino, True, mover)
        raise
    if mover:
        with contextlib.suppress(GLib.Error):  # quedó algo dentro (saltado)
            origen.delete(t.cancelable)
    return _apuntar(registro, origen, destino, True, mover) if nuevo else (destino, False)


def _transferir_archivo(t, origen, destino, mover, registro, reemplazar, tipo_destino):
    """Copia o mueve un archivo (o enlace) con progreso; al cancelar no deja copias a medias."""
    nuevo = tipo_destino is None
    if tipo_destino == Gio.FileType.DIRECTORY and reemplazar:
        raise GLib.Error.new_literal(Gio.io_error_quark(),
                                     "Ya hay una carpeta con ese nombre; no se reemplaza por un archivo.",
                                     E.IS_DIRECTORY)
    flags = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | (
        Gio.FileCopyFlags.OVERWRITE if reemplazar else Gio.FileCopyFlags.NONE)
    try:
        tamano = origen.query_info("standard::size", NOFOLLOW, None).get_size()
    except GLib.Error:
        tamano = 0
    try:
        if mover:
            origen.move(destino, flags, t.cancelable, t.progreso, None)
        else:
            origen.copy(destino, flags, t.cancelable, t.progreso, None)
    except GLib.Error as e:
        # GIO puede dejar la copia (entera o a medias) al cancelar: si el archivo es nuevo, se quita
        if es_error(e, E.CANCELLED) and nuevo:
            with contextlib.suppress(GLib.Error):
                destino.delete(None)
        raise
    t.base += tamano
    t.hecho = t.base
    t.archivos_hechos += 1
    return _apuntar(registro, origen, destino, nuevo, mover)


def enlazar(t, pares):
    """Crea enlaces simbólicos: (archivo al que apunta, enlace)."""
    hechos = []
    with contextlib.suppress(Cancelado):  # cancelada a medias: lo ya hecho se puede deshacer
        for objetivo, enlace in pares:
            t.esperar()
            if tipo_de(enlace) is not None:
                decision, enlace = t.conflicto(objetivo, enlace)
                if decision == "saltar":
                    continue
                if decision == "reemplazar":
                    try:
                        enlace.delete(t.cancelable)
                    except GLib.Error as e:
                        t.fallo(enlace, e)
                        continue
            try:
                enlace.make_symbolic_link(objetivo.get_path() or objetivo.get_uri(), t.cancelable)
            except GLib.Error as e:
                t.fallo(objetivo, e)
                continue
            hechos.append((objetivo.get_uri(), enlace.get_uri()))
            t.creados.append(enlace.get_uri())
    return {"tipo": "enlazar", "pares": hechos} if hechos else None


def cambiar_nombre(f, nombre, cancelable=None):
    """set_display_name que también sirve para cambiar solo mayúsculas en FAT/exFAT (allí el
    nombre nuevo «ya existe»): en ese caso pasa por un nombre temporal."""
    if nombre != f.get_basename() and nombre.casefold() == f.get_basename().casefold():
        temporal = f.set_display_name(f".archbox-{os.getpid()}-mayus", cancelable)
        try:
            return temporal.set_display_name(nombre, None)
        except GLib.Error:
            temporal.set_display_name(f.get_basename(), None)
            raise
    return f.set_display_name(nombre, cancelable)


def renombrar(t, pares):
    """Renombra (archivo, archivo nuevo en la misma carpeta); en dos pasos si unos nombres son
    los antiguos de otros (p. ej. al numerar). Si se cancela a medias, los que estaban en el
    nombre temporal vuelven a su nombre de antes: nunca quedan ocultos como .archbox-…"""
    pares = [(o, d) for o, d in pares if not o.equal(d)]
    origenes = {o.get_uri() for o, _ in pares}
    cruzados = any(d.get_uri() in origenes for _, d in pares)
    hechos, intermedios = [], []
    cancelado = []
    try:
        _renombrar_o_apartar(t, pares, cruzados, hechos, intermedios)
    except Cancelado:
        cancelado.append(True)
    # Segunda fase: siempre se hace, también tras cancelar (entonces todos vuelven a su nombre)
    _dar_nombres_finales(t, intermedios, hechos, cancelado)
    t.creados = [d for _, d in hechos]
    return {"tipo": "renombrar", "pares": hechos} if hechos else None


def _renombrar_o_apartar(t, pares, cruzados, hechos, intermedios):
    """Primera fase: renombra cada uno (a hechos) o, si hay nombres cruzados, lo aparta a un
    nombre temporal (a intermedios). Deja salir Cancelado para que renombrar haga la segunda."""
    for i, (origen, destino) in enumerate(pares):
        t.esperar()
        if not cruzados and tipo_de(destino) is not None and \
                destino.get_basename().casefold() != origen.get_basename().casefold():
            t.fallo(origen, GLib.Error.new_literal(Gio.io_error_quark(),
                                                   f"Ya existe «{destino.get_basename()}».", E.EXISTS))
            continue
        try:
            if cruzados:
                temporal = origen.set_display_name(f".archbox-{os.getpid()}-{i}", t.cancelable)
                intermedios.append((origen, temporal, destino))
            else:
                final = cambiar_nombre(origen, destino.get_basename(), t.cancelable)
                hechos.append((origen.get_uri(), final.get_uri()))
        except GLib.Error as e:
            t.fallo(origen, e)


def _dar_nombres_finales(t, intermedios, hechos, cancelado):
    """Segunda fase: cada temporal pasa a su nombre nuevo, o al de antes si se ha cancelado.

    cancelado es una lista que se llena al cancelar, también aquí en medio: los que quedan
    entonces vuelven a su nombre de antes."""
    def avisar(origen, error):
        try:
            t.fallo(origen, error)
        except Cancelado:
            cancelado.append(True)

    for origen, temporal, destino in intermedios:
        objetivo = origen if cancelado else destino
        try:
            if tipo_de(objetivo) is not None:
                raise GLib.Error.new_literal(Gio.io_error_quark(),
                                             f"Ya existe «{objetivo.get_basename()}».", E.EXISTS)
            final = temporal.set_display_name(objetivo.get_basename(), None)
        except GLib.Error as e:
            try:
                final = temporal.set_display_name(origen.get_basename(), None)  # se deja como estaba
            except GLib.Error as e2:
                # Ni el nombre nuevo ni el de antes: se avisa de dónde ha quedado para no perderlo
                avisar(origen, GLib.Error.new_literal(
                    Gio.io_error_quark(), f"{e.message.rstrip('.')}. Además no se pudo volver al nombre "
                    f"de antes ({e2.message}); el archivo está como «{temporal.get_basename()}».", E.FAILED))
                hechos.append((origen.get_uri(), temporal.get_uri()))
                continue
            if not cancelado:
                avisar(origen, e)
        if not final.equal(origen):
            hechos.append((origen.get_uri(), final.get_uri()))


def a_la_papelera(t, lista):
    """Tira a la papelera; si un sistema de archivos no tiene, ofrece borrar definitivamente."""
    t.archivos_total = len(lista)
    rutas, sin_papelera = [], []
    with contextlib.suppress(Cancelado):  # cancelada a medias: lo ya tirado se puede deshacer
        for f in lista:
            t.esperar()
            t.actual = f.get_basename()
            if not f.get_path() or not archivos.papelera_visible(f.get_path()):
                sin_papelera.append(f)  # su papelera no se vería (p. ej. /tmp): se ofrece borrar
                t.archivos_hechos += 1
                continue
            try:
                f.trash(t.cancelable)
                rutas.append(f.get_path())
            except GLib.Error as e:
                if es_error(e, E.NOT_SUPPORTED):
                    sin_papelera.append(f)
                else:
                    t.fallo(f, e)
            t.archivos_hechos += 1
    if sin_papelera and not t.cancelable.is_cancelled() \
            and t.preguntar(dialogos.confirmar_sin_papelera, len(sin_papelera)):
        borrar(t, sin_papelera)
    return {"tipo": "papelera", "rutas": [r for r in rutas if r]} if rutas else None


def borrar_recursivo(t, f):
    t.esperar()
    t.actual = f.get_basename()
    if tipo_de(f) == Gio.FileType.DIRECTORY:
        for hijo in hijos(f, t.cancelable):
            borrar_recursivo(t, hijo)
    f.delete(t.cancelable)
    t.archivos_hechos += 1


def borrar(t, lista):
    medir(t, lista)
    for f in lista:
        try:
            borrar_recursivo(t, f)
        except GLib.Error as e:
            t.fallo(f, e)


def restaurar(t, elementos):
    """Devuelve elementos de la papelera a su sitio, creando la carpeta si ya no existe."""
    rutas = []
    t.archivos_total = len(elementos)
    with contextlib.suppress(Cancelado):  # cancelada a medias: lo ya restaurado se puede deshacer
        for e in elementos:
            t.esperar()
            if not e or not e.original:
                continue
            origen, destino = Gio.File.new_for_path(e.archivo), Gio.File.new_for_path(e.original)
            if e.base and not archivos.destino_seguro(e.original, e.base):
                t.fallo(destino, GLib.Error.new_literal(
                    Gio.io_error_quark(), "La carpeta de destino no es segura (otro usuario puede "
                    "cambiarla o sale del disco); no se restaura.", E.PERMISSION_DENIED))
                continue
            try:
                destino.get_parent().make_directory_with_parents(t.cancelable)
            except GLib.Error as error:
                if not es_error(error, E.EXISTS):
                    t.fallo(destino, error)
                    continue
            if e.base and not archivos.destino_seguro(e.original, e.base):
                t.fallo(destino, GLib.Error.new_literal(
                    Gio.io_error_quark(), "La carpeta de destino no es segura (otro usuario puede "
                    "cambiarla o sale del disco); no se restaura.", E.PERMISSION_DENIED))
                continue
            try:
                final, _ = transferir_uno(t, origen, destino, mover=True)
            except GLib.Error as error:
                t.fallo(destino, error)
                continue
            if final:
                if not os.path.lexists(e.archivo):  # al mezclar pudo quedar algo dentro (saltado)
                    with contextlib.suppress(OSError):
                        os.remove(e.info)
                rutas.append(final.get_path())
                t.creados.append(final.get_uri())
    return {"tipo": "restaurar", "rutas": rutas} if rutas else None


def vaciar_papelera(t):
    elementos = archivos.elementos_papelera()
    t.archivos_total = len(elementos)
    for e in elementos:
        try:
            borrar_recursivo(t, Gio.File.new_for_path(e.archivo))
        except GLib.Error as error:
            t.fallo(Gio.File.new_for_path(e.archivo), error)
            continue
        with contextlib.suppress(OSError):
            os.remove(e.info)


def crear(t, clase, destino, plantilla=None):
    """Carpeta o archivo nuevo (vacío o copia de una plantilla); falla si ya existe."""
    if clase == "carpeta":
        destino.make_directory(t.cancelable)
    elif plantilla:
        Gio.File.new_for_path(plantilla).copy(destino, Gio.FileCopyFlags.NONE, t.cancelable, None, None)
    else:
        destino.create(Gio.FileCreateFlags.NONE, t.cancelable).close(None)
    t.creados.append(destino.get_uri())
    return {"tipo": "crear", "clase": clase, "uri": destino.get_uri(), "plantilla": plantilla}


def quitar_creados(t, uris, papelera):
    """Deshace una creación o una copia: borra lo vacío y lo demás va a la papelera."""
    for uri in uris:
        t.esperar()
        f = Gio.File.new_for_uri(uri)
        visible = bool(f.get_path()) and archivos.papelera_visible(f.get_path())
        try:
            if papelera and visible:
                f.trash(t.cancelable)
            else:
                f.delete(t.cancelable)
        except GLib.Error as e:
            if es_error(e, E.NOT_EMPTY) and visible:
                f.trash(t.cancelable)
            elif es_error(e, E.NOT_EMPTY):
                t.fallo(f, GLib.Error.new_literal(
                    Gio.io_error_quark(), "No está vacía y aquí no hay papelera; se deja como está.",
                    E.NOT_EMPTY))
            elif not es_error(e, E.NOT_FOUND):
                t.fallo(f, e)


# --- Portapapeles ----------------------------------------------------------------

class Portapapeles:
    """Copiar y cortar compatible con otros gestores de archivos de GTK (Thunar, Nautilus...):
    x-special/gnome-copied-files («copy» o «cut» y las URI), text/uri-list y texto."""

    GNOME = Gdk.Atom.intern("x-special/gnome-copied-files", False)

    def __init__(self):
        self.uris, self.cortar = [], False
        self.moviendo = set()  # tuplas de URI cortadas cuyo movimiento está en marcha
        self.w = Gtk.Invisible()
        portapapeles = Gdk.SELECTION_CLIPBOARD
        for i, objetivo in enumerate(("x-special/gnome-copied-files", "text/uri-list",
                                      "UTF8_STRING", "text/plain;charset=utf-8", "text/plain")):
            Gtk.selection_add_target(self.w, portapapeles, Gdk.Atom.intern(objetivo, False), i)
        self.w.connect("selection-get", self._dar)
        self.w.connect("selection-clear-event", self._perdido)

    def poner(self, uris, cortar):
        self.uris, self.cortar = list(uris), cortar
        Gtk.selection_owner_set(self.w, Gdk.SELECTION_CLIPBOARD, Gtk.get_current_event_time())

    def vaciar(self):
        self.uris = []
        Gtk.selection_owner_set(None, Gdk.SELECTION_CLIPBOARD, Gtk.get_current_event_time())

    def vaciar_si(self, uris):
        """Vacía tras pegar lo cortado, solo si sigue siendo lo mismo (no algo copiado después)."""
        if self.uris and self.uris == list(uris):
            self.vaciar()

    def _perdido(self, *_):
        self.uris = []
        return False

    def _dar(self, _w, datos, info, _t):
        if info == 0:
            texto = ("cut" if self.cortar else "copy") + "\n" + "\n".join(self.uris)
            datos.set(datos.get_target(), 8, texto.encode())
        elif info == 1:
            datos.set_uris(self.uris)
        else:
            datos.set_text("\n".join(Gio.File.new_for_uri(u).get_parse_name() for u in self.uris), -1)

    def leer(self, al_terminar):
        """al_terminar(uris, cortar): lo copiado aquí o en otro programa."""
        if self.uris:
            al_terminar(list(self.uris), self.cortar)
            return
        portapapeles = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)

        def de_gnome(_p, datos):
            texto = datos.get_data().decode(errors="replace") if datos and datos.get_length() > 0 else ""
            lineas = [linea for linea in texto.split("\n") if linea]
            if lineas and lineas[0] in ("copy", "cut"):
                al_terminar(lineas[1:], lineas[0] == "cut")
            else:
                portapapeles.request_uris(de_lista)

        def de_lista(_p, uris):
            al_terminar(list(uris or []), False)

        portapapeles.request_contents(self.GNOME, de_gnome)


# --- Ventana de progreso -----------------------------------------------------------

class Progreso(Gtk.Window):
    """Las operaciones en marcha, como el diálogo de progreso de Thunar."""

    def __init__(self):
        super().__init__(title="Operaciones con archivos")
        self.get_style_context().add_class("archbox")
        self.get_style_context().add_class("progreso")
        self.set_default_size(460, -1)
        self.set_resizable(False)
        self.caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=14)
        self.add(self.caja)
        self.caja.show()
        self.filas = {}
        self.connect("delete-event", lambda *_: self.hide() or True)

    def agregar(self, t):
        fila = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        titulo = Gtk.Label(label=t.titulo, xalign=0, ellipsize=3)
        titulo.get_style_context().add_class("titulo-trabajo")
        barra = Gtk.ProgressBar()
        detalle = Gtk.Label(xalign=0, ellipsize=3)
        detalle.get_style_context().add_class("dim-label")
        botones = Gtk.Box(spacing=6)
        pausa = Gtk.ToggleButton.new()
        pausa.set_image(Gtk.Image.new_from_icon_name("media-playback-pause-symbolic", Gtk.IconSize.BUTTON))
        pausa.set_tooltip_text("Pausar")
        pausa.connect("toggled", lambda b: t.pausar(b.get_active()))
        cancelar = Gtk.Button.new_from_icon_name("process-stop-symbolic", Gtk.IconSize.BUTTON)
        cancelar.set_tooltip_text("Cancelar")
        cancelar.connect("clicked", lambda _b: t.cancelar())
        arriba = Gtk.Box(spacing=8)
        arriba.pack_start(titulo, True, True, 0)
        botones.pack_start(pausa, False, False, 0)
        botones.pack_start(cancelar, False, False, 0)
        arriba.pack_end(botones, False, False, 0)
        fila.pack_start(arriba, False, False, 0)
        fila.pack_start(barra, False, False, 0)
        fila.pack_start(detalle, False, False, 0)
        self.caja.pack_start(fila, False, False, 0)
        self.filas[t] = (fila, barra, detalle)
        fila.show_all()

    def quitar(self, t):
        fila = self.filas.pop(t, None)
        if fila:
            fila[0].destroy()
        if not self.filas:
            self.hide()

    def actualizar(self, t):
        _f, barra, detalle = self.filas[t]
        if t.total:
            barra.set_fraction(min(t.hecho / t.total, 1))
            transcurrido = time.monotonic() - t.inicio - t.pausado
            velocidad = t.hecho / transcurrido if transcurrido > 0.5 else 0
            texto = f"{GLib.format_size(t.hecho)} de {GLib.format_size(t.total)}"
            if t.pausado_desde:
                texto += " — en pausa"
            elif velocidad:
                quedan = (t.total - t.hecho) / velocidad
                texto += f" — {GLib.format_size(int(velocidad))}/s — {tiempo_restante(quedan)}"
        elif t.archivos_total:
            barra.set_fraction(min(t.archivos_hechos / t.archivos_total, 1))
            texto = f"{t.archivos_hechos} de {t.archivos_total}"
        else:
            barra.pulse()
            texto = "Preparando…"
        detalle.set_text(f"{texto}   {t.actual}")


def tiempo_restante(segundos_):
    s = int(segundos_)
    if s < 60:
        return f"quedan {s} s"
    if s < 3600:
        return f"quedan {s // 60} min {s % 60:02d} s"
    return f"quedan {s // 3600} h {s % 3600 // 60:02d} min"


# --- Operaciones -------------------------------------------------------------------

class Operaciones:
    """Lanza los trabajos, enseña su progreso y lleva las pilas de deshacer y rehacer."""

    def __init__(self, app):
        self.app = app
        self.trabajos = []
        self.deshacer_pila, self.rehacer_pila = [], []
        self.progreso = None
        self.reloj = 0
        self.portapapeles = Portapapeles()
        self.al_cambiar_historial = []   # funciones a avisar cuando cambian las pilas

    def padre(self):
        return self.app.get_active_window()

    # Lanzar

    def lanzar(self, titulo, funcion, al_terminar=None, historial="nuevo", texto=None):
        """historial: "nuevo" (se apunta para deshacer), "deshacer", "rehacer" o None;
        texto: el nombre de la operación en el menú Deshacer (el título, si no se da)."""
        def fin(t):
            if t.registro and historial:
                t.registro["texto"] = texto or titulo
                if historial == "deshacer":
                    self.rehacer_pila.append(t.registro)
                else:
                    self.deshacer_pila.append(t.registro)
                    del self.deshacer_pila[:-MAX_DESHACER]
                    if historial == "nuevo":
                        self.rehacer_pila.clear()
                self._historial_cambiado()
            if al_terminar:
                al_terminar(t)

        t = Trabajo(self, titulo, funcion, fin)
        self.trabajos.append(t)
        self.app.hold()  # cerrar la última ventana no corta una operación en marcha
        t.empezar()
        if not self.reloj:
            self.reloj = GLib.timeout_add(250, self._latido)
        return t

    def _latido(self):
        ahora = time.monotonic()
        for t in self.trabajos:
            if not t.esperando and t not in (self.progreso.filas if self.progreso else {}) \
                    and ahora - t.inicio > ESPERA_PROGRESO / 1000:
                if not self.progreso:
                    self.progreso = Progreso()
                self.progreso.agregar(t)
                self.progreso.set_transient_for(None)
                self.progreso.show()
            if self.progreso and t in self.progreso.filas:
                self.progreso.actualizar(t)
        if not self.trabajos:
            self.reloj = 0
            return False
        return True

    def trabajo_terminado(self, t):
        if t in self.trabajos:
            self.trabajos.remove(t)
            self.app.release()
        if self.progreso:
            self.progreso.quitar(t)

    def _historial_cambiado(self):
        for funcion in self.al_cambiar_historial:
            funcion()

    # Operaciones de la interfaz (destinos en el hilo de GTK)

    def copiar(self, uris, carpeta, al_terminar=None):
        pares = [(Gio.File.new_for_uri(u), carpeta.get_child(Gio.File.new_for_uri(u).get_basename()))
                 for u in uris]
        return self.lanzar(f"Copiar {contar(uris)} a «{carpeta.get_basename()}»",
                           lambda t: transferir(t, pares, False), al_terminar)

    def mover(self, uris, carpeta, al_terminar=None):
        pares = [(Gio.File.new_for_uri(u), carpeta.get_child(Gio.File.new_for_uri(u).get_basename()))
                 for u in uris]
        return self.lanzar(f"Mover {contar(uris)} a «{carpeta.get_basename()}»",
                           lambda t: transferir(t, pares, True), al_terminar)

    def enlazar(self, uris, carpeta, al_terminar=None):
        pares = []
        for u in uris:
            f = Gio.File.new_for_uri(u)
            nombre = f.get_basename()
            if f.get_parent() and f.get_parent().equal(carpeta):
                nombre = archivos.nombre_libre(carpeta, nombre, "enlace")
            pares.append((f, carpeta.get_child(nombre)))
        return self.lanzar(f"Enlazar {contar(uris)} en «{carpeta.get_basename()}»",
                           lambda t: enlazar(t, pares), al_terminar)

    def duplicar(self, uris, al_terminar=None):
        pares = [(Gio.File.new_for_uri(u), Gio.File.new_for_uri(u)) for u in uris]
        return self.lanzar(f"Duplicar {contar(uris)}", lambda t: transferir(t, pares, False), al_terminar)

    def renombrar(self, pares, al_terminar=None):
        """pares: [(Gio.File, nombre nuevo)]."""
        pares = [(f, f.get_parent().get_child(n)) for f, n in pares]
        titulo = (f"Renombrar «{pares[0][0].get_basename()}»" if len(pares) == 1
                  else f"Renombrar {contar(pares)}")
        return self.lanzar(titulo, lambda t: renombrar(t, pares), al_terminar)

    def papelera(self, uris, al_terminar=None):
        lista = [Gio.File.new_for_uri(u) for u in uris]
        return self.lanzar(f"Mover {contar(uris)} a la papelera",
                           lambda t: a_la_papelera(t, lista), al_terminar)

    def borrar(self, uris, al_terminar=None):
        lista = [Gio.File.new_for_uri(u) for u in uris]
        return self.lanzar(f"Borrar {contar(uris)}", lambda t: borrar(t, lista), al_terminar, None)

    def restaurar(self, rutas_papelera, al_terminar=None):
        elementos = [archivos.elemento_de(r) for r in rutas_papelera]
        return self.lanzar(f"Restaurar {contar(rutas_papelera)}",
                           lambda t: restaurar(t, elementos), al_terminar)

    def vaciar_papelera(self, al_terminar=None):
        return self.lanzar("Vaciar la papelera", vaciar_papelera, al_terminar, None)

    def crear(self, clase, destino, plantilla=None, al_terminar=None):
        texto = "Crear carpeta" if clase == "carpeta" else "Crear documento"
        return self.lanzar(f"{texto} «{destino.get_basename()}»",
                           lambda t: crear(t, clase, destino, plantilla), al_terminar)

    # Deshacer y rehacer

    def texto_deshacer(self):
        return self.deshacer_pila[-1]["texto"] if self.deshacer_pila else None

    def texto_rehacer(self):
        return self.rehacer_pila[-1]["texto"] if self.rehacer_pila else None

    def deshacer(self, al_terminar=None):
        if not self.deshacer_pila or self.trabajos:
            return None
        r = self.deshacer_pila.pop()
        self._historial_cambiado()
        F = Gio.File.new_for_uri
        tipo = r["tipo"]
        if tipo == "copiar":
            funcion = lambda t: quitar_creados(t, [d for _, d in r["pares"]], papelera=True) or r  # noqa: E731
        elif tipo == "enlazar":
            funcion = lambda t: quitar_creados(t, [d for _, d in r["pares"]], papelera=False) or r  # noqa: E731
        elif tipo == "crear":
            funcion = lambda t: quitar_creados(t, [r["uri"]], papelera=False) or r  # noqa: E731
        elif tipo == "mover":
            pares = [(F(d), F(o)) for o, d in r["pares"]]
            funcion = lambda t: transferir(t, pares, True) and r  # noqa: E731
        elif tipo == "renombrar":
            pares = [(F(d), F(o)) for o, d in r["pares"]]
            funcion = lambda t: renombrar(t, pares) and r  # noqa: E731
        elif tipo == "papelera":
            def funcion(t):
                elementos = [archivos.buscar_en_papelera(ruta) for ruta in r["rutas"]]
                return restaurar(t, [e for e in elementos if e]) and r
        elif tipo == "restaurar":
            funcion = lambda t: a_la_papelera(t, [Gio.File.new_for_path(p) for p in r["rutas"]]) and r  # noqa: E731
        else:
            return None
        return self.lanzar(f"Deshacer: {r['texto']}", funcion, al_terminar, "deshacer", r["texto"])

    def rehacer(self, al_terminar=None):
        if not self.rehacer_pila or self.trabajos:
            return None
        r = self.rehacer_pila.pop()
        self._historial_cambiado()
        F = Gio.File.new_for_uri
        tipo = r["tipo"]
        if tipo in ("copiar", "mover"):
            pares = [(F(o), F(d)) for o, d in r["pares"]]
            funcion = lambda t: transferir(t, pares, tipo == "mover")  # noqa: E731
        elif tipo == "enlazar":
            pares = [(F(o), F(d)) for o, d in r["pares"]]
            funcion = lambda t: enlazar(t, pares)  # noqa: E731
        elif tipo == "renombrar":
            pares = [(F(o), F(d)) for o, d in r["pares"]]
            funcion = lambda t: renombrar(t, pares)  # noqa: E731
        elif tipo == "crear":
            funcion = lambda t: crear(t, r["clase"], F(r["uri"]), r["plantilla"])  # noqa: E731
        elif tipo == "papelera":
            funcion = lambda t: a_la_papelera(t, [Gio.File.new_for_path(p) for p in r["rutas"]])  # noqa: E731
        elif tipo == "restaurar":
            def funcion(t):
                elementos = [archivos.buscar_en_papelera(ruta) for ruta in r["rutas"]]
                return restaurar(t, [e for e in elementos if e])
        else:
            return None
        return self.lanzar(r["texto"], funcion, al_terminar, "rehacer")
