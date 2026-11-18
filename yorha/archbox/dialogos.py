"""Diálogos de ARCHBOX: nombre, conflicto, errores, confirmaciones, propiedades, renombrar
varios y «Abrir con otra aplicación».

Todos son asíncronos: se abren y llaman a al_responder(...) al cerrarse, sin bucles anidados.
Los diálogos que piden algo desde un trabajo (conflicto, error_trabajo, confirmar_sin_papelera)
reciben siempre (padre, ..., al_responder) para que Trabajo.preguntar los abra igual.
"""
import contextlib
import os
import re
import threading

from gi.repository import Gio, GLib, Gtk

import archivos

NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS


def dialogo(padre, titulo, botones, por_defecto=None):
    """Gtk.Dialog modal con botones [(texto, respuesta)] y una caja vertical para el contenido."""
    d = Gtk.Dialog(title=titulo, transient_for=padre, modal=True)
    d.get_style_context().add_class("archbox")
    d.set_resizable(False)
    for texto, respuesta in botones:
        b = d.add_button(texto, respuesta)
        if respuesta == por_defecto:
            b.get_style_context().add_class("suggested-action")
    if por_defecto is not None:
        d.set_default_response(por_defecto)
    caja = d.get_content_area()
    caja.set_spacing(10)
    caja.set_border_width(14)
    return d, caja


def etiqueta(texto, clase=None, selectable=False):
    e = Gtk.Label(label=texto, xalign=0, wrap=True, max_width_chars=60, selectable=selectable)
    if clase:
        e.get_style_context().add_class(clase)
    return e


def mensaje(padre, titulo, texto, al_responder=None):
    d, caja = dialogo(padre, titulo, [("Aceptar", Gtk.ResponseType.OK)], Gtk.ResponseType.OK)
    caja.pack_start(etiqueta(titulo, "titulo-dialogo"), False, False, 0)
    caja.pack_start(etiqueta(texto), False, False, 0)

    def respuesta(dlg, _r):
        dlg.destroy()
        if al_responder:
            al_responder(None)

    d.connect("response", respuesta)
    d.show_all()


def confirmar(padre, titulo, texto, boton, al_responder, peligro=True):
    """al_responder(True) si se pulsa boton."""
    d, caja = dialogo(padre, titulo, [("Cancelar", Gtk.ResponseType.CANCEL), (boton, Gtk.ResponseType.OK)],
                      Gtk.ResponseType.CANCEL if peligro else Gtk.ResponseType.OK)
    if peligro:
        d.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("destructive-action")
    caja.pack_start(etiqueta(titulo, "titulo-dialogo"), False, False, 0)
    caja.pack_start(etiqueta(texto), False, False, 0)

    def respuesta(dlg, r):
        dlg.destroy()
        al_responder(r == Gtk.ResponseType.OK)

    d.connect("response", respuesta)
    d.show_all()


def confirmar_sin_papelera(padre, n, al_responder):
    confirmar(padre, "Sin papelera",
              f"{'Un elemento está' if n == 1 else f'{n} elementos están'} en un sistema de archivos "
              "sin papelera. ¿Borrarlos definitivamente? No se podrá deshacer.",
              "Borrar definitivamente", al_responder)


def error_trabajo(padre, titulo, texto, con_saltar, al_responder):
    """al_responder((saltar, aplicar a todos)); sin con_saltar solo se informa."""
    botones = [("Cancelar", Gtk.ResponseType.CANCEL), ("Saltar", Gtk.ResponseType.ACCEPT)] if con_saltar \
        else [("Cerrar", Gtk.ResponseType.CLOSE)]
    d, caja = dialogo(padre, "Error", botones, botones[-1][1])
    caja.pack_start(etiqueta(titulo, "titulo-dialogo"), False, False, 0)
    caja.pack_start(etiqueta(texto), False, False, 0)
    todos = Gtk.CheckButton(label="Saltar todos los errores")
    if con_saltar:
        caja.pack_start(todos, False, False, 0)

    def respuesta(dlg, r):
        dlg.destroy()
        al_responder((r == Gtk.ResponseType.ACCEPT, todos.get_active()))

    d.connect("response", respuesta)
    d.show_all()


def pedir_nombre(padre, titulo, boton, inicial, al_responder, carpeta=None, carpeta_inicial=False):
    """Pide un nombre (selecciona el nombre sin la extensión, como Thunar); comprueba que no
    exista ya en carpeta. al_responder(nombre) solo si se acepta."""
    d, caja = dialogo(padre, titulo, [("Cancelar", Gtk.ResponseType.CANCEL), (boton, Gtk.ResponseType.OK)],
                      Gtk.ResponseType.OK)
    d.set_resizable(True)
    d.set_default_size(420, -1)
    caja.pack_start(etiqueta("Nombre:"), False, False, 0)
    entrada = Gtk.Entry(text=inicial, activates_default=True)
    aviso = etiqueta("", "aviso")
    caja.pack_start(entrada, False, False, 0)
    caja.pack_start(aviso, False, False, 0)
    aceptar = d.get_widget_for_response(Gtk.ResponseType.OK)

    def comprobar(_e=None):
        nombre = entrada.get_text()
        error = archivos.nombre_valido(nombre)
        if not error and carpeta and nombre != inicial \
                and archivos.ya_existe(carpeta, nombre, carpeta.get_child(inicial)):
            error = f"Ya existe «{nombre}»."
        aviso.set_text(error or "")
        aceptar.set_sensitive(not error)
        return error

    def respuesta(dlg, r):
        if r == Gtk.ResponseType.OK and comprobar():
            return
        nombre = entrada.get_text()
        dlg.destroy()
        if r == Gtk.ResponseType.OK:
            al_responder(nombre)

    entrada.connect("changed", comprobar)
    d.connect("response", respuesta)
    d.show_all()
    comprobar()
    base, _ext = archivos.partir_extension(inicial, carpeta_inicial)
    entrada.grab_focus()
    entrada.select_region(0, len(base))


def _resumen(archivo):
    """«Tamaño, modificado el …» de un archivo para el diálogo de conflicto."""
    try:
        info = archivo.query_info(archivos.ATRIBUTOS, NOFOLLOW, None)
    except GLib.Error:
        return None, ""
    tamano = "carpeta" if archivos.es_carpeta(info) else archivos.tamano_texto(info.get_size())
    return info, f"{tamano}, modificado: {archivos.fecha_texto(archivos.segundos(info))}"


def conflicto(padre, origen, destino, sugerido, al_responder):
    """al_responder((decision, nombre nuevo, aplicar a todos)); decision: reemplazar, saltar,
    renombrar o cancelar."""
    info_d, texto_d = _resumen(destino)
    info_o, texto_o = _resumen(origen)
    carpetas = bool(info_d and info_o and archivos.es_carpeta(info_d) and archivos.es_carpeta(info_o))
    d, caja = dialogo(padre, "Ya existe", [("Cancelar", 1), ("Saltar", 2), ("Renombrar", 3),
                                           ("Combinar" if carpetas else "Reemplazar", 4)], 2)
    nombre = destino.get_basename()
    titulo = (f"Ya hay una carpeta llamada «{nombre}» en «{destino.get_parent().get_basename()}»."
              if carpetas else f"Ya existe «{nombre}» en «{destino.get_parent().get_basename()}».")
    caja.pack_start(etiqueta(titulo, "titulo-dialogo"), False, False, 0)
    if carpetas:
        caja.pack_start(etiqueta("Al combinarlas, los archivos que coincidan se preguntarán uno a uno."),
                        False, False, 0)
    rejilla = Gtk.Grid(column_spacing=12, row_spacing=6)
    for fila, (texto, info, detalle) in enumerate((("Original", info_d, texto_d),
                                                   ("Nuevo", info_o, texto_o))):
        icono = Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.DIALOG) if info else Gtk.Image()
        rejilla.attach(icono, 0, fila, 1, 1)
        rejilla.attach(etiqueta(f"{texto}: {detalle}"), 1, fila, 1, 1)
    caja.pack_start(rejilla, False, False, 0)
    caja.pack_start(etiqueta("Nombre nuevo (para «Renombrar»):"), False, False, 0)
    entrada = Gtk.Entry(text=sugerido)
    caja.pack_start(entrada, False, False, 0)
    todos = Gtk.CheckButton(label="Aplicar a todos los conflictos")
    caja.pack_start(todos, False, False, 0)
    decisiones = {1: "cancelar", 2: "saltar", 3: "renombrar", 4: "reemplazar"}
    entrada.connect("changed", lambda e: d.set_response_sensitive(
        3, not archivos.nombre_valido(e.get_text())
        and not destino.get_parent().get_child(e.get_text()).query_exists(None)))

    def respuesta(dlg, r):
        texto = entrada.get_text()
        a_todos = todos.get_active()
        dlg.destroy()
        al_responder((decisiones.get(r, "cancelar"), texto, a_todos))

    d.connect("response", respuesta)
    d.show_all()


def abrir_con_otra(padre, archivo, info, al_elegir):
    """Gtk.AppChooserDialog con la casilla de Thunar «Usar como predeterminada»."""
    d = Gtk.AppChooserDialog.new(padre, Gtk.DialogFlags.MODAL, archivo)
    d.get_style_context().add_class("archbox")
    d.set_title("Abrir con")
    tipo = info.get_content_type() or "application/octet-stream"
    descripcion = Gio.content_type_get_description(tipo)
    casilla = Gtk.CheckButton(label=f"Usar como predeterminada para «{descripcion}»")
    casilla.set_margin_start(10)
    d.get_content_area().pack_end(casilla, False, False, 6)
    casilla.show()

    def respuesta(dlg, r):
        app = dlg.get_app_info() if r == Gtk.ResponseType.OK else None
        if app and casilla.get_active():
            with contextlib.suppress(GLib.Error):
                app.set_as_default_for_type(tipo)
        dlg.destroy()
        if app:
            al_elegir(app)

    d.connect("response", respuesta)
    d.show()


# --- Renombrar varios --------------------------------------------------------------

MODOS = ("Buscar y reemplazar", "Numerar", "Mayúsculas y minúsculas")
POSICIONES = ("Delante del nombre", "Detrás del nombre", "En lugar del nombre")
CAJAS = ("minúsculas", "MAYÚSCULAS", "Primera Letra En Mayúscula")


def nombre_nuevo(nombre, es_dir, i, ajustes):
    """El nombre de la fila i según el modo; la extensión se respeta salvo en buscar/reemplazar
    con «incluir la extensión»."""
    base, ext = archivos.partir_extension(nombre, es_dir)
    modo = ajustes["modo"]
    if modo == 0:
        buscar, poner = ajustes["buscar"], ajustes["poner"]
        if not buscar:
            return nombre
        texto = nombre if ajustes["con_ext"] else base
        bandera = 0 if ajustes["mayus"] else re.IGNORECASE
        texto = re.sub(re.escape(buscar), lambda _m: poner, texto, flags=bandera)
        return texto if ajustes["con_ext"] else texto + ext
    if modo == 1:
        numero = str(ajustes["inicio"] + i).zfill(ajustes["cifras"])
        texto = ajustes["texto"]
        posicion = ajustes["posicion"]
        if posicion == 0:
            return f"{texto}{numero}{base}{ext}"
        if posicion == 1:
            return f"{base}{texto}{numero}{ext}"
        return f"{texto}{numero}{ext}"
    caja = ajustes["caja"]
    base = base.lower() if caja == 0 else base.upper() if caja == 1 else base.title()
    return base + ext


class RenombrarVarios(Gtk.Dialog):
    """Renombrado en bloque con vista previa (como thunar --bulk-rename, lo básico)."""

    def __init__(self, padre, elementos, al_aplicar):
        super().__init__(title="Renombrar varios", transient_for=padre, modal=True)
        self.get_style_context().add_class("archbox")
        self.elementos = elementos          # [(Gio.File, Gio.FileInfo)]
        self.al_aplicar = al_aplicar        # al_aplicar([(Gio.File, nombre nuevo)])
        self.set_default_size(640, 520)
        self.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        self.aplicar = self.add_button("Renombrar", Gtk.ResponseType.OK)
        self.aplicar.get_style_context().add_class("suggested-action")
        caja = self.get_content_area()
        caja.set_spacing(10)
        caja.set_border_width(14)

        self.modo = Gtk.ComboBoxText()
        for m in MODOS:
            self.modo.append_text(m)
        self.modo.set_active(0)
        caja.pack_start(self.modo, False, False, 0)
        self.paginas = Gtk.Stack()
        caja.pack_start(self.paginas, False, False, 0)
        # Una página por modo, con el número del modo como nombre
        self.paginas.add_named(self._pagina_reemplazar(), "0")
        self.paginas.add_named(self._pagina_numerar(), "1")
        self.paginas.add_named(self._pagina_caja(), "2")
        self._crear_vista_previa(caja)
        self._conectar_cambios()
        self.connect("response", self._respuesta)
        self.show_all()
        self.actualizar()

    def _pagina_reemplazar(self):
        """Página de «Buscar y reemplazar»."""
        rejilla = self._rejilla()
        self.buscar, self.poner = Gtk.Entry(), Gtk.Entry()
        self.mayus = Gtk.CheckButton(label="Distinguir mayúsculas")
        self.con_ext = Gtk.CheckButton(label="Incluir la extensión")
        self._filas(rejilla, (("Buscar:", self.buscar), ("Reemplazar con:", self.poner),
                              ("", self.mayus), ("", self.con_ext)))
        return rejilla

    def _pagina_numerar(self):
        """Página de «Numerar»."""
        rejilla = self._rejilla()
        self.texto = Gtk.Entry()
        self.inicio = Gtk.SpinButton.new_with_range(0, 999999, 1)
        self.inicio.set_value(1)
        self.cifras = Gtk.SpinButton.new_with_range(1, 9, 1)
        self.cifras.set_value(2)
        self.posicion = Gtk.ComboBoxText()
        for p in POSICIONES:
            self.posicion.append_text(p)
        self.posicion.set_active(2)
        self._filas(rejilla, (("Texto:", self.texto), ("Empezar en:", self.inicio),
                              ("Cifras:", self.cifras), ("Número:", self.posicion)))
        return rejilla

    def _pagina_caja(self):
        """Página de «Mayúsculas y minúsculas»."""
        rejilla = self._rejilla()
        self.caja = Gtk.ComboBoxText()
        for c in CAJAS:
            self.caja.append_text(c)
        self.caja.set_active(0)
        self._filas(rejilla, (("Pasar a:", self.caja),))
        return rejilla

    def _crear_vista_previa(self, caja):
        """Lista de nombres actuales y nuevos (en rojo los que no valen) y el aviso debajo."""
        self.modelo = Gtk.ListStore(str, str, str)  # antes, después, color del aviso
        lista = Gtk.TreeView(model=self.modelo)
        for i, titulo in enumerate(("Nombre actual", "Nombre nuevo")):
            celda = Gtk.CellRendererText(ellipsize=3)
            col = Gtk.TreeViewColumn(titulo, celda, text=i)
            if i == 1:
                col.add_attribute(celda, "foreground", 2)
            col.set_expand(True)
            lista.append_column(col)
        desliza = Gtk.ScrolledWindow(vexpand=True)
        desliza.add(lista)
        caja.pack_start(desliza, True, True, 0)
        self.aviso = etiqueta("", "aviso")
        caja.pack_start(self.aviso, False, False, 0)

    def _conectar_cambios(self):
        """Cualquier cambio en los ajustes vuelve a calcular la vista previa."""
        for w in (self.buscar, self.poner, self.texto):
            w.connect("changed", self.actualizar)
        for w in (self.mayus, self.con_ext):
            w.connect("toggled", self.actualizar)
        for w in (self.inicio, self.cifras):
            w.connect("value-changed", self.actualizar)
        for w in (self.modo, self.posicion, self.caja):
            w.connect("changed", self.actualizar)

    @staticmethod
    def _rejilla():
        return Gtk.Grid(column_spacing=10, row_spacing=6)

    @staticmethod
    def _filas(rejilla, filas):
        for i, (texto, w) in enumerate(filas):
            rejilla.attach(etiqueta(texto), 0, i, 1, 1)
            w.set_hexpand(True)
            rejilla.attach(w, 1, i, 1, 1)

    def ajustes(self):
        return {"modo": self.modo.get_active(), "buscar": self.buscar.get_text(),
                "poner": self.poner.get_text(), "mayus": self.mayus.get_active(),
                "con_ext": self.con_ext.get_active(), "texto": self.texto.get_text(),
                "inicio": self.inicio.get_value_as_int(), "cifras": self.cifras.get_value_as_int(),
                "posicion": self.posicion.get_active(), "caja": self.caja.get_active()}

    def nombres(self):
        a = self.ajustes()
        return [(f, nombre_nuevo(i.get_display_name(), archivos.es_carpeta(i), n, a))
                for n, (f, i) in enumerate(self.elementos)]

    def actualizar(self, *_):
        self.paginas.set_visible_child_name(str(self.modo.get_active()))
        self.modelo.clear()
        nuevos = self.nombres()
        vistos, error = {}, None
        for _f, nombre in nuevos:
            vistos[nombre] = vistos.get(nombre, 0) + 1
        propios = {f.get_basename() for f, _ in nuevos}
        for (f, i), (_f, nombre) in zip(self.elementos, nuevos, strict=True):
            problema = archivos.nombre_valido(nombre)
            if not problema and vistos[nombre] > 1:
                problema = "Hay nombres repetidos."
            elif not problema and nombre != f.get_basename() and nombre not in propios \
                    and archivos.ya_existe(f.get_parent(), nombre, f):
                problema = f"Ya existe «{nombre}»."
            error = error or problema
            self.modelo.append([i.get_display_name(), nombre, "#cd664d" if problema else None])
        cambios = any(n != f.get_basename() for f, n in nuevos)
        self.aviso.set_text(error or "")
        self.aplicar.set_sensitive(cambios and not error)

    def _respuesta(self, _d, r):
        pares = [(f, n) for f, n in self.nombres() if n != f.get_basename()]
        self.destroy()
        if r == Gtk.ResponseType.OK and pares:
            self.al_aplicar(pares)


# --- Propiedades ---------------------------------------------------------------------

PERMISOS = (("Propietario", 6), ("Grupo", 3), ("Otros", 0))


def _sumar_tamanos(elementos, cuenta, cancelable):
    """Suma en cuenta bytes, archivos y carpetas de elementos (recursivo, sin seguir enlaces);
    corre en un hilo, así que no toca GTK, y al acabar pone cuenta["fin"]."""
    pendientes = [f for f, _ in elementos]
    while pendientes and not cancelable.is_cancelled():
        f = pendientes.pop()
        try:
            info = f.query_info("standard::type,standard::size,standard::name", NOFOLLOW, cancelable)
        except GLib.Error:
            continue
        if info.get_file_type() == Gio.FileType.DIRECTORY:
            cuenta["carpetas"] += 1
            try:
                for hijo in f.enumerate_children("standard::name", NOFOLLOW, cancelable):
                    pendientes.append(f.get_child(hijo.get_name()))
            except GLib.Error:
                pass
        else:
            cuenta["archivos"] += 1
            cuenta["bytes"] += info.get_size()
    cuenta["fin"] = True


class Propiedades(Gtk.Dialog):
    """Propiedades de uno o varios archivos: General y Permisos, como en Thunar (sin emblemas).

    renombrar(archivo, nombre) hace el cambio (deshacible) por las operaciones de la ventana.
    """

    def __init__(self, padre, elementos, renombrar):
        super().__init__(title="Propiedades", transient_for=padre)
        self.get_style_context().add_class("archbox")
        self.get_style_context().add_class("propiedades")
        self.elementos = list(elementos)
        self.renombrar = renombrar
        self.cancelable = Gio.Cancellable()
        self.connect("destroy", lambda *_: self.cancelable.cancel())
        self.add_button("Cerrar", Gtk.ResponseType.CLOSE)
        self.connect("response", self._cerrar)
        self.set_default_size(440, -1)
        archivo, info = elementos[0]
        self.uno = len(elementos) == 1
        if self.uno:
            self.set_title(f"Propiedades de «{info.get_display_name()}»")
        libro = Gtk.Notebook()
        self.get_content_area().pack_start(libro, True, True, 0)
        libro.append_page(self._general(), Gtk.Label(label="General"))
        if self.uno:
            libro.append_page(self._permisos(archivo, info), Gtk.Label(label="Permisos"))
        self.show_all()

    def _rejilla(self):
        r = Gtk.Grid(column_spacing=12, row_spacing=8, margin=14)
        r.fila = 0
        return r

    def _fila(self, rejilla, titulo, w):
        t = etiqueta(titulo, "clave")
        t.set_xalign(1)
        t.set_valign(Gtk.Align.START)
        rejilla.attach(t, 0, rejilla.fila, 1, 1)
        if isinstance(w, str):
            w = etiqueta(w, selectable=True)
        w.set_hexpand(True)
        rejilla.attach(w, 1, rejilla.fila, 1, 1)
        rejilla.fila += 1
        return w

    def _general(self):
        r = self._rejilla()
        archivo, info = self.elementos[0]
        icono = Gtk.Image.new_from_gicon(info.get_icon(), Gtk.IconSize.DIALOG) if self.uno \
            else Gtk.Image.new_from_icon_name("edit-select-all", Gtk.IconSize.DIALOG)
        icono.set_pixel_size(48)
        r.attach(icono, 0, 0, 1, 1)
        if self.uno and info.get_attribute_boolean("access::can-rename"):
            self.nombre = Gtk.Entry(text=info.get_display_name())
            self.nombre.connect("activate", lambda _e: self._renombrar())
            r.attach(self.nombre, 1, 0, 1, 1)
        else:
            self.nombre = None
            texto = info.get_display_name() if self.uno else f"{len(self.elementos)} elementos seleccionados"
            r.attach(etiqueta(texto, "titulo-dialogo", selectable=True), 1, 0, 1, 1)
        r.fila = 1
        if self.uno:
            self._fila(r, "Tipo:", archivos.tipo_texto(info))
            tipo = info.get_content_type()
            if tipo and not archivos.es_carpeta(info) and not info.get_is_symlink():
                boton = Gtk.AppChooserButton(content_type=tipo)
                boton.set_show_dialog_item(True)
                boton.connect("changed", lambda b: self._predeterminada(b, tipo))
                self._fila(r, "Abrir con:", boton)
            if info.get_is_symlink():
                self._fila(r, "Enlace a:", info.get_symlink_target() or "")
        padre = archivo.get_parent()
        self._fila(r, "Ubicación:", archivos.ruta_bonita(padre) if padre else "/")
        self.tamano = self._fila(r, "Tamaño:", "Calculando…")
        self._calcular_tamano()
        if self.uno:
            for titulo, atributo in (("Creado:", "time::created"), ("Modificado:", "time::modified"),
                                     ("Accedido:", "time::access")):
                s = archivos.segundos(info, atributo)
                if s:
                    self._fila(r, titulo, GLib.DateTime.new_from_unix_local(s).format("%d/%m/%Y, %H:%M:%S"))
        self.libre = self._fila(r, "Espacio libre:", "")
        archivo.query_filesystem_info_async("filesystem::free,filesystem::size", GLib.PRIORITY_DEFAULT,
                                            self.cancelable, self._espacio)
        return r

    def _espacio(self, archivo, resultado):
        try:
            info = archivo.query_filesystem_info_finish(resultado)
        except GLib.Error:
            return
        libre = info.get_attribute_uint64("filesystem::free")
        total = info.get_attribute_uint64("filesystem::size")
        self.libre.set_text(f"{GLib.format_size(libre)} de {GLib.format_size(total)}" if total else "")

    def _calcular_tamano(self):
        """Suma en un hilo (las carpetas pueden ser enormes) y enseña el total al ir sumando."""
        cuenta = {"bytes": 0, "archivos": 0, "carpetas": 0, "fin": False}
        cancelable = self.cancelable

        def enseñar():
            if cancelable.is_cancelled():
                return False
            b = cuenta["bytes"]
            texto = GLib.format_size(b) + (f" ({b:,} bytes)".replace(",", ".") if b >= 1000 else "")
            carpetas = cuenta["carpetas"] - sum(1 for _f, i in self.elementos if archivos.es_carpeta(i))
            if any(archivos.es_carpeta(i) for _f, i in self.elementos) or not self.uno:
                texto += f", {cuenta['archivos']} archivos"
                if carpetas > 0:
                    texto += f" y {carpetas} carpetas"
            self.tamano.set_text(texto + ("" if cuenta["fin"] else " — calculando…"))
            return not cuenta["fin"]

        threading.Thread(target=_sumar_tamanos, args=(self.elementos, cuenta, cancelable),
                         daemon=True).start()
        GLib.timeout_add(200, enseñar)

    @staticmethod
    def _predeterminada(boton, tipo):
        app = boton.get_app_info()
        if app:
            with contextlib.suppress(GLib.Error):
                app.set_as_default_for_type(tipo)

    def _permisos(self, archivo, info):
        r = self._rejilla()
        self._fila(r, "Propietario:", info.get_attribute_string("owner::user")
                   or str(info.get_attribute_uint32("unix::uid")))
        self._fila(r, "Grupo:", info.get_attribute_string("owner::group")
                   or str(info.get_attribute_uint32("unix::gid")))
        modo = info.get_attribute_uint32("unix::mode") if info.has_attribute("unix::mode") else None
        propio = info.get_attribute_uint32("unix::uid") == archivos.UID and modo is not None \
            and not info.get_is_symlink()
        es_dir = archivos.es_carpeta(info)
        tabla = Gtk.Grid(column_spacing=14, row_spacing=6)
        for c, texto in enumerate(("Leer", "Escribir", "Acceder" if es_dir else "Ejecutar")):
            tabla.attach(etiqueta(texto, "clave"), c + 1, 0, 1, 1)
        self.casillas = []
        for f, (quien, desplazamiento) in enumerate(PERMISOS):
            tabla.attach(etiqueta(quien), 0, f + 1, 1, 1)
            for c, bit in enumerate((4, 2, 1)):
                mascara = bit << desplazamiento
                casilla = Gtk.CheckButton()
                casilla.set_active(bool(modo and modo & mascara))
                casilla.set_sensitive(propio)
                casilla.connect("toggled", lambda _c: self._poner_permisos(archivo, modo))
                casilla.mascara = mascara
                self.casillas.append(casilla)
                tabla.attach(casilla, c + 1, f + 1, 1, 1)
        self._fila(r, "Acceso:", tabla)
        if modo is not None:
            self.octal = self._fila(r, "Modo:", f"{modo & 0o7777:04o}")
        if not propio:
            self._fila(r, "", "Solo el propietario puede cambiar los permisos.")
        return r

    def _poner_permisos(self, archivo, modo):
        nuevo = modo & ~0o777
        for casilla in self.casillas:
            if casilla.get_active():
                nuevo |= casilla.mascara
        try:
            archivo.set_attribute_uint32("unix::mode", nuevo, NOFOLLOW, None)
            self.octal.set_text(f"{nuevo & 0o7777:04o}")
        except GLib.Error as e:
            mensaje(self, "No se pudieron cambiar los permisos", e.message)

    def _renombrar(self):
        if not self.nombre:
            return
        archivo, info = self.elementos[0]
        # Tras renombrar (Intro), el nombre vigente es el nuevo: cerrar no debe renombrar otra vez
        actual = getattr(self, "nombre_vigente", None) or info.get_display_name()
        nombre = self.nombre.get_text()
        if nombre == actual:
            return
        error = archivos.nombre_valido(nombre)
        if error:
            mensaje(self, "No se puede renombrar", error)
            self.nombre.set_text(actual)
            return
        self.renombrar(archivo, nombre)
        self.nombre_vigente = nombre
        self.elementos[0] = (archivo.get_parent().get_child_for_display_name(nombre), info)
        self.set_title(f"Propiedades de «{nombre}»")

    def _cerrar(self, *_):
        self._renombrar()
        self.destroy()


def es_escritorio_de_confianza(archivo, info):
    """Un .desktop solo se ejecuta si es ejecutable, del usuario y no lo puede tocar nadie más."""
    ruta = archivo.get_path()
    if not ruta or info.get_content_type() != "application/x-desktop":
        return False
    try:
        st = os.stat(ruta)
    except OSError:
        return False
    return st.st_uid == archivos.UID and st.st_mode & 0o100 and not st.st_mode & 0o022
