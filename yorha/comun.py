"""Piezas comunes de barra.py, ajustes.py y lanzador.py: el tema guardado y los PID.

Sin GTK, para que la barra no cargue nada que no use.
"""
import os
import signal

RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/nonexistent")


def tema_actual():
    """Tema que dejó tema.sh en $XDG_RUNTIME_DIR/yorha-tema; claro si no hay ninguno."""
    try:
        with open(os.path.join(RUNTIME, "yorha-tema")) as f:
            return "oscuro" if f.read().strip() == "oscuro" else "claro"
    except OSError:
        return "claro"


def es_yorha():
    """True si la sesión de sway es la de YoRHa (su config es un enlace a yorha/sway.conf);
    False en el escritorio de trabajo (xfce/sway.conf). Lo usan ARCHBOX y el visor."""
    return os.path.realpath(os.path.expanduser("~/.config/sway/config")).endswith("/yorha/sway.conf")


def _pidfile(nombre):
    return os.path.join(RUNTIME, f"yorha-{nombre}.pid")


def _es_programa(pid, nombre):
    """True si pid es python ejecutando nombre.py (no un editor que tenga el archivo abierto)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        return False
    script = f"{nombre}.py".encode()
    return (b"python" in os.path.basename(argv[0])
            and any(os.path.basename(a) == script for a in argv[1:3]))


def apuntar_pid(nombre):
    try:
        with open(_pidfile(nombre), "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def soltar_pid(nombre):
    """Borra el pidfile al salir, solo si sigue siendo el de este proceso."""
    try:
        with open(_pidfile(nombre)) as f:
            if f.read().strip() == str(os.getpid()):
                os.remove(_pidfile(nombre))
    except OSError:
        pass


def alternar_instancia(nombre):
    """Si nombre.py ya está abierto lo cierra (SIGTERM) y devuelve True; si no, apunta este proceso."""
    try:
        with open(_pidfile(nombre)) as f:
            pid = int(f.read().strip())
        if pid != os.getpid() and _es_programa(pid, nombre):
            os.kill(pid, signal.SIGTERM)
            return True
    except (OSError, ValueError):
        pass
    apuntar_pid(nombre)
    return False
