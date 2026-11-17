"""Bandeja del sistema para panel.py: iconos de nm-applet, Proton VPN y demás.

En Wayland los iconos de bandeja no usan la bandeja de X11, sino StatusNotifierItem por
D-Bus. Aquí está todo lo que hace falta, con Gio y sin programas extra:
- el vigilante (org.kde.StatusNotifierWatcher), con el que se registran los programas;
- el anfitrión, que lee el icono, el título y el estado de cada uno y los pinta;
- los menús, que llegan por com.canonical.dbusmenu y se dibujan con Gtk.Menu.
Clic izquierdo: Activate (o el menú, si el programa solo tiene menú); clic derecho: el menú;
clic central: SecondaryActivate; rueda: Scroll.
"""
import time

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # versiones ya fijadas por panel.py

VIGILANTE = "org.kde.StatusNotifierWatcher"
ITEM = "org.kde.StatusNotifierItem"
MENU = "com.canonical.dbusmenu"
RUTA_ITEM = "/StatusNotifierItem"
TAMANO = 22
RUEDA = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK

# Lo que ofrece el vigilante por D-Bus. El XML de introspección se arma desde esta tabla
# (con \x3c y \x3e en vez de los signos de menor y mayor, para que el archivo no lleve etiquetas)
METODOS = ("RegisterStatusNotifierItem", "RegisterStatusNotifierHost")
PROPIEDADES = (("RegisteredStatusNotifierItems", "as"), ("IsStatusNotifierHostRegistered", "b"),
               ("ProtocolVersion", "i"))
SENALES = (("StatusNotifierItemRegistered", "s"), ("StatusNotifierItemUnregistered", "s"),
           ("StatusNotifierHostRegistered", None))


def _xml_vigilante():
    a, c = "\x3c", "\x3e"
    partes = [f'{a}node{c}{a}interface name="{VIGILANTE}"{c}']
    partes += [f'{a}method name="{m}"{c}{a}arg type="s" direction="in"/{c}{a}/method{c}' for m in METODOS]
    partes += [f'{a}property name="{n}" type="{t}" access="read"/{c}' for n, t in PROPIEDADES]
    for n, t in SENALES:
        partes.append(f'{a}signal name="{n}"{c}{a}arg type="{t}"/{c}{a}/signal{c}' if t
                      else f'{a}signal name="{n}"/{c}')
    partes.append(f"{a}/interface{c}{a}/node{c}")
    return "".join(partes)


def _pixbuf_de(pixmaps):
    """El icono a(iiay) que más se acerca a TAMANO, como GdkPixbuf (llega en ARGB de red)."""
    validos = [(w, h, bytes(d)) for w, h, d in pixmaps or [] if w > 0 and h > 0
               and len(d) >= w * h * 4]
    if not validos:
        return None
    w, h, datos = min(validos, key=lambda p: (p[0] < TAMANO, abs(p[0] - TAMANO)))
    rgba = bytearray(len(datos))
    rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = datos[1::4], datos[2::4], datos[3::4], datos[0::4]
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB,
                                             True, 8, w, h, w * 4)
    return pixbuf.scale_simple(TAMANO, TAMANO, GdkPixbuf.InterpType.BILINEAR) if w != TAMANO else pixbuf


def marcado_seguro(texto):
    """La descripción del ToolTip puede traer marcado; si Pango no lo entiende, va como texto."""
    try:
        Pango.parse_markup(texto, -1, "\0")
        return texto
    except GLib.Error:
        return GLib.markup_escape_text(texto)


class Icono(Gtk.EventBox):
    """Un programa de la bandeja."""

    def __init__(self, bus, servicio, ruta):
        super().__init__()
        self.bus, self.servicio, self.ruta = bus, servicio, ruta
        self.props_item = {}
        self.imagen = Gtk.Image()
        boton = Gtk.Button()
        boton.set_relief(Gtk.ReliefStyle.NONE)
        boton.get_style_context().add_class("bandeja")
        boton.add(self.imagen)
        boton.connect("button-release-event", self.al_pulsar)
        boton.connect("scroll-event", self.al_rodar)
        boton.add_events(RUEDA)
        self.add(boton)
        self.boton = boton
        self.menu = None
        self.suscripcion = bus.signal_subscribe(servicio, ITEM, None, ruta, None,
                                                Gio.DBusSignalFlags.NONE, self.al_cambiar)
        self.releer()

    def quitar(self):
        self.bus.signal_unsubscribe(self.suscripcion)
        self.destroy()

    def al_cambiar(self, *_):
        # NewIcon, NewStatus, NewToolTip... llegan en ráfagas: se relee una vez
        if not getattr(self, "pendiente", False):
            self.pendiente = True
            GLib.timeout_add(50, self.releer)

    def releer(self):
        self.pendiente = False
        self.bus.call(self.servicio, self.ruta, "org.freedesktop.DBus.Properties", "GetAll",
                      GLib.Variant("(s)", (ITEM,)), GLib.VariantType("(a{sv})"),
                      Gio.DBusCallFlags.NONE, 2000, None, self._leidas)
        return False

    def _leidas(self, bus, resultado):
        try:
            self.props_item = bus.call_finish(resultado).unpack()[0]
        except GLib.Error:
            return
        p = self.props_item
        estado = p.get("Status", "Active")
        self.set_visible(estado != "Passive")
        atencion = estado == "NeedsAttention"
        nombre = (atencion and p.get("AttentionIconName")) or p.get("IconName") or ""
        pixmap = (atencion and p.get("AttentionIconPixmap")) or p.get("IconPixmap")
        tema = Gtk.IconTheme.get_default()
        ruta_tema = p.get("IconThemePath")
        if ruta_tema and ruta_tema not in tema.get_search_path():
            tema.append_search_path(ruta_tema)
        if nombre.startswith("/"):
            try:
                self.imagen.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(nombre, TAMANO, TAMANO))
            except GLib.Error:
                self.imagen.set_from_icon_name("image-missing", Gtk.IconSize.LARGE_TOOLBAR)
        elif nombre and tema.has_icon(nombre):
            self.imagen.set_from_icon_name(nombre, Gtk.IconSize.LARGE_TOOLBAR)
        elif (pixbuf := _pixbuf_de(pixmap)):
            self.imagen.set_from_pixbuf(pixbuf)
        else:
            self.imagen.set_from_icon_name(nombre or "image-missing", Gtk.IconSize.LARGE_TOOLBAR)
        self.imagen.set_pixel_size(TAMANO)
        consejo = p.get("ToolTip")
        texto = ""
        if consejo and len(consejo) >= 4:
            texto = GLib.markup_escape_text(consejo[2])
            if consejo[3]:
                texto += f"\n{marcado_seguro(consejo[3])}"
        self.boton.set_tooltip_markup(texto or None)
        if not texto:
            self.boton.set_tooltip_text(p.get("Title") or None)

    def _llamar(self, metodo, parametros):
        self.bus.call(self.servicio, self.ruta, ITEM, metodo, parametros, None,
                      Gio.DBusCallFlags.NONE, 2000, None, None)

    def al_pulsar(self, boton, evento):
        x, y = int(evento.x_root), int(evento.y_root)
        tiene_menu = bool(self.props_item.get("Menu"))
        if evento.button == Gdk.BUTTON_PRIMARY and not self.props_item.get("ItemIsMenu", False):
            self._llamar("Activate", GLib.Variant("(ii)", (x, y)))
        elif evento.button in (1, 3) and tiene_menu:
            self.abrir_menu(evento)
        elif evento.button == Gdk.BUTTON_SECONDARY:
            self._llamar("ContextMenu", GLib.Variant("(ii)", (x, y)))
        elif evento.button == Gdk.BUTTON_MIDDLE:
            self._llamar("SecondaryActivate", GLib.Variant("(ii)", (x, y)))
        return False  # GtkButton termina su clic; si no, el botón se queda pulsado

    def al_rodar(self, _boton, evento):
        hay, _dx, dy = evento.get_scroll_deltas()
        if evento.direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            delta, sentido = (1 if evento.direction == Gdk.ScrollDirection.UP else -1), "vertical"
        elif hay and dy:
            delta, sentido = (-1 if dy > 0 else 1), "vertical"
        else:
            return False
        self._llamar("Scroll", GLib.Variant("(is)", (delta, sentido)))
        return True

    # --- Menú (com.canonical.dbusmenu) --------------------------------------

    def abrir_menu(self, evento):
        ruta = self.props_item["Menu"]
        self.bus.call(self.servicio, ruta, MENU, "AboutToShow", GLib.Variant("(i)", (0,)),
                      None, Gio.DBusCallFlags.NONE, 1000, None, None)
        self.bus.call(self.servicio, ruta, MENU, "GetLayout",
                      GLib.Variant("(iias)", (0, -1, [])), GLib.VariantType("(u(ia{sv}av))"),
                      Gio.DBusCallFlags.NONE, 2000, None, self._con_disposicion, ruta)

    def _con_disposicion(self, bus, resultado, ruta):
        try:
            _version, raiz = bus.call_finish(resultado).unpack()
        except GLib.Error:
            return
        if self.menu:
            self.menu.destroy()
        self.menu = self._construir(raiz[2], ruta)
        self.menu.show_all()
        self.menu.popup_at_widget(self.boton, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def _construir(self, hijos, ruta):
        menu = Gtk.Menu()
        grupo = None
        for hijo in hijos:
            ident, props, nietos = hijo
            if not props.get("visible", True):
                continue
            if props.get("type") == "separator":
                menu.append(Gtk.SeparatorMenuItem())
                grupo = None
                continue
            texto = props.get("label", "")
            tipo = props.get("toggle-type", "")
            if tipo == "checkmark":
                elemento = Gtk.CheckMenuItem.new_with_mnemonic(texto)
                elemento.set_active(props.get("toggle-state", 0) == 1)
            elif tipo == "radio":
                elemento = Gtk.RadioMenuItem.new_with_mnemonic_from_widget(grupo, texto)
                grupo = elemento
                elemento.set_active(props.get("toggle-state", 0) == 1)
            else:
                elemento = Gtk.MenuItem.new_with_mnemonic(texto)
            elemento.set_sensitive(props.get("enabled", True))
            if nietos or props.get("children-display") == "submenu":
                elemento.set_submenu(self._construir(nietos, ruta))
            else:
                elemento.connect("activate", self._pulsado, ruta, ident)
            menu.append(elemento)
        return menu

    def _pulsado(self, elemento, ruta, ident):
        # Al pintar el estado inicial, GTK también emite "activate" en los radio: se ignora
        if isinstance(elemento, Gtk.RadioMenuItem) and not elemento.get_active():
            return
        self.bus.call(self.servicio, ruta, MENU, "Event",
                      GLib.Variant("(isvu)", (ident, "clicked", GLib.Variant("i", 0),
                                              int(time.time()))),
                      None, Gio.DBusCallFlags.NONE, 2000, None, None)


class Bandeja(Gtk.Box):
    """Vigilante (con el que se registran los programas) y anfitrión (los pinta) a la vez."""

    def __init__(self, al_cambiar=None):
        super().__init__(spacing=2)
        self.iconos = {}  # "servicio/ruta" -> Icono
        self.al_cambiar = al_cambiar or (lambda: None)  # se llama al entrar o salir un icono
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        nodo = Gio.DBusNodeInfo.new_for_xml(_xml_vigilante())
        self.registro = self.bus.register_object("/StatusNotifierWatcher", nodo.interfaces[0],
                                                 self._metodo, self._propiedad, None)
        Gio.bus_own_name_on_connection(self.bus, VIGILANTE, Gio.BusNameOwnerFlags.NONE,
                                       None, None)
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus",
                                  "NameOwnerChanged", "/org/freedesktop/DBus", None,
                                  Gio.DBusSignalFlags.NONE, self._dueno_cambiado)

    def _metodo(self, bus, remitente, _ruta, _interfaz, metodo, parametros, invocacion):
        if metodo == "RegisterStatusNotifierItem":
            valor = parametros.unpack()[0]
            # Unos mandan su nombre de bus y otros solo la ruta (libayatana): se completa
            servicio, ruta = (remitente, valor) if valor.startswith("/") else (valor, RUTA_ITEM)
            self.anadir(servicio, ruta)
        elif metodo == "RegisterStatusNotifierHost":
            bus.emit_signal(None, "/StatusNotifierWatcher", VIGILANTE,
                            "StatusNotifierHostRegistered", None)
        invocacion.return_value(None)

    def _propiedad(self, _bus, _remitente, _ruta, _interfaz, nombre):
        if nombre == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.iconos))
        if nombre == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if nombre == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def anadir(self, servicio, ruta):
        clave = f"{servicio}{ruta}"
        if clave in self.iconos:
            return
        icono = Icono(self.bus, servicio, ruta)
        self.iconos[clave] = icono
        self.pack_start(icono, False, False, 0)
        icono.show_all()
        self.bus.emit_signal(None, "/StatusNotifierWatcher", VIGILANTE,
                             "StatusNotifierItemRegistered", GLib.Variant("(s)", (clave,)))
        self.al_cambiar()

    def _dueno_cambiado(self, _bus, _remitente, _ruta, _interfaz, _senal, parametros):
        nombre, _antes, ahora = parametros.unpack()
        if ahora:
            return
        for clave in [c for c, i in self.iconos.items() if i.servicio == nombre]:
            self.iconos.pop(clave).quitar()
            self.bus.emit_signal(None, "/StatusNotifierWatcher", VIGILANTE,
                                 "StatusNotifierItemUnregistered", GLib.Variant("(s)", (clave,)))
            self.al_cambiar()

