#!/usr/bin/env python3
"""ARCHBOX — gestor de archivos hecho a mano, con lo que da Thunar (XFCE 4.20).

Uso: python3 -I archbox.py [RUTA|URI]...

Una sola instancia (Gtk.Application es.nier.Archbox): volver a llamarlo abre otra ventana en
la que ya corre. Además atiende org.freedesktop.FileManager1 en el bus de sesión, así que
«Mostrar en la carpeta» de los navegadores abre ARCHBOX con el archivo seleccionado.

Aspecto: en YoRHa (~/.config/sway/config enlazado a .../yorha/sway.conf) usa archbox.css con
la paleta del tema claro u oscuro (el de tema.sh; lo vuelve a leer con SIGUSR1 o al cambiar
$XDG_RUNTIME_DIR/yorha-tema); en el escritorio de trabajo usa el tema de GTK del sistema.

Estado (vista, orden, ocultos, zoom, tamaño de ventana...) en ~/.config/archbox/estado.json.
Nunca usa un shell: los programas se abren con Gio.AppInfo o Gio.Subprocess con listas.
"""
import json
import os
import signal
import sys

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
YORHA = os.path.dirname(CARPETA)
sys.path[:0] = [CARPETA, YORHA]  # con python3 -I las carpetas no están en sys.path
import comun  # noqa: E402
import acciones  # noqa: E402
import ventana  # noqa: E402
import vista  # noqa: E402
from operaciones import Operaciones  # noqa: E402

ID = "es.nier.Archbox"
ESTADO = os.path.join(GLib.get_user_config_dir(), "archbox", "estado.json")
ESTADO_INICIAL = {"vista": "iconos", "zoom": {}, "orden": "nombre", "descendente": False,
                  "ocultos": False, "panel_lateral": True, "lateral": 190, "ancho": 960,
                  "alto": 620, "avisar_usb": True}
# En el escritorio de trabajo solo se retoca lo justo sobre el tema del sistema
CSS_TRABAJO = """
.archbox .barra-estado { padding: 3px 8px; }
.archbox .herramientas { padding: 4px; }
.archbox .lugares .seccion { padding: 8px 8px 2px 8px; font-weight: bold; opacity: 0.7; }
.archbox .lugares row { padding: 3px 8px; }
.archbox .lugares levelbar block { min-height: 3px; }
.archbox .ruta button.actual { font-weight: bold; }
.archbox .aviso-vista, .archbox .aviso { opacity: 0.7; }
.archbox notebook.inactivo .vista { opacity: 0.8; }
.archbox entry.ubicacion.error { color: @error_color; }
"""
DBUS = """
<node>
  <interface name="org.freedesktop.FileManager1">
    <method name="ShowFolders">
      <arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/>
    </method>
    <method name="ShowItems">
      <arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/>
    </method>
    <method name="ShowItemProperties">
      <arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/>
    </method>
  </interface>
</node>
"""


def leer_estado():
    estado = json.loads(json.dumps(ESTADO_INICIAL))
    try:
        with open(ESTADO, encoding="utf-8") as f:
            guardado = json.load(f)
    except (OSError, ValueError):
        return estado
    for clave, valor in guardado.items():
        if clave in estado and type(valor) is type(estado[clave]):
            estado[clave] = valor
    if estado["vista"] not in vista.VISTAS:
        estado["vista"] = "iconos"
    if estado["orden"] not in vista.ORDENES:
        estado["orden"] = "nombre"
    return estado


class Archbox(Gtk.Application):

    def __init__(self):
        super().__init__(application_id=ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.estado = None
        self.reloj_estado = 0

    # Arranque

    def do_startup(self):
        Gtk.Application.do_startup(self)
        GLib.set_application_name("ARCHBOX")
        self.estado = leer_estado()
        self.iconos = vista.Iconos()
        self.miniaturas = vista.Miniaturas()
        self.ops = Operaciones(self)
        self.proveedor = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.proveedor,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.yorha = comun.es_yorha()
        self.aplicar_tema()
        # SIGUSR1 (como la barra) vuelve a leer el tema; sin manejador, la señal lo cerraría
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, self._releer_tema)
        if self.yorha:
            try:
                self.monitor_tema = Gio.File.new_for_path(os.path.join(comun.RUNTIME, "yorha-tema")) \
                    .monitor_file(Gio.FileMonitorFlags.NONE, None)
                self.monitor_tema.connect("changed", lambda *_: self._releer_tema())
            except GLib.Error:
                pass
        for nombre, atajos in acciones.ATAJOS.items():
            self.set_accels_for_action(f"win.{nombre}", atajos)
        salir = Gio.SimpleAction.new("salir", None)
        salir.connect("activate", lambda *_: self.salir())
        self.add_action(salir)
        self.set_accels_for_action("app.salir", ["<Control>q"])
        avisar = Gio.SimpleAction.new_stateful("avisar-usb", None,
                                               GLib.Variant("b", self.estado["avisar_usb"]))
        avisar.connect("activate", self._alternar_aviso)
        self.add_action(avisar)
        abrir = Gio.SimpleAction.new("abrir-volumen", GLib.VariantType.new("s"))
        abrir.connect("activate", lambda _a, p: self._abrir_volumen(p.get_string()))
        self.add_action(abrir)
        self.monitor_volumenes = Gio.VolumeMonitor.get()
        self.monitor_volumenes.connect("volume-added", self._volumen_nuevo)
        self._registrar_dbus()

    def do_activate(self):
        self.nueva_ventana(None)

    def do_open(self, archivos_, _n, _pista):
        """Carpetas: una pestaña para cada una; archivos: su carpeta con ellos seleccionados."""
        carpetas, sueltos = [], {}
        for f in archivos_:
            tipo = f.query_file_type(Gio.FileQueryInfoFlags.NONE, None)
            if tipo == Gio.FileType.DIRECTORY or f.get_parent() is None:
                carpetas.append(f)
            else:
                sueltos.setdefault(f.get_parent().get_uri(), []).append(f.get_uri())
        if carpetas or not sueltos:
            self.nueva_ventana(carpetas or None)
        self._mostrar_elementos(sueltos)

    def _mostrar_elementos(self, por_carpeta, id_inicio=""):
        for carpeta, uris in por_carpeta.items():
            v = self.nueva_ventana([Gio.File.new_for_uri(carpeta)], id_inicio)
            v.panel.seleccionar_uris(uris)

    def nueva_ventana(self, destinos, id_inicio=""):
        v = ventana.Ventana(self, destinos)
        if id_inicio:
            v.set_startup_id(id_inicio)
        v.present()
        return v

    def salir(self):
        self.guardar_estado(ya=True)
        for v in self.get_windows():
            v.destroy()

    def do_shutdown(self):
        if self.estado is not None:
            self.guardar_estado(ya=True)
        Gtk.Application.do_shutdown(self)

    # Estado

    def guardar_estado(self, ya=False):
        """Guarda estado.json un segundo después del último cambio (o ya)."""
        if self.reloj_estado:
            GLib.source_remove(self.reloj_estado)
            self.reloj_estado = 0
        if not ya:
            self.reloj_estado = GLib.timeout_add(1000, self.guardar_estado, True)
            return False
        try:
            os.makedirs(os.path.dirname(ESTADO), mode=0o700, exist_ok=True)
            temporal = f"{ESTADO}.{os.getpid()}"
            with open(temporal, "w", encoding="utf-8") as f:
                json.dump(self.estado, f, indent=1, ensure_ascii=False)
            os.replace(temporal, ESTADO)
        except OSError:
            pass
        return False

    def ventanas(self):
        return [v for v in self.get_windows() if isinstance(v, ventana.Ventana)]

    def poner_ocultos(self, si):
        for v in self.ventanas():
            v.lookup_action("ocultos").set_state(GLib.Variant("b", si))
            for p in v.paneles():
                p.poner_ocultos(si)

    def rellenar_lugares(self):
        for v in self.ventanas():
            v.lugares.rellenar()

    def soltar_montaje(self, raiz):
        """Antes de desmontar: ninguna pestaña puede seguir dentro (el montaje estaría ocupado)."""
        casa = Gio.File.new_for_path(GLib.get_home_dir())
        for v in self.ventanas():
            for p in v.paneles():
                u = p.ubicacion
                if isinstance(u, Gio.File) and (u.equal(raiz) or u.has_prefix(raiz)):
                    p.ir_a(casa)

    # Tema

    def aplicar_tema(self):
        if not self.yorha:
            self.proveedor.load_from_data(CSS_TRABAJO.encode())
            return
        from piezas import PALETAS  # la misma paleta que el panel de ajustes
        tema = comun.tema_actual()
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", tema == "oscuro")
        css = "".join(f"@define-color {k} {v};\n" for k, v in PALETAS[tema].items())
        with open(os.path.join(CARPETA, "archbox.css"), encoding="utf-8") as f:
            css += f.read()
        self.proveedor.load_from_data(css.encode())

    def _releer_tema(self):
        self.aplicar_tema()
        return GLib.SOURCE_CONTINUE

    # Dispositivos

    def _alternar_aviso(self, accion, _p):
        si = not accion.get_state().get_boolean()
        accion.set_state(GLib.Variant("b", si))
        self.estado["avisar_usb"] = si
        self.guardar_estado()

    def _volumen_nuevo(self, _m, volumen):
        """Como thunar-volman, pero mínimo: solo avisa (no monta solo)."""
        unidad = volumen.get_drive()
        if not self.estado["avisar_usb"] or not unidad or not unidad.is_removable():
            return
        aviso = Gio.Notification.new("Dispositivo conectado")
        aviso.set_body(f"«{volumen.get_name()}» está listo para abrirse.")
        aviso.set_icon(volumen.get_icon())
        objetivo = GLib.Variant("s", volumen.get_uuid() or volumen.get_name())
        aviso.add_button_with_target("Abrir", "app.abrir-volumen", objetivo)
        aviso.set_default_action_and_target("app.abrir-volumen", objetivo)
        self.send_notification("volumen", aviso)

    def _abrir_volumen(self, clave):
        for volumen in self.monitor_volumenes.get_volumes():
            if clave in (volumen.get_uuid(), volumen.get_name()):
                montaje = volumen.get_mount()
                if montaje:
                    self.nueva_ventana([montaje.get_root()])
                    return

                def montado(vol, resultado):
                    try:
                        vol.mount_finish(resultado)
                    except GLib.Error:
                        return
                    if vol.get_mount():
                        self.nueva_ventana([vol.get_mount().get_root()])

                volumen.mount(Gio.MountMountFlags.NONE, Gtk.MountOperation.new(self.get_active_window()),
                              None, montado)
                return

    # D-Bus: org.freedesktop.FileManager1

    def _registrar_dbus(self):
        conexion = self.get_dbus_connection()
        if conexion is None:
            return
        info = Gio.DBusNodeInfo.new_for_xml(DBUS).interfaces[0]
        try:
            conexion.register_object("/org/freedesktop/FileManager1", info, self._llamada_dbus, None, None)
        except GLib.Error:
            return
        Gio.bus_own_name_on_connection(conexion, "org.freedesktop.FileManager1",
                                       Gio.BusNameOwnerFlags.NONE, None, None)

    def _llamada_dbus(self, _con, _remitente, _ruta, _interfaz, metodo, parametros, invocacion):
        uris, id_inicio = parametros.unpack()
        archivos_ = [Gio.File.new_for_uri(u) if "://" in u or u.startswith("file:")
                     else Gio.File.new_for_path(u) for u in uris]
        if metodo == "ShowFolders":
            self.nueva_ventana(archivos_ or None, id_inicio)
        elif metodo == "ShowItems":
            por_carpeta = {}
            for f in archivos_:
                padre = f.get_parent() or f
                por_carpeta.setdefault(padre.get_uri(), []).append(f.get_uri())
            self._mostrar_elementos(por_carpeta, id_inicio)
        elif metodo == "ShowItemProperties":
            v = self.get_active_window() or self.nueva_ventana(
                [archivos_[0].get_parent()] if archivos_ and archivos_[0].get_parent() else None, id_inicio)
            if archivos_:
                v.propiedades_de(archivos_)
        invocacion.return_value(None)


def main():
    GLib.set_prgname("archbox")
    sys.exit(Archbox().run(sys.argv))


if __name__ == "__main__":
    main()
