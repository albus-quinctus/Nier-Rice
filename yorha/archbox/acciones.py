"""Acciones de la ventana de ARCHBOX: atajos, menús (principal y contextuales), abrir
archivos, soltar y cada orden (a_<nombre>, una por acción «win.<nombre>»).

Es una mezcla (mixin) de ventana.Ventana: usa su panel activo (self.panel), su panel lateral
y las operaciones de la aplicación (self.app.ops).
"""
import contextlib
import os

from gi.repository import Gdk, Gio, GLib, Gtk

import archivos
import dialogos
import lugares
import vista

# Acción → atajos de teclado (los de Thunar; Ctrl+D añade un marcador, como en Thunar 4.18)
ATAJOS = {
    "atras": ["<Alt>Left", "BackSpace", "XF86Back"],
    "adelante": ["<Alt>Right", "XF86Forward"],
    "subir": ["<Alt>Up"],
    "inicio": ["<Alt>Home"],
    "recargar": ["F5", "<Control>r"],
    "ubicacion": ["<Control>l"],
    "buscar": ["<Control>f"],
    "nueva-ventana": ["<Control>n"],
    "nueva-pestana": ["<Control>t"],
    "cerrar-pestana": ["<Control>w"],
    "pestana-anterior": ["<Control>Page_Up"],
    "pestana-siguiente": ["<Control>Page_Down"],
    "vista::iconos": ["<Control>1"],
    "vista::compacta": ["<Control>2"],
    "vista::detallada": ["<Control>3"],
    "ocultos": ["<Control>h"],
    "panel-lateral": ["F9"],
    "dividir": ["F3"],
    "ampliar": ["<Control>plus", "<Control>KP_Add", "<Control>equal"],
    "reducir": ["<Control>minus", "<Control>KP_Subtract"],
    "tamano-normal": ["<Control>0", "<Control>KP_0"],
    "copiar": ["<Control>c"],
    "copiar-direccion": ["<Control><Shift>c"],
    "cortar": ["<Control>x"],
    "pegar": ["<Control>v"],
    "seleccionar-todo": ["<Control>a"],
    "invertir-seleccion": ["<Control>i"],
    "renombrar": ["F2"],
    "duplicar": ["<Control><Shift>d"],
    "marcador": ["<Control>d"],
    "nueva-carpeta": ["<Control><Shift>n"],
    "papelera": ["Delete", "KP_Delete"],
    "borrar": ["<Shift>Delete", "<Shift>KP_Delete"],
    "deshacer": ["<Control>z"],
    "rehacer": ["<Control><Shift>z", "<Control>y"],
    "propiedades": ["<Alt>Return", "<Alt>KP_Enter"],
}
# Acciones sin parámetro (además de las de ATAJOS)
OTRAS = ("abrir", "abrir-pestana", "abrir-ventana", "abrir-con-otra", "ejecutar", "terminal",
         "buscar-aqui", "comprimir", "extraer-aqui", "extraer-en", "enlace", "nuevo-archivo",
         "restaurar", "vaciar-papelera", "pegar-en", "cerrar-ventana")
CON_TEXTO = ("abrir-con", "predeterminada", "nuevo-desde")
COMPRIMIDOS = ("application/zip", "application/x-tar", "application/x-compressed-tar",
               "application/x-bzip-compressed-tar", "application/x-xz-compressed-tar",
               "application/x-zstd-compressed-tar", "application/x-7z-compressed",
               "application/vnd.rar", "application/x-rar", "application/gzip", "application/x-xz",
               "application/x-bzip", "application/x-bzip2", "application/zstd", "application/x-lzma",
               "application/x-cpio", "application/vnd.debian.binary-package", "application/x-rpm",
               "application/java-archive", "application/x-arj", "application/x-lha")


def es_comprimido(info):
    tipo = info.get_content_type() or ""
    return any(Gio.content_type_is_a(tipo, t) for t in COMPRIMIDOS)


class Acciones:
    """Las órdenes de la ventana; cada a_<nombre> atiende la acción «win.<nombre>»."""

    def _acciones(self):
        e = self.app.estado
        for nombre in list({a.split("::")[0] for a in ATAJOS}) + list(OTRAS):
            if nombre in ("vista", "ocultos", "panel-lateral", "dividir"):
                continue
            a = Gio.SimpleAction.new(nombre, None)
            a.connect("activate", lambda _a, _p, n=nombre: getattr(self, "a_" + n.replace("-", "_"))())
            self.add_action(a)
        for nombre in CON_TEXTO:
            a = Gio.SimpleAction.new(nombre, GLib.VariantType.new("s"))
            a.connect("activate",
                      lambda _a, p, n=nombre: getattr(self, "a_" + n.replace("-", "_"))(p.get_string()))
            self.add_action(a)
        for nombre, inicial in (("vista", e["vista"]), ("ordenar", e["orden"])):
            a = Gio.SimpleAction.new_stateful(nombre, GLib.VariantType.new("s"), GLib.Variant("s", inicial))
            a.connect("activate", self._cambiar_texto)
            self.add_action(a)
        for nombre, inicial in (("ocultos", e["ocultos"]), ("panel-lateral", e["panel_lateral"]),
                                ("dividir", False), ("descendente", e["descendente"])):
            a = Gio.SimpleAction.new_stateful(nombre, None, GLib.Variant("b", inicial))
            a.connect("activate", self._alternar)
            self.add_action(a)

    def _menu_principal(self):
        m = Gio.Menu()
        s = Gio.Menu()
        s.append("Ventana nueva", "win.nueva-ventana")
        s.append("Pestaña nueva", "win.nueva-pestana")
        s.append("Vista dividida", "win.dividir")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Crear carpeta…", "win.nueva-carpeta")
        s.append("Crear documento vacío…", "win.nuevo-archivo")
        s.append("Añadir a marcadores", "win.marcador")
        s.append("Abrir terminal aquí", "win.terminal")
        m.append_section(None, s)
        s = Gio.Menu()
        texto = self.app.ops.texto_deshacer()
        s.append(f"Deshacer: {texto}" if texto else "Deshacer", "win.deshacer")
        texto = self.app.ops.texto_rehacer()
        s.append(f"Rehacer: {texto}" if texto else "Rehacer", "win.rehacer")
        m.append_section(None, s)
        s = Gio.Menu()
        for tipo, texto in (("iconos", "Iconos"), ("compacta", "Lista compacta"),
                            ("detallada", "Lista detallada")):
            s.append(texto, f"win.vista::{tipo}")
        m.append_section(None, s)
        s = Gio.Menu()
        orden = Gio.Menu()
        for campo, texto in vista.ORDENES.items():
            orden.append(texto, f"win.ordenar::{campo}")
        orden.append("Descendente", "win.descendente")
        s.append_submenu("Ordenar por", orden)
        s.append("Mostrar archivos ocultos", "win.ocultos")
        s.append("Panel lateral", "win.panel-lateral")
        zoom = Gio.Menu()
        zoom.append("Ampliar", "win.ampliar")
        zoom.append("Reducir", "win.reducir")
        zoom.append("Tamaño normal", "win.tamano-normal")
        s.append_submenu("Tamaño de los iconos", zoom)
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Avisar al conectar un dispositivo", "app.avisar-usb")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Cerrar ventana", "win.cerrar-ventana")
        s.append("Salir", "app.salir")
        m.append_section(None, s)
        return m

    # Abrir archivos

    def abrir(self, elementos, donde="aqui"):
        carpetas = [f for f, i in elementos if archivos.es_carpeta(i)]
        otros = [(f, i) for f, i in elementos if not archivos.es_carpeta(i)]
        if self.panel.ubicacion == "papelera" and otros:
            dialogos.mensaje(self, "Está en la papelera", "Restáuralo para abrirlo.")
            return
        for n, carpeta in enumerate(carpetas):
            if donde == "ventana":
                self.app.nueva_ventana([carpeta])
            elif donde == "aqui" and n == 0 and len(carpetas) == 1:
                self.ir_a(carpeta)
            else:
                self.nueva_pestana(carpeta, cambiar=False)
        if otros:
            self.abrir_archivos(otros)

    def abrir_archivos(self, elementos, app=None):
        """Abre con la aplicación predeterminada; nunca ejecuta programas con doble clic."""
        por_app = {}
        for f, info in elementos:
            tipo = info.get_content_type() or "application/octet-stream"
            if app is None and archivos.es_programa(info):
                dialogos.mensaje(self, f"«{info.get_display_name()}» es un programa",
                                 "Por seguridad, ARCHBOX no ejecuta programas al abrirlos. "
                                 "Si confías en él, ejecútalo desde una terminal.")
                continue
            elegida = app
            if elegida is None and (Gio.content_type_is_a(tipo, "application/x-desktop") or (
                    info.get_attribute_boolean("access::can-execute")
                    and Gio.content_type_is_a(tipo, "text/plain"))):
                # Lanzadores y guiones se abren como texto, no se ejecutan
                elegida = Gio.AppInfo.get_default_for_type("text/plain", False)
            elegida = elegida or Gio.AppInfo.get_default_for_type(tipo, not f.is_native())
            if elegida is None:
                dialogos.abrir_con_otra(self, f, info,
                                        lambda a, f=f, i=info: self.abrir_archivos([(f, i)], a))
                continue
            por_app.setdefault(elegida.get_id() or id(elegida), (elegida, []))[1].append(f)
        for elegida, lista in por_app.values():
            self.lanzar_app(elegida, lista)

    def lanzar_app(self, app, lista):
        contexto = self.get_display().get_app_launch_context()
        contexto.set_timestamp(Gtk.get_current_event_time())
        try:
            if app.supports_files() or app.supports_uris() or len(lista) <= 1:
                app.launch(lista, contexto)
            else:
                for f in lista:
                    app.launch([f], contexto)
        except GLib.Error as e:
            dialogos.mensaje(self, f"No se pudo abrir con {app.get_display_name()}", e.message)

    # Soltar (arrastrar y soltar, también desde el panel lateral)

    def soltar(self, uris, destino, accion):
        D = Gdk.DragAction
        uris = [u for u in uris if not Gio.File.new_for_uri(u).equal(destino)]
        if not uris:
            return
        if accion == D.ASK:
            menu = Gtk.Menu()
            menu.get_style_context().add_class("archbox")
            for texto, a in (("Copiar aquí", D.COPY), ("Mover aquí", D.MOVE), ("Enlazar aquí", D.LINK)):
                item = Gtk.MenuItem(label=texto)
                item.connect("activate", lambda _i, a=a: self.soltar(uris, destino, a))
                menu.append(item)
            menu.append(Gtk.SeparatorMenuItem())
            menu.append(Gtk.MenuItem(label="Cancelar"))
            menu.show_all()
            menu.attach_to_widget(self, None)
            menu.popup_at_pointer(None)
            return
        panel = self.panel
        fin = lambda t: panel.seleccionar_uris(t.creados) if panel.ubicacion and \
            isinstance(panel.ubicacion, Gio.File) and panel.ubicacion.equal(destino) else None  # noqa: E731
        if accion == D.MOVE:
            mismos = [u for u in uris if (Gio.File.new_for_uri(u).get_parent() or destino).equal(destino)]
            uris = [u for u in uris if u not in mismos]
            if uris:
                self.app.ops.mover(uris, destino, fin)
        elif accion == D.LINK:
            self.app.ops.enlazar(uris, destino, fin)
        else:
            self.app.ops.copiar(uris, destino, fin)

    # Menú contextual

    def menu_contextual(self, panel, evento, sobre_elemento):
        self.activar_panel(panel)
        self.actualizar_acciones()
        modelo = self._menu_elementos(panel) if sobre_elemento else self._menu_fondo(panel)
        menu = Gtk.Menu.new_from_model(modelo)
        menu.get_style_context().add_class("archbox")
        menu.attach_to_widget(self, None)
        menu.popup_at_pointer(evento)

    def _menu_fondo(self, panel):
        m = Gio.Menu()
        if panel.ubicacion == "papelera":
            m.append("Vaciar la papelera", "win.vaciar-papelera")
            return m
        s = Gio.Menu()
        s.append("Crear carpeta…", "win.nueva-carpeta")
        crear = Gio.Menu()
        crear.append("Documento vacío…", "win.nuevo-archivo")
        plantillas = archivos.plantillas()
        if plantillas:
            p = Gio.Menu()
            for nombre, ruta in plantillas:
                item = Gio.MenuItem.new(os.path.splitext(nombre)[0], None)
                item.set_action_and_target_value("win.nuevo-desde", GLib.Variant("s", ruta))
                p.append_item(item)
            crear.append_section(None, p)
        s.append_submenu("Crear documento", crear)
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Abrir terminal aquí", "win.terminal")
        s.append("Buscar en esta carpeta", "win.buscar")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Pegar", "win.pegar")
        s.append("Copiar dirección", "win.copiar-direccion")
        s.append("Seleccionar todo", "win.seleccionar-todo")
        s.append("Deshacer", "win.deshacer")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Añadir a marcadores", "win.marcador")
        s.append("Mostrar archivos ocultos", "win.ocultos")
        orden = Gio.Menu()
        for campo, texto in vista.ORDENES.items():
            orden.append(texto, f"win.ordenar::{campo}")
        orden.append("Descendente", "win.descendente")
        s.append_submenu("Ordenar por", orden)
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Propiedades", "win.propiedades")
        m.append_section(None, s)
        return m

    def _menu_elementos(self, panel):
        elegidos = panel.seleccion()
        m = Gio.Menu()
        if panel.ubicacion == "papelera":
            m.append("Restaurar", "win.restaurar")
            m.append("Borrar definitivamente", "win.borrar")
            s = Gio.Menu()
            s.append("Propiedades", "win.propiedades")
            m.append_section(None, s)
            return m
        carpetas = all(archivos.es_carpeta(i) for _f, i in elegidos)
        s = Gio.Menu()
        s.append("Abrir", "win.abrir")
        if carpetas:
            s.append("Abrir en una pestaña nueva", "win.abrir-pestana")
            s.append("Abrir en una ventana nueva", "win.abrir-ventana")
        if len(elegidos) == 1 and dialogos.es_escritorio_de_confianza(*elegidos[0]):
            s.append("Ejecutar", "win.ejecutar")
        s.append_submenu("Abrir con", self._menu_abrir_con(elegidos))
        m.append_section(None, s)
        s = Gio.Menu()
        if len(elegidos) == 1 and carpetas:
            s.append("Abrir terminal aquí", "win.terminal")
            s.append("Buscar en esta carpeta", "win.buscar-aqui")
        if GLib.find_program_in_path("xarchiver"):
            s.append("Comprimir…", "win.comprimir")
            if len(elegidos) == 1 and es_comprimido(elegidos[0][1]):
                s.append("Extraer aquí", "win.extraer-aqui")
                s.append("Extraer en…", "win.extraer-en")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Cortar", "win.cortar")
        s.append("Copiar", "win.copiar")
        s.append("Copiar dirección", "win.copiar-direccion")
        if len(elegidos) == 1 and carpetas:
            s.append("Pegar en la carpeta", "win.pegar-en")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Duplicar", "win.duplicar")
        s.append("Crear enlace", "win.enlace")
        s.append("Renombrar…", "win.renombrar")
        if len(elegidos) == 1 and carpetas:
            s.append("Añadir a marcadores", "win.marcador")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Mover a la papelera", "win.papelera")
        s.append("Borrar definitivamente", "win.borrar")
        m.append_section(None, s)
        s = Gio.Menu()
        s.append("Propiedades", "win.propiedades")
        m.append_section(None, s)
        return m

    def _menu_abrir_con(self, elegidos):
        m = Gio.Menu()
        tipos = {i.get_content_type() for _f, i in elegidos}
        if len(tipos) == 1:
            tipo = tipos.pop() or "application/octet-stream"
            predeterminada = Gio.AppInfo.get_default_for_type(tipo, False)
            apps = Gio.AppInfo.get_recommended_for_type(tipo)
            apps_lista, defecto = Gio.Menu(), Gio.Menu()
            for app in apps:
                if not app.get_id():
                    continue
                es_pred = predeterminada and app.equal(predeterminada)
                texto = app.get_display_name() + (" (predeterminada)" if es_pred else "")
                item = Gio.MenuItem.new(texto, None)
                item.set_action_and_target_value("win.abrir-con", GLib.Variant("s", app.get_id()))
                if app.get_icon():
                    item.set_icon(app.get_icon())
                apps_lista.append_item(item)
                if not es_pred:
                    item = Gio.MenuItem.new(app.get_display_name(), None)
                    item.set_action_and_target_value("win.predeterminada", GLib.Variant("s", app.get_id()))
                    defecto.append_item(item)
            m.append_section(None, apps_lista)
            if defecto.get_n_items():
                fin = Gio.Menu()
                fin.append_submenu("Establecer como predeterminada", defecto)
                m.append_section(None, fin)
        otra = Gio.Menu()
        otra.append("Otra aplicación…", "win.abrir-con-otra")
        m.append_section(None, otra)
        return m

    # Acciones de la ventana: a_<nombre>

    def a_atras(self):
        self.panel.volver()

    def a_adelante(self):
        self.panel.avanzar()

    def a_subir(self):
        if isinstance(self.panel.ubicacion, Gio.File) and self.panel.ubicacion.get_parent():
            self.panel.ir_a(self.panel.ubicacion.get_parent())

    def a_inicio(self):
        self.ir_a(Gio.File.new_for_path(GLib.get_home_dir()))

    def a_recargar(self):
        self.panel.recargar()
        self.lugares.rellenar()

    def a_ubicacion(self):
        self.ruta.editar()

    def a_buscar(self):
        self.panel.mostrar_busqueda()

    def a_buscar_aqui(self):
        elegidos = self.panel.seleccion()
        if elegidos and archivos.es_carpeta(elegidos[0][1]):
            self.panel.ir_a(elegidos[0][0])
        self.panel.mostrar_busqueda()

    def a_nueva_ventana(self):
        self.app.nueva_ventana([self.panel.ubicacion])

    def a_nueva_pestana(self):
        self.nueva_pestana(self.panel.ubicacion)

    def a_cerrar_pestana(self):
        self.cerrar_panel(self.panel)

    def a_cerrar_ventana(self):
        self.destroy()

    def _cambiar_pestana(self, paso):
        libro = self.libro_de(self.panel)
        n = libro.get_n_pages()
        if n > 1:
            libro.set_current_page((libro.get_current_page() + paso) % n)

    def a_pestana_anterior(self):
        self._cambiar_pestana(-1)

    def a_pestana_siguiente(self):
        self._cambiar_pestana(1)

    def _cambiar_texto(self, accion, valor):
        accion.set_state(valor)
        texto = valor.get_string()
        if accion.get_name() == "vista":
            self.app.estado["vista"] = texto
            self.app.guardar_estado()
            self.panel.poner_vista(texto)
        else:
            self.panel.ordenar_por(texto, self.panel.contenido.orden[1])

    def _alternar(self, accion, _p):
        si = not accion.get_state().get_boolean()
        accion.set_state(GLib.Variant("b", si))
        nombre = accion.get_name()
        if nombre == "ocultos":
            self.app.estado["ocultos"] = si
            self.app.guardar_estado()
            self.app.poner_ocultos(si)
        elif nombre == "panel-lateral":
            self.app.estado["panel_lateral"] = si
            self.app.guardar_estado()
            self.lugares.set_visible(si)
        elif nombre == "dividir":
            if si:
                libro = self._libro()
                self.libros.append(libro)
                self.division.pack2(libro, True, False)
                libro.show_all()
                self.division.set_position(self.division.get_allocated_width() // 2)
                self.nueva_pestana(self.panel.ubicacion, libro=libro)
            else:
                self._quitar_division(self.libros[1])
        elif nombre == "descendente":
            self.panel.ordenar_por(self.panel.contenido.orden[0], si)

    def _quitar_division(self, libro):
        """Cierra un lado de la vista dividida; sus pestañas pasan al otro si quedan."""
        if len(self.libros) < 2:
            return
        self.libros.remove(libro)
        queda = self.libros[0]
        for panel in libro.get_children():  # las pestañas no se pierden: pasan al otro lado
            etiqueta = libro.get_tab_label(panel)
            libro.remove(panel)
            queda.append_page(panel, etiqueta)
            queda.set_tab_reorderable(panel, True)
            queda.set_tab_detachable(panel, True)
        libro.destroy()
        self.lookup_action("dividir").set_state(GLib.Variant("b", False))
        if self.panel not in self.paneles():
            self.panel = None
            self.activar_panel(queda.get_nth_page(queda.get_current_page()))
        self._marcar_libros()

    def a_ampliar(self):
        self.panel.zoom(1)

    def a_reducir(self):
        self.panel.zoom(-1)

    def a_tamano_normal(self):
        self.panel.zoom(0)

    def a_seleccionar_todo(self):
        self.panel.vista.seleccionar_todo()

    def a_invertir_seleccion(self):
        self.panel.invertir_seleccion()

    def a_abrir(self):
        self.abrir(self.panel.seleccion())

    def a_abrir_pestana(self):
        self.abrir(self.panel.seleccion(), "pestana")

    def a_abrir_ventana(self):
        self.abrir(self.panel.seleccion(), "ventana")

    def a_abrir_con(self, ident):
        app = next((a for a in Gio.AppInfo.get_all() if a.get_id() == ident), None)
        if app:
            self.abrir_archivos([e for e in self.panel.seleccion() if not archivos.es_carpeta(e[1])] or
                                self.panel.seleccion(), app)

    def a_predeterminada(self, ident):
        app = next((a for a in Gio.AppInfo.get_all() if a.get_id() == ident), None)
        tipos = {i.get_content_type() for _f, i in self.panel.seleccion()}
        if app and len(tipos) == 1:
            try:
                app.set_as_default_for_type(tipos.pop())
            except GLib.Error as e:
                dialogos.mensaje(self, "No se pudo cambiar la aplicación predeterminada", e.message)

    def a_abrir_con_otra(self):
        elegidos = self.panel.seleccion()
        if elegidos:
            f, info = elegidos[0]
            dialogos.abrir_con_otra(self, f, info, lambda app: self.abrir_archivos(elegidos, app))

    def a_ejecutar(self):
        elegidos = self.panel.seleccion()
        if len(elegidos) == 1 and dialogos.es_escritorio_de_confianza(*elegidos[0]):
            try:
                app = Gio.DesktopAppInfo.new_from_filename(elegidos[0][0].get_path())
            except TypeError:
                app = None
            if app:
                self.lanzar_app(app, [])

    def _carpeta_menu(self):
        """La carpeta seleccionada (si es una sola) o la que se ve."""
        elegidos = self.panel.seleccion()
        if len(elegidos) == 1 and archivos.es_carpeta(elegidos[0][1]):
            return elegidos[0][0]
        return self.panel.ubicacion if isinstance(self.panel.ubicacion, Gio.File) else None

    def a_terminal(self):
        carpeta = self._carpeta_menu()
        ruta = carpeta.get_path() if carpeta else None
        if ruta and not archivos.lanzar(["foot", f"--working-directory={ruta}"], ruta):
            dialogos.mensaje(self, "No se pudo abrir la terminal", "No se encuentra foot.")

    def a_comprimir(self):
        rutas = [f.get_path() for f, _ in self.panel.seleccion() if f.get_path()]
        if rutas:
            archivos.lanzar(["xarchiver", "--compress"] + rutas, os.path.dirname(rutas[0]))

    def a_extraer_aqui(self):
        elegidos = self.panel.seleccion()
        if elegidos and elegidos[0][0].get_path():
            ruta = elegidos[0][0].get_path()
            archivos.lanzar(["xarchiver", "--extract-to", os.path.dirname(ruta), ruta], os.path.dirname(ruta))

    def a_extraer_en(self):
        elegidos = self.panel.seleccion()
        if elegidos and elegidos[0][0].get_path():
            ruta = elegidos[0][0].get_path()
            archivos.lanzar(["xarchiver", "--extract", ruta], os.path.dirname(ruta))

    def _al_terminar_aqui(self):
        """Función que selecciona en el panel actual lo que cree la operación."""
        panel = self.panel
        return lambda t: panel.seleccionar_uris(t.creados)

    def a_copiar(self):
        self.app.ops.portapapeles.poner(self.panel.uris_seleccion(), False)

    def a_cortar(self):
        self.app.ops.portapapeles.poner(self.panel.uris_seleccion(), True)

    def a_copiar_direccion(self):
        """Copia como texto la ruta completa de lo seleccionado (una por línea) o de la carpeta
        que se ve. Con wl-copy el texto sigue en el portapapeles aunque se cierre ARCHBOX."""
        elegidos = [f for f, _ in self.panel.seleccion()]
        if not elegidos and isinstance(self.panel.ubicacion, Gio.File):
            elegidos = [self.panel.ubicacion]
        texto = "\n".join(f.get_path() or f.get_parse_name() for f in elegidos)
        if texto and not archivos.lanzar(["wl-copy", "--", texto]):
            Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(texto, -1)

    def _pegar_en(self, carpeta):
        portapapeles = self.app.ops.portapapeles

        def pegar(uris, cortar):
            if not uris:
                return
            fin = self._al_terminar_aqui()
            if cortar:
                clave = tuple(uris)
                if clave in portapapeles.moviendo:  # ya se está moviendo: pegar dos veces lo repartiría
                    return
                portapapeles.moviendo.add(clave)

                def movido(t):
                    portapapeles.moviendo.discard(clave)
                    fin(t)
                    # Se vacía solo si todo se movió: si se canceló o falló algo, lo cortado sigue
                    if not any(Gio.File.new_for_uri(u).query_exists(None) for u in uris):
                        portapapeles.vaciar_si(uris)
                self.app.ops.mover(uris, carpeta, movido)
            else:
                self.app.ops.copiar(uris, carpeta, fin)

        portapapeles.leer(pegar)

    def a_pegar(self):
        if isinstance(self.panel.ubicacion, Gio.File):
            self._pegar_en(self.panel.ubicacion)

    def a_pegar_en(self):
        carpeta = self._carpeta_menu()
        if carpeta:
            self._pegar_en(carpeta)

    def a_renombrar(self):
        elegidos = self.panel.seleccion()
        if len(elegidos) == 1:
            f, info = elegidos[0]
            dialogos.pedir_nombre(self, "Renombrar", "Renombrar", info.get_display_name(),
                                  lambda nombre: self.app.ops.renombrar([(f, nombre)],
                                                                        self._al_terminar_aqui()),
                                  f.get_parent(), archivos.es_carpeta(info))
        elif elegidos:
            dialogos.RenombrarVarios(self, elegidos,
                                     lambda pares: self.app.ops.renombrar(pares, self._al_terminar_aqui()))

    def a_duplicar(self):
        self.app.ops.duplicar(self.panel.uris_seleccion(), self._al_terminar_aqui())

    def a_enlace(self):
        self.app.ops.enlazar(self.panel.uris_seleccion(), self.panel.ubicacion, self._al_terminar_aqui())

    def a_marcador(self):
        carpeta = self._carpeta_menu()
        if carpeta:
            lugares.anadir_marcador(carpeta)
            self.app.rellenar_lugares()

    def a_nueva_carpeta(self):
        carpeta = self.panel.ubicacion
        nombre = archivos.nombre_libre(carpeta, "Carpeta nueva", None, True) \
            if carpeta.get_child("Carpeta nueva").query_exists(None) else "Carpeta nueva"
        dialogos.pedir_nombre(self, "Crear carpeta", "Crear", nombre,
                              lambda n: self.app.ops.crear("carpeta", carpeta.get_child(n), None,
                                                           self._al_terminar_aqui()),
                              carpeta, True)

    def _nuevo_documento(self, plantilla):
        carpeta = self.panel.ubicacion
        base = os.path.basename(plantilla) if plantilla else "Documento nuevo"
        nombre = archivos.nombre_libre(carpeta, base) if carpeta.get_child(base).query_exists(None) else base
        dialogos.pedir_nombre(self, "Crear documento", "Crear", nombre,
                              lambda n: self.app.ops.crear("archivo", carpeta.get_child(n), plantilla,
                                                           self._al_terminar_aqui()),
                              carpeta)

    def a_nuevo_archivo(self):
        self._nuevo_documento(None)

    def a_nuevo_desde(self, plantilla):
        self._nuevo_documento(plantilla)

    def a_papelera(self):
        if self.panel.ubicacion == "papelera":
            self.a_borrar()
        else:
            self.app.ops.papelera(self.panel.uris_seleccion())

    def a_borrar(self):
        elegidos = self.panel.seleccion()
        if not elegidos:
            return
        uris = [f.get_uri() for f, _ in elegidos]
        texto = (f"¿Borrar definitivamente «{elegidos[0][1].get_display_name()}»?" if len(elegidos) == 1
                 else f"¿Borrar definitivamente {len(elegidos)} elementos?")
        papelera = self.panel.ubicacion == "papelera"

        def si(ok):
            if not ok:
                return
            infos = [archivos.elemento_de(f.get_path()) for f, _ in elegidos] if papelera else []

            def fin(_t):
                for e in infos:
                    if e and not os.path.lexists(e.archivo):
                        with contextlib.suppress(OSError):
                            os.remove(e.info)
            self.app.ops.borrar(uris, fin)

        dialogos.confirmar(self, texto, "Si borras un elemento, se perderá para siempre.", "Borrar", si)

    def a_restaurar(self):
        rutas = [f.get_path() for f, _ in self.panel.seleccion()]
        if rutas:
            self.app.ops.restaurar(rutas)

    def a_vaciar_papelera(self):
        self.vaciar_papelera()

    def vaciar_papelera(self):
        dialogos.confirmar(self, "¿Vaciar la papelera?",
                           "Todos los elementos de la papelera se borrarán para siempre.",
                           "Vaciar la papelera",
                           lambda ok: ok and self.app.ops.vaciar_papelera())

    def a_deshacer(self):
        self.app.ops.deshacer(self._al_terminar_aqui())

    def a_rehacer(self):
        self.app.ops.rehacer(self._al_terminar_aqui())

    def a_propiedades(self):
        elegidos = self.panel.seleccion()
        if elegidos:
            self.propiedades(elegidos)
        elif isinstance(self.panel.ubicacion, Gio.File):
            self.propiedades_de([self.panel.ubicacion])

    def propiedades_de(self, lista):
        elementos = []
        for f in lista:
            try:
                elementos.append((f, f.query_info(archivos.ATRIBUTOS, Gio.FileQueryInfoFlags.NONE, None)))
            except GLib.Error as e:
                dialogos.mensaje(self, "No se pueden ver las propiedades", e.message)
                return
        if elementos:
            self.propiedades(elementos)

    def propiedades(self, elementos):
        dialogos.Propiedades(self, elementos,
                             lambda f, nombre: self.app.ops.renombrar([(f, nombre)],
                                                                      self._al_terminar_aqui()))
