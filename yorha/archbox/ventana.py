"""Ventana de ARCHBOX: barra de herramientas, barra de ruta, panel lateral, pestañas, vista
dividida, búsqueda y barra de estado (las órdenes, los menús y los atajos están en acciones.py).

Cada pestaña es un Panel: su historial, su contenido (vista.Contenido) y su vista. La barra de
ruta, la de estado y las acciones siguen siempre al panel activo.
"""

from gi.repository import Gdk, Gio, GLib, Gtk

import archivos
import lugares
import vista
from acciones import Acciones
from vista import C_ARCHIVO, C_ES_DIR, C_INFO, C_NOMBRE, C_TAMANO, C_URI

TECLEO_MS = 1200


def icono_boton(nombre, accion, ayuda, objetivo=None):
    b = Gtk.Button.new_from_icon_name(nombre, Gtk.IconSize.LARGE_TOOLBAR)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.set_action_name(accion)
    if objetivo is not None:
        b.set_action_target_value(objetivo)
    b.set_tooltip_text(ayuda)
    b.set_can_focus(False)
    return b


# --- Barra de ruta -------------------------------------------------------------

class BarraRuta(Gtk.Stack):
    """Botones de la ruta (se conservan los de las subcarpetas al subir, como en Thunar) y,
    con Ctrl+L, una entrada con autocompletado de rutas."""

    def __init__(self, ventana):
        super().__init__(transition_type=Gtk.StackTransitionType.NONE, hexpand=True)
        self.ventana = ventana
        self.get_style_context().add_class("ruta")
        self.cadena, self.actual = [], None
        self.botones = Gtk.Box()
        self.botones.get_style_context().add_class("linked")
        self.desliza = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                          vscrollbar_policy=Gtk.PolicyType.NEVER)
        self.desliza.add(self.botones)
        self.desliza.get_hadjustment().connect("changed", lambda a: a.set_value(a.get_upper()))
        self.entrada = Gtk.Entry()
        self.entrada.get_style_context().add_class("ubicacion")
        self.completar = Gtk.ListStore(str)
        completado = Gtk.EntryCompletion(model=self.completar, text_column=0, inline_completion=True,
                                         popup_single_match=False, minimum_key_length=1)
        self.entrada.set_completion(completado)
        self.listada = None
        self.entrada.connect("changed", self._cambio)
        self.entrada.connect("activate", self._activar)
        self.entrada.connect("key-press-event", self._tecla)
        self.entrada.connect("focus-out-event", lambda *_: GLib.idle_add(self._salir_si_no_foco) and False)
        self.add_named(self.desliza, "botones")
        self.add_named(self.entrada, "entrada")

    def poner(self, destino):
        self.actual = destino
        if destino == "papelera":
            cadena = ["papelera"]
        else:
            cadena = []
            f = destino
            casa = Gio.File.new_for_path(GLib.get_home_dir())
            while f is not None:
                cadena.insert(0, f)
                if f.equal(casa):
                    break
                f = f.get_parent()
        guardada = [c for c in self.cadena]
        if not (isinstance(destino, Gio.File)
                and any(isinstance(c, Gio.File) and c.equal(destino) for c in guardada)
                and all(isinstance(a, Gio.File) and a.equal(b)
                        for a, b in zip(guardada, cadena, strict=False))):
            self.cadena = cadena
            for b in self.botones.get_children():
                b.destroy()
            for f in cadena:
                self.botones.pack_start(self._boton(f), False, False, 0)
            self.botones.show_all()
        for b, f in zip(self.botones.get_children(), self.cadena, strict=False):
            if isinstance(f, Gio.File):
                actual = isinstance(destino, Gio.File) and f.equal(destino)
            else:
                actual = f == destino
            (b.get_style_context().add_class if actual else b.get_style_context().remove_class)("actual")
        if self.get_visible_child_name() == "entrada":
            self._texto_actual()

    def _boton(self, f):
        caja = Gtk.Box(spacing=4)
        if f == "papelera":
            icono, texto = "user-trash", "Papelera"
        elif f.get_path() == GLib.get_home_dir():
            icono, texto = "user-home", f.get_basename()
        elif f.get_parent() is None:
            icono, texto = "drive-harddisk", ""
        else:
            icono, texto = None, f.get_basename()
        if icono:
            caja.pack_start(Gtk.Image.new_from_icon_name(icono, Gtk.IconSize.MENU), False, False, 0)
        if texto:
            caja.pack_start(Gtk.Label(label=texto), False, False, 0)
        boton = Gtk.Button()
        boton.add(caja)
        boton.set_can_focus(False)
        boton.connect("clicked", lambda _b: self.ventana.ir_a(f))
        boton.connect("button-press-event",
                      lambda _b, e: e.button == Gdk.BUTTON_MIDDLE
                      and (self.ventana.ir_a(f, "pestana") or True))
        return boton

    # Entrada de texto

    def editar(self, texto=None):
        self.set_visible_child_name("entrada")
        if texto is None:
            self._texto_actual()
            self.entrada.grab_focus()
        else:
            self.entrada.set_text(texto)
            self.entrada.grab_focus_without_selecting()
            self.entrada.set_position(-1)

    def _texto_actual(self):
        if isinstance(self.actual, Gio.File):
            ruta = self.actual.get_path()
            texto = ruta if ruta else self.actual.get_uri()
            self.entrada.set_text(texto if texto.endswith("/") else texto + "/")
        else:
            self.entrada.set_text("")

    def salir(self):
        self.entrada.get_style_context().remove_class("error")
        self.set_visible_child_name("botones")

    def _salir_si_no_foco(self):
        if not self.entrada.has_focus():
            self.salir()
        return False

    def _tecla(self, _e, evento):
        if evento.keyval == Gdk.KEY_Escape:
            self.salir()
            self.ventana.enfocar_vista()
            return True
        return False

    def _cambio(self, entrada):
        """Llena el autocompletado con el contenido de la carpeta que se está escribiendo."""
        entrada.get_style_context().remove_class("error")
        texto = entrada.get_text()
        if "/" not in texto:
            return
        prefijo = texto[:texto.rfind("/") + 1]
        if prefijo == self.listada:
            return
        self.listada = prefijo
        carpeta = archivos.desde_texto(prefijo or "/")

        def leido(c, resultado):
            try:
                enumerador = c.enumerate_children_finish(resultado)
            except GLib.Error:
                return
            enumerador.next_files_async(1000, GLib.PRIORITY_DEFAULT, None, tanda)

        def tanda(enumerador, resultado):
            try:
                infos = enumerador.next_files_finish(resultado)
            except GLib.Error:
                infos = []
            if self.listada != prefijo:
                return
            self.completar.clear()
            for info in sorted(infos, key=lambda i: (i.get_file_type() != Gio.FileType.DIRECTORY,
                                                    i.get_display_name().casefold())):
                barra = "/" if info.get_file_type() == Gio.FileType.DIRECTORY else ""
                self.completar.append([prefijo + info.get_display_name() + barra])
            enumerador.close_async(GLib.PRIORITY_DEFAULT, None, None)
            entrada.get_completion().complete()

        carpeta.enumerate_children_async("standard::display-name,standard::type", Gio.FileQueryInfoFlags.NONE,
                                         GLib.PRIORITY_DEFAULT, None, leido)

    def _activar(self, entrada):
        base = self.actual if isinstance(self.actual, Gio.File) else None
        destino = archivos.desde_texto(entrada.get_text(), base)

        def leido(f, resultado):
            try:
                info = f.query_info_finish(resultado)
            except GLib.Error:
                entrada.get_style_context().add_class("error")
                return
            self.salir()
            if archivos.es_carpeta(info):
                self.ventana.ir_a(f)
            else:
                self.ventana.ir_a(f.get_parent(), seleccionar=[f.get_uri()])
                self.ventana.abrir_archivos([(f, info)])
            self.ventana.enfocar_vista()

        destino.query_info_async(archivos.ATRIBUTOS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT,
                                 None, leido)


# --- Panel (una pestaña) -------------------------------------------------------

class Panel(Gtk.Box):
    """Una pestaña: historial, contenido, búsqueda y la vista."""

    def __init__(self, ventana, destino):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.ventana, self.app = ventana, ventana.app
        estado = self.app.estado
        self.tipo = estado["vista"]
        self.ocultos = estado["ocultos"]
        self.contenido = vista.Contenido(self.app.iconos, self.app.miniaturas)
        self.contenido.orden = (estado["orden"], estado["descendente"])
        self.contenido.ocultos = self.ocultos
        self.modelo = self.contenido.store
        self.ubicacion = None
        self.atras, self.adelante = [], []
        self.tecleo, self.reloj_tecleo = "", 0
        self.por_seleccionar, self.reloj_seleccion = set(), 0
        self.libre = ""
        self.vista = None
        self.reloj_busqueda = 0

        self._crear_busqueda()
        self._crear_paginas()
        self._crear_etiqueta()

        self.contenido.connect("cargado", self._cargado)
        self.contenido.connect("error", self._error)
        self.contenido.connect("perdido", self._perdido)
        self.contenido.connect("cambiado", lambda _c: self._cambio())
        self.contenido.connect("nueva", lambda _c, uri: self._nueva(uri))
        # El IconView no siempre se repinta al cambiar una fila (p. ej. al llegar una miniatura)
        self.repintar = 0
        self.contenido.store.connect("row-changed", lambda *_: self._pedir_repintado())
        self.poner_vista(self.tipo)
        self.show_all()
        self.ir_a(destino)

    def _crear_busqueda(self):
        """Barra de búsqueda (Ctrl+F): filtra la carpeta o busca también en las subcarpetas."""
        self.busqueda = Gtk.Revealer()
        barra = Gtk.Box(spacing=8)
        barra.get_style_context().add_class("busqueda")
        self.entrada_busqueda = Gtk.SearchEntry(placeholder_text="Buscar en esta carpeta")
        self.entrada_busqueda.connect("search-changed", lambda _e: self._pedir_busqueda())
        self.entrada_busqueda.connect("stop-search", lambda _e: self.cerrar_busqueda())
        self.entrada_busqueda.connect("activate", lambda _e: self.enfocar_vista(primera=True))
        self.subcarpetas = Gtk.CheckButton(label="Buscar en subcarpetas", active=True)
        self.subcarpetas.connect("toggled", lambda _c: self._pedir_busqueda())
        cerrar = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        cerrar.set_relief(Gtk.ReliefStyle.NONE)
        cerrar.connect("clicked", lambda _b: self.cerrar_busqueda())
        barra.pack_start(self.entrada_busqueda, True, True, 0)
        barra.pack_start(self.subcarpetas, False, False, 0)
        barra.pack_start(cerrar, False, False, 0)
        self.busqueda.add(barra)
        self.pack_start(self.busqueda, False, False, 0)

    def _crear_paginas(self):
        """La vista (dentro de su ScrolledWindow) y, en su lugar, el aviso cuando no se puede abrir."""
        self.paginas = Gtk.Stack()
        self.desliza = Gtk.ScrolledWindow()
        self.desliza.connect("scroll-event", self._rueda)
        self.paginas.add_named(self.desliza, "vista")
        self.aviso = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.aviso.get_style_context().add_class("aviso-vista")
        self.paginas.add_named(self.aviso, "aviso")
        self.pack_start(self.paginas, True, True, 0)

    def _crear_etiqueta(self):
        """Lo que se ve en la pestaña: icono, nombre y cerrar."""
        self.etiqueta = Gtk.Box(spacing=6)
        self.icono_pestana = Gtk.Image()
        self.nombre_pestana = Gtk.Label(ellipsize=3, width_chars=10, max_width_chars=22)
        x = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        x.set_relief(Gtk.ReliefStyle.NONE)
        x.set_can_focus(False)
        x.get_style_context().add_class("cerrar-pestana")
        x.connect("clicked", lambda _b: self.ventana.cerrar_panel(self))
        for w in (self.icono_pestana, self.nombre_pestana, x):
            self.etiqueta.pack_start(w, False, False, 0)
        self.etiqueta.show_all()

    # Navegación

    def ir_a(self, destino, historial=True, seleccionar=None):
        if historial and self.ubicacion is not None and not self._igual(destino, self.ubicacion):
            self.atras.append(self.ubicacion)
            self.adelante.clear()
        self._cargar(destino, seleccionar)

    @staticmethod
    def _igual(a, b):
        if isinstance(a, Gio.File) and isinstance(b, Gio.File):
            return a.equal(b)
        return a == b

    def _cargar(self, destino, seleccionar=None):
        anterior = self.ubicacion
        self.ubicacion = destino
        self.tecleo = ""
        if self.busqueda.get_reveal_child():
            self.busqueda.set_reveal_child(False)
            self.entrada_busqueda.set_text("")
        self.contenido.texto = ""
        self.por_seleccionar = set(seleccionar or [])
        # Al subir se selecciona la carpeta de la que se viene, como en Thunar
        if not seleccionar and isinstance(anterior, Gio.File) and isinstance(destino, Gio.File) \
                and anterior.has_parent(destino):
            self.por_seleccionar = {anterior.get_uri()}
        self.paginas.set_visible_child_name("vista")
        # Sin modelo mientras se lee: se pone entero al final (con miles de archivos es mucho más rápido)
        self.vista.w.set_model(None)
        self.escribible = False
        if destino == "papelera":
            self.contenido.cargar_papelera()
            self.libre = ""
        else:
            self.contenido.cargar(destino)
            self._espacio_libre(destino)
        self.vista.modo(self.contenido.modo)
        self._pestana()
        self.ventana.panel_cambiado(self)

    def _espacio_libre(self, destino):
        """Espacio libre (barra de estado) y si se puede escribir (pegar, crear...), sin bloquear."""
        def libre(f, resultado):
            try:
                info = f.query_filesystem_info_finish(resultado)
            except GLib.Error:
                return
            if f is self.ubicacion and info.has_attribute("filesystem::free"):
                self.libre = GLib.format_size(info.get_attribute_uint64("filesystem::free"))
                self.ventana.actualizar_estado(self)

        def permisos(f, resultado):
            try:
                info = f.query_info_finish(resultado)
            except GLib.Error:
                return
            if f is self.ubicacion:
                self.escribible = info.get_attribute_boolean("access::can-write")
                self.ventana.actualizar_estado(self)

        destino.query_filesystem_info_async("filesystem::free", GLib.PRIORITY_LOW, None, libre)
        destino.query_info_async("access::can-write", Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT,
                                 None, permisos)

    def volver(self):
        if self.atras:
            self.adelante.append(self.ubicacion)
            self._cargar(self.atras.pop())

    def avanzar(self):
        if self.adelante:
            self.atras.append(self.ubicacion)
            self._cargar(self.adelante.pop())

    def recargar(self):
        rutas = self.uris_seleccion()
        if self.contenido.modo == "busqueda":
            self._buscar()
        else:
            self._cargar(self.ubicacion, rutas)

    def titulo(self):
        if self.ubicacion == "papelera":
            return "Papelera"
        if self.ubicacion.get_path() == "/":
            return "Sistema de archivos"
        return self.ubicacion.get_basename() or self.ubicacion.get_parse_name()

    def _pestana(self):
        self.nombre_pestana.set_text(self.titulo())
        icono = "user-trash" if self.ubicacion == "papelera" else \
            "user-home" if self.ubicacion.get_path() == GLib.get_home_dir() else "folder"
        self.icono_pestana.set_from_icon_name(icono, Gtk.IconSize.MENU)
        self.etiqueta.set_tooltip_text(archivos.ruta_bonita(self.ubicacion)
                                       if isinstance(self.ubicacion, Gio.File) else "Papelera")

    def _cargado(self, _c):
        if self.vista.w.get_model() is None:
            self.vista.w.set_model(self.modelo)
        self._seleccionar_pendientes()
        if not self.vista.seleccion() and len(self.modelo):
            self.vista.poner_cursor(Gtk.TreePath.new_first(), seleccionar=False)
        self.ventana.actualizar_estado(self)

    def _error(self, _c, mensaje):
        self.aviso.set_text(f"No se puede abrir «{self.titulo()}».\n\n{mensaje}")
        self.paginas.set_visible_child_name("aviso")
        self.ventana.actualizar_estado(self)

    def _perdido(self, _c):
        """La carpeta abierta ya no existe: se sube hasta la primera que siga existiendo."""
        f = self.ubicacion
        while f.get_parent() is not None and not f.query_exists(None):
            f = f.get_parent()
        self._cargar(f)

    def _cambio(self):
        self._seleccionar_pendientes()
        self.ventana.actualizar_estado(self)

    def _pedir_repintado(self):
        if not self.repintar:
            self.repintar = GLib.timeout_add(100, self._repintado)

    def _repintado(self):
        self.repintar = 0
        self.vista.w.queue_draw()
        return False

    def _nueva(self, uri):
        if uri in self.por_seleccionar:
            self._seleccionar_pendientes()

    # Selección

    def seleccionar_uris(self, uris):
        """Selecciona esas URI ahora o cuando aparezcan (tras pegar, crear, renombrar...)."""
        self.por_seleccionar = set(uris)
        if self.reloj_seleccion:
            GLib.source_remove(self.reloj_seleccion)
        self.reloj_seleccion = GLib.timeout_add(3000, self._olvidar_seleccion)
        self._seleccionar_pendientes()

    def _olvidar_seleccion(self):
        self.por_seleccionar.clear()
        self.reloj_seleccion = 0
        return False

    def _seleccionar_pendientes(self):
        if not self.por_seleccionar or self.contenido.cargando:
            return
        rutas = []
        for uri in self.por_seleccionar:
            fila = self.contenido.filas.get(uri)
            if fila:
                rutas.append(self.modelo.get_path(fila))
        if rutas:
            rutas.sort()
            self.vista.seleccionar(rutas)

    def seleccion(self):
        """[(Gio.File, Gio.FileInfo)] de lo seleccionado."""
        modelo = self.vista.w.get_model()
        if modelo is None:
            return []
        elementos = []
        for ruta in self.vista.seleccion():
            fila = modelo.get_iter(ruta)
            elementos.append((modelo.get_value(fila, C_ARCHIVO), modelo.get_value(fila, C_INFO)))
        return elementos

    def uris_seleccion(self):
        return [f.get_uri() for f, _ in self.seleccion()]

    def seleccion_cambiada(self):
        if self.ventana.panel is self:
            self.ventana.actualizar_estado(self)

    def invertir_seleccion(self):
        modelo = self.vista.w.get_model()
        elegidas = {r.to_string() for r in self.vista.seleccion()}
        rutas = [Gtk.TreePath.new_from_indices([i]) for i in range(len(modelo))
                 if str(i) not in elegidas]
        self.vista.seleccionar(rutas, cursor=False)

    # Vista, zoom, orden, ocultos

    def poner_vista(self, tipo):
        rutas = [r.copy() for r in self.vista.seleccion()] if self.vista else []
        self.tipo = tipo
        zoom = self.app.estado["zoom"].get(tipo, vista.ZOOM_INICIAL[tipo])
        tam = vista.TAMANOS[tipo][max(0, min(zoom, len(vista.TAMANOS[tipo]) - 1))]
        if self.vista:
            self.vista.w.destroy()
        self.contenido.poner_tamano(tam)
        self.vista = vista.Vista(tipo, None if self.contenido.cargando else self.modelo, tam, self)
        self.vista.modo(self.contenido.modo)
        self.vista.mostrar_orden(*self.contenido.orden)
        self.desliza.add(self.vista.w)
        self.vista.w.show()
        if rutas:
            self.vista.seleccionar(rutas)

    def zoom(self, paso):
        tamanos = vista.TAMANOS[self.tipo]
        actual = self.app.estado["zoom"].get(self.tipo, vista.ZOOM_INICIAL[self.tipo])
        nuevo = vista.ZOOM_INICIAL[self.tipo] if paso == 0 else max(0, min(actual + paso, len(tamanos) - 1))
        if nuevo != actual:
            self.app.estado["zoom"][self.tipo] = nuevo
            self.app.guardar_estado()
            self.poner_vista(self.tipo)

    def _rueda(self, _w, evento):
        """Ctrl + rueda del ratón: zoom."""
        if not evento.state & Gdk.ModifierType.CONTROL_MASK:
            return False
        ok, _dx, dy = evento.get_scroll_deltas()
        direccion = evento.direction
        if direccion == Gdk.ScrollDirection.UP or (ok and dy < 0):
            self.zoom(1)
        elif direccion == Gdk.ScrollDirection.DOWN or (ok and dy > 0):
            self.zoom(-1)
        return True

    def ordenar_por(self, campo, descendente=None):
        actual, desc = self.contenido.orden
        if descendente is None:
            descendente = not desc if campo == actual else False
        self.contenido.ordenar(campo, descendente)
        self.vista.mostrar_orden(campo, descendente)
        self.app.estado["orden"], self.app.estado["descendente"] = campo, descendente
        self.app.guardar_estado()
        self.ventana.panel_cambiado(self)

    def poner_ocultos(self, si):
        self.ocultos = si
        self.contenido.filtrar(si, self.contenido.texto)
        self.ventana.actualizar_estado(self)

    # Búsqueda

    def mostrar_busqueda(self):
        if self.ubicacion == "papelera":
            return
        self.busqueda.set_reveal_child(True)
        self.entrada_busqueda.grab_focus()

    def cerrar_busqueda(self):
        estaba = self.contenido.modo == "busqueda" or self.contenido.texto
        self.busqueda.set_reveal_child(False)
        self.entrada_busqueda.set_text("")
        if estaba:
            self._cargar(self.ubicacion)
        self.enfocar_vista()

    def _pedir_busqueda(self):
        if self.reloj_busqueda:
            GLib.source_remove(self.reloj_busqueda)
        self.reloj_busqueda = GLib.timeout_add(250, self._buscar)

    def _buscar(self):
        self.reloj_busqueda = 0
        texto = self.entrada_busqueda.get_text().strip()
        recursiva = self.subcarpetas.get_active()
        if texto and recursiva:
            self.contenido.buscar(self.ubicacion, texto, self.ocultos)
        elif self.contenido.modo == "busqueda":
            self.contenido.texto = archivos.plegar(texto)
            self.contenido.cargar(self.ubicacion)
        else:
            self.contenido.filtrar(self.ocultos, texto)
        self.vista.modo(self.contenido.modo)
        self.ventana.actualizar_estado(self)
        return False

    # Abrir

    def activar(self, ruta):
        modelo = self.vista.w.get_model()
        fila = modelo.get_iter(ruta)
        elegidos = self.seleccion() or [(modelo.get_value(fila, C_ARCHIVO), modelo.get_value(fila, C_INFO))]
        self.ventana.abrir(elegidos)

    def clic_medio(self, ruta):
        modelo = self.vista.w.get_model()
        fila = modelo.get_iter(ruta)
        if modelo.get_value(fila, C_ES_DIR):
            self.ventana.nueva_pestana(modelo.get_value(fila, C_ARCHIVO), cambiar=False)

    def menu(self, evento, sobre_elemento):
        self.ventana.menu_contextual(self, evento, sobre_elemento)

    def enfocar(self):
        self.ventana.activar_panel(self)

    def enfocar_vista(self, primera=False):
        self.vista.w.grab_focus()
        if primera and len(self.vista.w.get_model() or []) and not self.vista.seleccion():
            self.vista.poner_cursor(Gtk.TreePath.new_first())

    # Arrastrar y soltar

    def destino_soltar(self, ruta):
        """(carpeta donde soltar, True si es una carpeta de la vista) o (None, False)."""
        modelo = self.vista.w.get_model()
        if ruta is not None and modelo is not None:
            fila = modelo.get_iter(ruta)
            if modelo.get_value(fila, C_ES_DIR) and modelo.get_value(fila, C_URI) not in vista.ARRASTRANDO:
                return modelo.get_value(fila, C_ARCHIVO), True
        if self.contenido.modo == "carpeta" and self.escribible:
            return self.ubicacion, False
        return None, False

    def soltar(self, uris, destino, accion):
        self.ventana.soltar(uris, destino, accion)

    # Búsqueda al teclear (type-ahead)

    def teclear(self, evento):
        if evento.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK):
            return False
        if evento.keyval == Gdk.KEY_BackSpace and self.tecleo:
            self.tecleo = self.tecleo[:-1]
        elif evento.keyval == Gdk.KEY_Escape and self.tecleo:
            self.tecleo = ""
        else:
            letra = Gdk.keyval_to_unicode(evento.keyval)
            if letra < 32 or letra == 127 or (letra == 32 and not self.tecleo):
                return False
            self.tecleo += chr(letra)
        if self.reloj_tecleo:
            GLib.source_remove(self.reloj_tecleo)
        self.reloj_tecleo = GLib.timeout_add(TECLEO_MS, self._fin_tecleo)
        if self.tecleo:
            buscado = archivos.plegar(self.tecleo)
            modelo = self.vista.w.get_model() or []
            for i, fila in enumerate(modelo):
                if archivos.plegar(fila[C_NOMBRE]).startswith(buscado):
                    ruta = Gtk.TreePath.new_from_indices([i])
                    self.vista.seleccionar([ruta])
                    break
        self.ventana.actualizar_estado(self)
        return True

    def _fin_tecleo(self):
        self.tecleo, self.reloj_tecleo = "", 0
        self.ventana.actualizar_estado(self)
        return False

    # Barra de estado

    def texto_estado(self):
        if self.tecleo:
            return f"Ir a: {self.tecleo}"
        if self.paginas.get_visible_child_name() == "aviso":
            return ""
        modelo = self.modelo
        elegidos = self.seleccion()
        if elegidos:
            if len(elegidos) == 1:
                f, info = elegidos[0]
                texto = f"«{info.get_display_name()}»"
                if not archivos.es_carpeta(info):
                    texto += f" ({archivos.tamano_texto(info.get_size())})"
                return f"{texto} {archivos.tipo_texto(info)}"
            tamano = sum(i.get_size() for _f, i in elegidos if not archivos.es_carpeta(i))
            return f"{len(elegidos)} elementos seleccionados ({archivos.tamano_texto(tamano)})"
        n = len(modelo)
        tamano = sum(fila[C_TAMANO] for fila in modelo)
        texto = "1 elemento" if n == 1 else f"{n} elementos"
        if n:
            texto += f" ({archivos.tamano_texto(tamano)})"
        if self.contenido.modo == "busqueda":
            texto = ("Buscando… " if self.contenido.cargando else "Resultados: ") + texto
        elif self.contenido.cargando:
            texto = "Cargando…"
        if self.libre:
            texto += f", espacio libre: {self.libre}"
        return texto


# --- Ventana -------------------------------------------------------------------

class Ventana(Acciones, Gtk.ApplicationWindow):

    def __init__(self, app, destinos):
        super().__init__(application=app, title="ARCHBOX")
        self.app = app
        self.get_style_context().add_class("archbox")
        self.set_icon_name("system-file-manager")
        estado = app.estado
        self.set_default_size(estado["ancho"], estado["alto"])
        self.panel = None
        self._acciones()

        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(caja)
        caja.pack_start(self._herramientas(), False, False, 0)
        self.lateral = Gtk.Paned()
        caja.pack_start(self.lateral, True, True, 0)
        self.lugares = lugares.Lugares(self)
        self.lateral.pack1(self.lugares, False, False)
        self.lateral.set_position(estado["lateral"])
        self.lateral.connect("notify::position", self._lateral_movido)
        self.division = Gtk.Paned()
        self.lateral.pack2(self.division, True, False)
        self.libros = [self._libro()]
        self.division.pack1(self.libros[0], True, False)
        self.estado = Gtk.Label(xalign=0, ellipsize=3)
        self.estado.get_style_context().add_class("estado")
        barra_estado = Gtk.Box()
        barra_estado.get_style_context().add_class("barra-estado")
        barra_estado.pack_start(self.estado, True, True, 0)
        caja.pack_start(barra_estado, False, False, 0)

        self.connect("key-press-event", self._tecla)
        self.connect("configure-event", self._medida)
        self.connect("destroy", self._cerrada)
        self.app.ops.al_cambiar_historial.append(self._historial)
        caja.show_all()
        self.lugares.set_visible(estado["panel_lateral"])
        for d in destinos or [Gio.File.new_for_path(GLib.get_home_dir())]:
            self.nueva_pestana(d)
        self._historial()

    # Construcción

    def _herramientas(self):
        barra = Gtk.Box(spacing=2)
        barra.get_style_context().add_class("herramientas")
        for icono, accion, ayuda in (("go-previous-symbolic", "win.atras", "Atrás (Alt+Izquierda)"),
                                     ("go-next-symbolic", "win.adelante", "Adelante (Alt+Derecha)"),
                                     ("go-up-symbolic", "win.subir", "Subir (Alt+Arriba)"),
                                     ("go-home-symbolic", "win.inicio", "Carpeta personal (Alt+Inicio)"),
                                     ("view-refresh-symbolic", "win.recargar", "Recargar (F5)")):
            barra.pack_start(icono_boton(icono, accion, ayuda), False, False, 0)
        self.ruta = BarraRuta(self)
        barra.pack_start(self.ruta, True, True, 6)
        barra.pack_start(icono_boton("edit-find-symbolic", "win.buscar", "Buscar (Ctrl+F)"), False, False, 0)
        vistas = Gtk.Box()
        vistas.get_style_context().add_class("linked")
        for icono, tipo, ayuda in (("view-grid-symbolic", "iconos", "Iconos (Ctrl+1)"),
                                   ("view-list-symbolic", "compacta", "Lista compacta (Ctrl+2)"),
                                   ("view-paged-symbolic", "detallada", "Lista detallada (Ctrl+3)")):
            b = Gtk.ToggleButton()
            b.set_image(Gtk.Image.new_from_icon_name(icono, Gtk.IconSize.LARGE_TOOLBAR))
            b.set_action_name("win.vista")
            b.set_action_target_value(GLib.Variant("s", tipo))
            b.set_tooltip_text(ayuda)
            b.set_can_focus(False)
            vistas.pack_start(b, False, False, 0)
        barra.pack_start(vistas, False, False, 4)
        self.boton_menu = Gtk.MenuButton()
        self.boton_menu.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic",
                                                               Gtk.IconSize.LARGE_TOOLBAR))
        self.boton_menu.set_tooltip_text("Menú")
        self.boton_menu.set_can_focus(False)
        barra.pack_start(self.boton_menu, False, False, 0)
        return barra

    def _historial(self):
        self.boton_menu.set_menu_model(self._menu_principal())
        self.actualizar_acciones()

    def _libro(self):
        libro = Gtk.Notebook(scrollable=True, show_border=False)
        # Un grupo por ventana: las pestañas pasan de un lado al otro de la vista dividida, pero no a
        # otra ventana (el panel seguiría atado a la ventana de la que salió)
        libro.set_group_name(f"archbox-{id(self)}")
        libro.connect("switch-page", lambda _l, panel, _n: GLib.idle_add(self.activar_panel, panel))
        libro.connect("page-removed", self._pagina_quitada)
        libro.connect("page-added", lambda lib, *_: lib.set_show_tabs(lib.get_n_pages() > 1))
        return libro

    # Paneles y pestañas

    def libro_de(self, panel):
        return next((lib for lib in self.libros if lib.page_num(panel) >= 0), self.libros[0])

    def paneles(self):
        return [p for lib in self.libros for p in lib.get_children()]

    def nueva_pestana(self, destino, cambiar=True, libro=None):
        libro = libro or (self.libro_de(self.panel) if self.panel else self.libros[0])
        panel = Panel(self, destino)
        sitio = libro.page_num(self.panel) + 1 if self.panel in libro.get_children() else -1
        n = libro.insert_page(panel, panel.etiqueta, sitio)
        libro.set_tab_reorderable(panel, True)
        libro.set_tab_detachable(panel, True)
        if cambiar or not self.panel:
            libro.set_current_page(n)
            self.activar_panel(panel)
            panel.enfocar_vista()
        return panel

    def cerrar_panel(self, panel):
        libro = self.libro_de(panel)
        if len(self.paneles()) == 1:
            self.destroy()
            return
        panel.contenido.cancelar()
        libro.remove_page(libro.page_num(panel))

    def _pagina_quitada(self, libro, _panel, _n):
        libro.set_show_tabs(libro.get_n_pages() > 1)
        if libro.get_n_pages() == 0 and len(self.libros) > 1:
            self._quitar_division(libro)
        elif self.panel not in self.paneles():
            otro = libro if libro.get_n_pages() else self.libros[0]
            self.activar_panel(otro.get_nth_page(otro.get_current_page()))

    def activar_panel(self, panel):
        if panel is None or panel is self.panel or panel not in self.paneles():
            return False
        self.panel = panel
        self._marcar_libros()
        self.panel_cambiado(panel)
        return False

    def _marcar_libros(self):
        """En la vista dividida, el lado que no tiene el foco se ve atenuado."""
        for lib in self.libros:
            activo = self.panel in lib.get_children() or len(self.libros) == 1
            estilo = lib.get_style_context()
            (estilo.remove_class if activo else estilo.add_class)("inactivo")

    def panel_cambiado(self, panel):
        if panel is not self.panel:
            return
        self.ruta.poner(panel.ubicacion)
        self.lugares.marcar(panel.ubicacion)
        self.set_title(f"{panel.titulo()} — ARCHBOX")
        self.lookup_action("vista").set_state(GLib.Variant("s", panel.tipo))
        self.lookup_action("ordenar").set_state(GLib.Variant("s", panel.contenido.orden[0]))
        self.lookup_action("descendente").set_state(GLib.Variant("b", panel.contenido.orden[1]))
        self.actualizar_estado(panel)

    def actualizar_estado(self, panel):
        if panel is self.panel:
            self.estado.set_text(panel.texto_estado())
            self.actualizar_acciones()

    def actualizar_acciones(self):
        p = self.panel
        if p is None:
            return
        elegidos = p.seleccion()
        hay = bool(elegidos)
        papelera = p.ubicacion == "papelera"
        escribir = p.escribible
        borrables = hay and not papelera and all(i.get_attribute_boolean("access::can-delete") or
                                                 i.get_attribute_boolean("access::can-trash")
                                                 for _f, i in elegidos)
        estados = {
            "atras": bool(p.atras), "adelante": bool(p.adelante),
            "subir": isinstance(p.ubicacion, Gio.File) and p.ubicacion.get_parent() is not None,
            "copiar": hay and not papelera, "cortar": borrables, "pegar": escribir,
            "copiar-direccion": not papelera and (hay or isinstance(p.ubicacion, Gio.File)),
            "pegar-en": hay and len(elegidos) == 1 and archivos.es_carpeta(elegidos[0][1]),
            "renombrar": hay and not papelera and all(i.get_attribute_boolean("access::can-rename")
                                                      for _f, i in elegidos),
            "duplicar": hay and escribir and not papelera, "enlace": hay and escribir and not papelera,
            "papelera": borrables or (hay and papelera), "borrar": hay and (borrables or papelera),
            "restaurar": hay and papelera, "nueva-carpeta": escribir, "nuevo-archivo": escribir,
            "nuevo-desde": escribir, "propiedades": True, "abrir": hay, "abrir-pestana": hay,
            "abrir-ventana": hay, "abrir-con-otra": hay and len(elegidos) == 1,
            "deshacer": bool(self.app.ops.deshacer_pila), "rehacer": bool(self.app.ops.rehacer_pila),
            "marcador": isinstance(p.ubicacion, Gio.File) or (hay and archivos.es_carpeta(elegidos[0][1])),
            "terminal": isinstance(p.ubicacion, Gio.File) and p.ubicacion.get_path() is not None
                        and bool(GLib.find_program_in_path("foot")),
            "buscar": not papelera,
        }
        for nombre, si in estados.items():
            accion = self.lookup_action(nombre)
            if accion:
                accion.set_enabled(bool(si))

    def _lateral_movido(self, paned, _p):
        if self.lugares.get_visible() and paned.get_position() > 40:
            self.app.estado["lateral"] = paned.get_position()
            self.app.guardar_estado()

    def _medida(self, _w, _e):
        if not (self.is_maximized() or self.get_window() and
                self.get_window().get_state() & (Gdk.WindowState.FULLSCREEN | Gdk.WindowState.TILED)):
            ancho, alto = self.get_size()
            if (ancho, alto) != (self.app.estado["ancho"], self.app.estado["alto"]):
                self.app.estado["ancho"], self.app.estado["alto"] = ancho, alto
                self.app.guardar_estado()
        return False

    def _cerrada(self, _w):
        for p in self.paneles():
            p.contenido.cancelar()
        if self._historial in self.app.ops.al_cambiar_historial:
            self.app.ops.al_cambiar_historial.remove(self._historial)

    # Navegación (también la usan la barra de ruta y el panel lateral)

    def ir_a(self, destino, donde="aqui", seleccionar=None):
        if donde == "pestana":
            self.nueva_pestana(destino, cambiar=False)
        elif donde == "ventana":
            self.app.nueva_ventana([destino])
        else:
            self.panel.ir_a(destino, seleccionar=seleccionar)
            self.panel.enfocar_vista()

    def enfocar_vista(self):
        if self.panel:
            self.panel.enfocar_vista()

    def _tecla(self, _w, evento):
        """Las entradas de texto reciben las teclas antes que los atajos (Ctrl+V, Supr...);
        en la vista, las letras van a la búsqueda al teclear."""
        foco = self.get_focus()
        if isinstance(foco, Gtk.Editable):
            return self.propagate_key_event(evento)
        return bool(self.panel and foco is self.panel.vista.w and self.panel.teclear(evento))
