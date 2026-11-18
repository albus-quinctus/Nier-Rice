"""Piezas sin ventanas de ARCHBOX: atributos, nombres, fechas, la papelera y lanzar programas.

La papelera se lee directamente de disco según la especificación de freedesktop.org
(~/.local/share/Trash y las .Trash-UID de cada unidad), sin depender de gvfs: tirar a la
papelera lo hace Gio (g_file_trash) y aquí solo se listan, restauran y vacían los elementos.
"""
import os
import re
import stat
import unicodedata
import urllib.parse

from gi.repository import Gio, GLib

# Lo que se pide de cada archivo al listar una carpeta
ATRIBUTOS = ("standard::*,time::modified,time::access,time::created,unix::mode,unix::uid,"
             "unix::gid,owner::user,owner::group,access::*,id::filesystem")
# Programas que nunca se ejecutan con doble clic (como la opción segura de Thunar)
TIPOS_PROGRAMA = ("application/x-executable", "application/x-sharedlib",
                  "application/x-pie-executable", "application/vnd.appimage",
                  "application/x-ms-dos-executable", "application/x-msdownload")
UID = os.getuid()
NOMBRE_MAX = 255  # bytes que caben en un nombre de archivo (ext4, btrfs, FAT con LFN…)


def plegar(texto):
    """Texto para comparar al buscar: sin mayúsculas ni tildes («Música» → «musica»)."""
    return "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))


def es_carpeta(info):
    return info.get_file_type() == Gio.FileType.DIRECTORY


def es_oculto(info):
    nombre = info.get_name() or ""
    return info.get_is_hidden() or info.get_is_backup() or nombre.startswith(".")


def es_programa(info):
    """True si abrirlo con doble clic lo ejecutaría (binario, AppImage, .exe...)."""
    tipo = info.get_content_type() or ""
    return any(Gio.content_type_is_a(tipo, t) for t in TIPOS_PROGRAMA)


def tipo_texto(info):
    tipo = info.get_content_type()
    if info.get_file_type() == Gio.FileType.SYMBOLIC_LINK or info.get_is_symlink():
        destino = info.get_symlink_target() or ""
        return f"Enlace a {Gio.content_type_get_description(tipo)}" if tipo else f"Enlace roto ({destino})"
    return Gio.content_type_get_description(tipo) if tipo else "Desconocido"


def tamano_texto(tamano):
    return GLib.format_size(tamano)


def fecha_texto(segundos):
    """Fecha como la enseña Thunar: «Hoy, 12:30», «Ayer, 09:15» o «14/10/2026, 18:02»."""
    if not segundos:
        return ""
    fecha = GLib.DateTime.new_from_unix_local(segundos)
    hoy = GLib.DateTime.new_now_local()
    dias = (hoy.get_ymd(), hoy.add_days(-1).get_ymd())
    if fecha.get_ymd() == dias[0]:
        return fecha.format("Hoy, %H:%M")
    if fecha.get_ymd() == dias[1]:
        return fecha.format("Ayer, %H:%M")
    return fecha.format("%d/%m/%Y, %H:%M")


def segundos(info, atributo="time::modified"):
    return info.get_attribute_uint64(atributo) if info.has_attribute(atributo) else 0


def partir_extension(nombre, carpeta=False):
    """("foto", ".png"); las carpetas y los archivos ocultos sin punto más no tienen extensión."""
    if carpeta:
        return nombre, ""
    for doble in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if nombre.lower().endswith(doble) and len(nombre) > len(doble):
            return nombre[:-len(doble)], nombre[-len(doble):]
    base, ext = os.path.splitext(nombre)
    return (base, ext) if base else (nombre, "")


def nombre_libre(carpeta, nombre, etiqueta=None, es_dir=False):
    """Nombre que aún no existe en carpeta: «x (copia).txt», «x (copia 2).txt» o «x (2).txt»."""
    base, ext = partir_extension(nombre, es_dir)
    for n in range(1 if etiqueta else 2, 10000):
        if etiqueta:
            nuevo = f"{base} ({etiqueta}){ext}" if n == 1 else f"{base} ({etiqueta} {n}){ext}"
        else:
            nuevo = f"{base} ({n}){ext}"
        if not carpeta.get_child(nuevo).query_exists(None):
            return nuevo
    return nombre


def nombre_valido(nombre):
    """Mensaje de error si el nombre no sirve para un archivo, o None si vale."""
    if not nombre or nombre in (".", ".."):
        return "Escribe un nombre."
    if "/" in nombre or "\0" in nombre:
        return "El nombre no puede llevar «/»."
    if len(nombre.encode()) > NOMBRE_MAX:
        return "El nombre es demasiado largo."
    return None


def ya_existe(carpeta, nombre, actual):
    """True si en carpeta hay otro archivo llamado nombre. actual es el que se renombra: en
    FAT/exFAT «Foto» y «foto» son él mismo, y cambiar solo mayúsculas no choca con nada."""
    otro = carpeta.get_child(nombre)
    if not otro.query_exists(None):
        return False
    try:
        ids = [f.query_info("id::file", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, None)
               .get_attribute_string("id::file") for f in (otro, actual)]
    except GLib.Error:  # actual aún no existe (crear): el nombre está ocupado
        return True
    return ids[0] is None or ids[0] != ids[1]  # sin id (p. ej. en red) no se puede saber


def desde_texto(texto, base=None):
    """Gio.File de lo que se escribe o se pasa por la línea de órdenes (ruta, ~/..., o URI)."""
    texto = texto.strip()
    if texto.startswith("~"):
        texto = os.path.expanduser(texto)
    if "://" in texto or texto.startswith("file:"):
        return Gio.File.new_for_uri(texto)
    if base and not texto.startswith("/"):
        return base.resolve_relative_path(texto)
    return Gio.File.new_for_path(texto)


def ruta_bonita(gfile):
    """Ruta para enseñar: con ~ en lugar de la carpeta personal."""
    ruta = gfile.get_path()
    if not ruta:
        return gfile.get_parse_name()
    casa = GLib.get_home_dir()
    return "~" + ruta[len(casa):] if ruta == casa or ruta.startswith(casa + "/") else ruta


# --- Programas -----------------------------------------------------------------

def lanzar(argv, carpeta=None):
    """Arranca argv sin shell, con carpeta como directorio de trabajo; False si no se puede."""
    if not GLib.find_program_in_path(argv[0]):
        return False
    lanzador = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_INHERIT)
    if carpeta:
        lanzador.set_cwd(carpeta)
    try:
        proceso = lanzador.spawnv(argv)
    except GLib.Error:
        return False
    proceso.wait_async(None, None, None)  # se recoge al terminar, sin dejar zombis
    return True


# --- Papelera ------------------------------------------------------------------

# Solo las papeleras de discos que se montan para el usuario (USB, tarjetas): nunca /tmp, /dev/shm
# ni otros sitios en los que puede escribir cualquiera
MONTAJES_USUARIO = ("/media/", "/run/media/", "/mnt/")
SISTEMAS_PROHIBIDOS = {"tmpfs", "devtmpfs", "proc", "sysfs", "overlay", "squashfs", "ramfs"}


def _puntos_de_montaje():
    """Puntos de montaje de discos del usuario, leídos de /proc/self/mountinfo. Se llama desde
    hilos, y el monitor de volúmenes de GIO solo se puede usar desde el hilo principal."""
    puntos = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as f:
            for linea in f:
                campos = linea.split()
                if " - " not in linea or len(campos) < 5:
                    continue
                tipo = linea.split(" - ", 1)[1].split()[0]
                # Los espacios y raros vienen como \040: se deshacen los escapes octales
                punto = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), campos[4])
                if punto.startswith(MONTAJES_USUARIO) and tipo not in SISTEMAS_PROHIBIDOS:
                    puntos.append(punto)
    except OSError:
        pass
    return puntos


def _carpeta_mia(ruta):
    """True si ruta es una carpeta de verdad (no un enlace), del usuario y que solo él escribe."""
    try:
        st = os.lstat(ruta)
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == UID and not st.st_mode & 0o022


def _papelera_segura(papelera, compartida=None):
    """Comprobaciones de la especificación de la papelera (y algo más): la papelera, files/ e
    info/ son carpetas del usuario y no enlaces; .Trash compartida, carpeta con el bit sticky."""
    if compartida is not None:
        try:
            st = os.lstat(compartida)
        except OSError:
            return False
        if not stat.S_ISDIR(st.st_mode) or not st.st_mode & stat.S_ISVTX:
            return False
    return all(_carpeta_mia(r) for r in (papelera, os.path.join(papelera, "files"),
                                          os.path.join(papelera, "info")))


def _carpetas_papelera():
    """[(carpeta de la papelera, carpeta base de las rutas relativas o None)] que existen."""
    casa = os.path.join(GLib.get_user_data_dir(), "Trash")
    carpetas = [(casa, None)]
    for raiz in _puntos_de_montaje():
        compartida = os.path.join(raiz, ".Trash")
        for papelera, padre in ((os.path.join(compartida, str(UID)), compartida),
                                (os.path.join(raiz, f".Trash-{UID}"), None)):
            if _papelera_segura(papelera, padre):
                carpetas.append((papelera, raiz))
    return carpetas


def papelera_visible(ruta):
    """True si al tirar ruta a la papelera, ARCHBOX la volverá a ver: la de casa (mismo disco
    que ~/.local/share/Trash) o la de un disco de /media, /run/media o /mnt. GIO también usa
    .Trash-UID en /tmp o /var, pero esas no se listan (son de todos), así que no se usan."""
    try:
        real = os.path.realpath(ruta)
        disco = os.lstat(os.path.dirname(real) or "/").st_dev
        casa_datos = GLib.get_user_data_dir()
        while not os.path.exists(casa_datos) and casa_datos not in ("/", ""):
            casa_datos = os.path.dirname(casa_datos)  # la papelera aún no existe: su padre
        if real.startswith(GLib.get_home_dir().rstrip("/") + "/") and \
                disco == os.stat(casa_datos).st_dev:
            return True
    except OSError:
        return False
    return any(real.startswith(p.rstrip("/") + "/") for p in _puntos_de_montaje())


def destino_seguro(original, base):
    """Justo antes de restaurar en un disco: la carpeta sigue dentro del disco y nadie más que
    el usuario (o root) puede cambiar ninguna carpeta del camino, así que tampoco puede
    cambiarla por un enlace entre esta comprobación y el movimiento."""
    raiz = os.path.realpath(base)
    carpeta = os.path.dirname(original)
    if not os.path.realpath(carpeta).startswith(raiz.rstrip("/") + "/") and \
            os.path.realpath(carpeta) != raiz:
        return False
    actual = carpeta
    while actual not in ("/", "") and not os.path.lexists(actual):  # aún sin crear: su padre
        actual = os.path.dirname(actual)
    while True:
        try:
            st = os.lstat(actual)
        except OSError:
            return False
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return False
        if st.st_uid not in (UID, 0) or (st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX):
            return False
        if os.path.realpath(actual) == raiz or actual in ("/", ""):
            return True
        actual = os.path.dirname(actual)


def leer_trashinfo(ruta_info, base):
    """(ruta original, fecha de borrado en segundos) de un .trashinfo; (None, 0) si no vale."""
    original, fecha = None, 0
    try:
        with open(ruta_info, encoding="utf-8", errors="replace") as f:
            for linea in f:
                clave, _, valor = linea.strip().partition("=")
                if clave == "Path":
                    original = urllib.parse.unquote(valor)
                elif clave == "DeletionDate":
                    dt = GLib.DateTime.new_from_iso8601(valor, GLib.TimeZone.new_local())
                    fecha = dt.to_unix() if dt else 0
    except OSError:
        return None, 0
    if original and base:
        # Papelera de un disco: solo rutas relativas y que no salgan del disco (un USB o una
        # papelera falsa no puede «restaurar» en ~/.config ni en ningún otro sitio)
        if original.startswith("/"):
            return None, fecha
        original = os.path.normpath(os.path.join(base, original))
        dentro = os.path.realpath(base).rstrip("/") + "/"
        # realpath también deshace enlaces del disco que apunten fuera de él
        if not original.startswith(base.rstrip("/") + "/") or \
                not os.path.realpath(original).startswith(dentro):
            return None, fecha
    elif original and not original.startswith("/"):
        return None, fecha
    return original, fecha


class Elemento:
    """Un elemento de la papelera: el archivo guardado, su .trashinfo y de dónde vino."""

    def __init__(self, archivo, info, original, fecha, base=None):
        self.archivo, self.info, self.original, self.fecha = archivo, info, original, fecha
        self.base = base  # raíz del disco si es la papelera de un disco; None si es la de casa


def elementos_papelera():
    """Todos los elementos de las papeleras (lectura de disco: llamarla fuera del hilo de GTK)."""
    elementos = []
    for carpeta, base in _carpetas_papelera():
        archivos = os.path.join(carpeta, "files")
        try:
            nombres = os.listdir(archivos)
        except OSError:
            continue
        for nombre in nombres:
            info = os.path.join(carpeta, "info", nombre + ".trashinfo")
            original, fecha = leer_trashinfo(info, base)
            elementos.append(Elemento(os.path.join(archivos, nombre), info, original, fecha, base))
    return elementos


def buscar_en_papelera(original):
    """El último elemento tirado desde la ruta original (para deshacer «Mover a la papelera»)."""
    nombre = os.path.basename(original)
    mejor = None
    for carpeta, base in _carpetas_papelera():
        carpeta_info = os.path.join(carpeta, "info")
        try:
            candidatos = [n for n in os.listdir(carpeta_info) if n.startswith(nombre)]
        except OSError:
            continue
        for n in candidatos:
            ruta, fecha = leer_trashinfo(os.path.join(carpeta_info, n), base)
            archivo = os.path.join(carpeta, "files", n[:-len(".trashinfo")])
            if ruta == original and os.path.lexists(archivo) and (not mejor or fecha >= mejor.fecha):
                mejor = Elemento(archivo, os.path.join(carpeta_info, n), ruta, fecha, base)
    return mejor


def elemento_de(ruta_archivo):
    """Elemento de la papelera que corresponde a un archivo de una carpeta files/."""
    carpeta = os.path.dirname(os.path.dirname(ruta_archivo))
    for papelera, base in _carpetas_papelera():
        if os.path.realpath(papelera) == os.path.realpath(carpeta):
            info = os.path.join(papelera, "info", os.path.basename(ruta_archivo) + ".trashinfo")
            original, fecha = leer_trashinfo(info, base)
            return Elemento(ruta_archivo, info, original, fecha, base)
    return None


def carpetas_archivos_papelera():
    """Carpetas files/ de las papeleras, para vigilarlas."""
    return [os.path.join(c, "files") for c, _ in _carpetas_papelera()]


def papelera_vacia():
    return not any(os.listdir(c) for c in carpetas_archivos_papelera() if os.path.isdir(c))


# --- Plantillas ----------------------------------------------------------------

def plantillas():
    """[(nombre, ruta)] de ~/Plantillas (o la carpeta de plantillas de XDG), sin las ocultas."""
    carpeta = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_TEMPLATES)
    if not carpeta or os.path.realpath(carpeta) == os.path.realpath(GLib.get_home_dir()):
        carpeta = os.path.join(GLib.get_home_dir(), "Plantillas")
    try:
        nombres = sorted(os.listdir(carpeta), key=str.casefold)
    except OSError:
        return []
    return [(n, os.path.join(carpeta, n)) for n in nombres
            if not n.startswith(".") and os.path.isfile(os.path.join(carpeta, n))]
