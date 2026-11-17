"""Órdenes del sistema sin GTK (volumen, Wi-Fi, Bluetooth, pantallas, apps, MPRIS): menú, buscador y xfce."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback

from gi.repository import Gio, GLib


def orden(argv, tiempo=8):
    """Ejecuta argv sin shell y devuelve su salida, o None si falla o no existe."""
    if not shutil.which(argv[0]):
        return None
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=tiempo,
                           stdin=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def lanzar(argv):
    """Arranca un programa independiente del panel."""
    if shutil.which(argv[0]):
        subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def altura_barra():
    """Lo que reserva arriba la barra de escritorios, según sway (0 si no se sabe)."""
    try:
        r = subprocess.run(["swaymsg", "-t", "get_workspaces"], capture_output=True,
                           text=True, timeout=2)
        for ws in json.loads(r.stdout):
            if ws.get("focused"):
                salidas = json.loads(subprocess.run(["swaymsg", "-t", "get_outputs"],
                                                    capture_output=True, text=True,
                                                    timeout=2).stdout)
                y = next((o["rect"]["y"] for o in salidas if o.get("name") == ws.get("output")), 0)
                return max(0, ws["rect"]["y"] - y)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        pass
    return 0


def en_segundo_plano(trabajo, al_terminar):
    """Ejecuta trabajo() en un hilo y entrega el resultado en el hilo de GTK; si trabajo()
    falla, entrega None (lo mismo que orden() cuando algo no responde)."""
    def hilo():
        try:
            resultado = trabajo()
        except Exception:  # noqa: BLE001 — sin esto, quien espera (un botón en «...») no se entera nunca
            traceback.print_exc()
            resultado = None
        GLib.idle_add(lambda: al_terminar(resultado) and False)
    threading.Thread(target=hilo, daemon=True).start()

# --- Sistema: sonido, wifi, bluetooth ---------------------------------------

def nombre_pactl(tipo):
    return "@DEFAULT_SINK@" if tipo == "sink" else "@DEFAULT_SOURCE@"


def volumen(tipo):
    """(volumen 0-100, silenciado) del altavoz (sink) o micrófono (source) por defecto."""
    salida = orden(["pactl", f"get-{tipo}-volume", nombre_pactl(tipo)])
    silencio = orden(["pactl", f"get-{tipo}-mute", nombre_pactl(tipo)])
    if salida is None:
        return None, False
    m = re.search(r"(\d+)%", salida)
    return (int(m.group(1)) if m else 0), bool(silencio and "yes" in silencio)


def poner_volumen(tipo, valor):
    orden(["pactl", f"set-{tipo}-volume", nombre_pactl(tipo), f"{int(valor)}%"])


def alternar_silencio(tipo):
    """Alterna el silencio y devuelve el estado real tras el cambio."""
    orden(["pactl", f"set-{tipo}-mute", nombre_pactl(tipo), "toggle"])
    return volumen(tipo)[1]


def apps_con_sonido():
    """[(indice, nombre, volumen)] de los programas que están sonando."""
    salida = orden(["pactl", "list", "sink-inputs"])
    if not salida:
        return []
    apps = []
    for bloque in salida.split("Sink Input #")[1:]:
        indice = bloque.split("\n", 1)[0].strip()
        nombre = re.search(r'application\.name = "(.*)"', bloque)
        vol = re.search(r"Volume:.*?(\d+)%", bloque)
        apps.append((indice, nombre.group(1) if nombre else f"#{indice}",
                     int(vol.group(1)) if vol else 0))
    return apps


def partir_nmcli(linea):
    """Separa una línea de nmcli -t (los ':' dentro de los campos van escapados)."""
    campos, actual, escapado = [], "", False
    for letra in linea:
        if escapado:
            actual += letra
            escapado = False
        elif letra == "\\":
            escapado = True
        elif letra == ":":
            campos.append(actual)
            actual = ""
        else:
            actual += letra
    return campos + [actual]


def wifi_activado():
    salida = orden(["nmcli", "radio", "wifi"])
    return None if salida is None else salida.strip() == "enabled"


def alternar_wifi():
    activado = wifi_activado()
    if activado is not None:
        orden(["nmcli", "radio", "wifi", "off" if activado else "on"])
    return wifi_activado()


def redes_wifi():
    """[(ssid, señal, en_uso)] sin repetir SSID, la red en uso y la mejor señal primero."""
    salida = orden(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL",
                    "device", "wifi", "list", "--rescan", "auto"], tiempo=20)
    if salida is None:
        return None
    vistas, redes = set(), []
    for linea in salida.splitlines():
        en_uso, ssid, senal = (partir_nmcli(linea) + [""] * 3)[:3]
        if not ssid or ssid in vistas:
            continue
        vistas.add(ssid)
        redes.append((ssid, int(senal or 0), en_uso == "*"))
    return sorted(redes, key=lambda r: (not r[2], -r[1]))


def bluetooth_activado():
    salida = orden(["bluetoothctl", "show"])
    if salida is None or "Powered:" not in salida:
        return None
    return "Powered: yes" in salida


def alternar_bluetooth():
    activado = bluetooth_activado()
    if activado is not None:
        orden(["bluetoothctl", "power", "off" if activado else "on"])
    return bluetooth_activado()


def dispositivos_bluetooth():
    """[(mac, nombre, conectado)] de los dispositivos emparejados (dos llamadas, no una por cada uno)."""
    emparejados = orden(["bluetoothctl", "devices", "Paired"])
    if emparejados is None:
        return None
    conectados = {mac for mac, _ in _dispositivos(orden(["bluetoothctl", "devices", "Connected"]))}
    return [(mac, nombre, mac in conectados) for mac, nombre in _dispositivos(emparejados)]


def _dispositivos(salida):
    """(mac, nombre) de cada línea "Device MAC Nombre" de bluetoothctl."""
    for linea in (salida or "").splitlines():
        partes = linea.split(" ", 2)
        if len(partes) == 3 and partes[0] == "Device":
            yield partes[1], partes[2]


def conectado_bluetooth(mac):
    return "Connected: yes" in (orden(["bluetoothctl", "info", mac]) or "")


# Pantallas: sway las maneja con "output"; aquí solo se elige cómo colocarlas

def pantallas():
    """[(nombre, modelo, activa, ancho lógico)] de las salidas de sway, la del portátil primero."""
    salida = orden(["swaymsg", "-r", "-t", "get_outputs"])
    if salida is None:
        return None
    lista = []
    for o in json.loads(salida):
        # Apagada no tiene modo actual: se usa el primero que anuncia (el preferido)
        modo = o.get("current_mode") or (o.get("modes") or [{"width": 0}])[0]
        escala = o.get("scale") if (o.get("scale") or 0) > 0 else 1
        modelo = " ".join(x for x in (o.get("make"), o.get("model")) if x and x != "Unknown")
        lista.append((o["name"], modelo, bool(o.get("active")), round(modo["width"] / escala)))
    return sorted(lista, key=lambda p: not es_interna(p[0]))


def es_interna(nombre):
    return nombre.startswith(("eDP", "LVDS"))


# Categorías del lanzador: (nombre, categorías freedesktop que entran, ayuda). Cada aplicación
# va a la primera que encaje; las que no encajan en ninguna, a Otras.
CATEGORIAS = (
    ("Internet", {"Network"}, "Navegadores, correo y otras aplicaciones de red."),
    ("Oficina", {"Office"}, "Documentos, hojas de cálculo y presentaciones."),
    ("Desarrollo", {"Development"}, "Editores de código y herramientas de programación."),
    ("Multimedia", {"AudioVideo", "Audio", "Video"}, "Música, vídeo y sonido."),
    ("Gráficos", {"Graphics"}, "Imágenes, dibujo y visores."),
    ("Juegos", {"Game"}, "Juegos."),
    ("Utilidades", {"Utility"}, "Editores de texto, calculadora y otras herramientas."),
    ("Sistema", {"System", "Settings"}, "Terminales, configuración y herramientas del sistema."),
)
OTRAS = ("Otras", "Aplicaciones sin categoría conocida.")
AYUDAS_CATEGORIA = {n: a for n, _, a in CATEGORIAS + ((OTRAS[0], None, OTRAS[1]),)}


def categoria(app):
    """Nombre de la primera categoría de CATEGORIAS que encaja con la aplicación."""
    obtener = getattr(app, "get_categories", None)  # solo los .desktop la tienen
    suyas = set(((obtener() if obtener else None) or "").split(";"))
    return next((n for n, cats, _ in CATEGORIAS if cats & suyas), OTRAS[0])


def aplicaciones():
    """Aplicaciones visibles en el menú, por orden alfabético."""
    return sorted((a for a in Gio.AppInfo.get_all() if a.should_show()),
                  key=lambda a: a.get_display_name().lower())


def buscar_apps(apps, texto, maximo):
    """Las primeras maximo apps cuyo nombre o descripción contienen texto (ya en minúsculas)."""
    return [a for a in apps if texto in a.get_display_name().lower()
            or texto in (a.get_description() or "").lower()][:maximo]


def agrupar_apps(apps):
    """[(nombre, ayuda, [apps])] solo con las categorías que tienen alguna aplicación."""
    grupos = {n: [] for n in AYUDAS_CATEGORIA}
    for app in apps:
        grupos[categoria(app)].append(app)
    return [(n, AYUDAS_CATEGORIA[n], lista) for n, lista in grupos.items() if lista]


MPRIS = "org.mpris.MediaPlayer2"
RUTA_MPRIS = "/org/mpris/MediaPlayer2"


def llamar_dbus(destino, ruta, interfaz, metodo, parametros=None, respuesta=None):
    """Llamada síncrona al bus de sesión (siempre desde un hilo); None si falla."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        r = bus.call_sync(destino, ruta, interfaz, metodo, parametros,
                          GLib.VariantType(respuesta) if respuesta else None,
                          Gio.DBusCallFlags.NONE, 2000, None)
    except GLib.Error:
        return None
    return r.unpack() if r is not None else ()


def reproductores():
    """[(bus, titulo, artista, estado)] de los reproductores MPRIS abiertos, o None sin D-Bus."""
    nombres = llamar_dbus("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "ListNames", respuesta="(as)")
    if nombres is None:
        return None
    lista = []
    for bus in sorted(n for n in nombres[0] if n.startswith(MPRIS + ".")):
        props = llamar_dbus(bus, RUTA_MPRIS, "org.freedesktop.DBus.Properties", "GetAll",
                            GLib.Variant("(s)", (MPRIS + ".Player",)), "(a{sv})")
        if props is None:
            continue
        meta = props[0].get("Metadata", {})
        artistas = meta.get("xesam:artist") or []
        if isinstance(artistas, str):
            artistas = [artistas]
        lista.append((bus, meta.get("xesam:title") or bus.split(".")[3].capitalize(),
                      ", ".join(artistas), props[0].get("PlaybackStatus", "Stopped")))
    return lista


def mandar_reproductor(bus, metodo):
    """Play/pausa, anterior o siguiente; devuelve el estado tras el cambio."""
    llamar_dbus(bus, RUTA_MPRIS, MPRIS + ".Player", metodo)
    time.sleep(0.3)  # el reproductor tarda un momento en cambiar de canción
    estado = llamar_dbus(bus, RUTA_MPRIS, "org.freedesktop.DBus.Properties", "Get",
                         GLib.Variant("(ss)", (MPRIS + ".Player", "PlaybackStatus")), "(v)")
    return estado[0] if estado else None
