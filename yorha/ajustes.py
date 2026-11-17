#!/usr/bin/env python3
"""Panel de ajustes YoRHa para sway, rehecho desde el menú de hyprland-yorha.

Uso: python3 -I ajustes.py [--seccion sound|wifi|bluetooth|screens|music|power]
Lo abre sway pulsando solo Mod (Mod+Escape lo abre en POWER, Mod+m en MUSIC y Mod+p en
pantallas); si ya está abierto, la misma orden lo cierra. Escape o un clic fuera de las columnas vuelve atrás; en
la primera columna, cierra el panel.

Columnas en cascada como el original: SYSTEM y APPS a la izquierda y cada
elección abre la siguiente. Solo vive mientras está abierto. Llama a pactl,
nmcli, bluetoothctl, systemctl y swaymsg con listas de argumentos (sin shell),
siempre fuera del hilo de la interfaz, y nunca pide ni guarda contraseñas: si
una red necesita clave, nmcli la pide en una terminal. La música se controla
por D-Bus (MPRIS) con Gio, sin playerctl.

Aquí solo está el menú (columnas, páginas y el modo Harmony al lado): las piezas NieR
(botones, deslizadores, la capa Pantalla) están en piezas.py y las órdenes del sistema en
sistema.py, que comparten el buscador, el panel de trabajo y ARCHBOX.
"""
import argparse
import os
import random
import subprocess
import sys
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GLib, Gtk, GtkLayerShell  # noqa: E402

CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CARPETA)  # con python3 -I la carpeta del script no está en sys.path
import comun  # noqa: E402
from piezas import Boton, Deslizador, Pantalla, aviso, ejecutar, titulo  # noqa: E402
from sistema import (alternar_bluetooth, alternar_silencio, alternar_wifi, altura_barra,  # noqa: E402
                     apps_con_sonido, bluetooth_activado, conectado_bluetooth, dispositivos_bluetooth,
                     en_segundo_plano, es_interna, lanzar, mandar_reproductor, orden, pantallas,
                     poner_volumen, redes_wifi, reproductores, volumen, wifi_activado)

COLUMNAS = 4
CELDAS = (16, 9)      # cuadrícula NieR que se enciende detrás de las columnas al abrir
TERMINAL = ["sh", os.path.join(CARPETA, "terminal.sh")]
ESTADOS_MUSICA = {"Playing": "Sonando", "Paused": "En pausa", "Stopped": "Parado"}


def si_no(valor):
    return "Sí" if valor else "No"


# --- Panel ------------------------------------------------------------------

class Columna(Gtk.ScrolledWindow):
    def __init__(self, numero):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.get_style_context().add_class("columna")
        self.get_style_context().add_class(f"columna{numero}")
        self.contenido = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.contenido.get_style_context().add_class("contenido")
        self.add(self.contenido)
        self.contenido.set_focus_vadjustment(self.get_vadjustment())
        self.elegido = None
        self.abierta = False

    def llenar(self, piezas):
        for hijo in self.contenido.get_children():
            self.contenido.remove(hijo)
        for pieza in piezas:
            padre = pieza.get_parent()  # los botones de apps se reutilizan entre páginas
            if padre:
                padre.remove(pieza)
            self.contenido.pack_start(pieza, False, False, 0)
        self.contenido.show_all()
        self.get_vadjustment().set_value(0)

    def elegir(self, boton):
        if self.elegido:
            self.elegido.marcar("elegido", False)
        self.elegido = boton
        if boton:
            boton.marcar("elegido", True)

    def abrir(self, si):
        """Una columna cerrada no admite clics y se vacía al acabar la animación."""
        self.abierta = si
        self.set_sensitive(si)
        ctx = self.get_style_context()
        (ctx.remove_class if si else ctx.add_class)("cerrada")
        if not si:
            self.elegir(None)
            GLib.timeout_add(400, lambda: (self.abierta or self.llenar([])) and False)


class Panel(Pantalla):
    def __init__(self, tema, seccion):
        super().__init__("ajustes", "ajustes", tema)
        # Tapa también la barra de escritorios: mientras el menú está abierto no se cambia de
        # escritorio (zona -1: no respeta el hueco que reserva la barra)
        GtkLayerShell.set_exclusive_zone(self, -1)
        # Tamaño provisional; se corrige con el de la pantalla donde sway abra el panel
        monitor = Gdk.Display.get_default().get_monitor(0).get_geometry()
        self.ancho, self.alto = monitor.width, monitor.height
        self.pantalla = (monitor.width, monitor.height)
        self.foco_pendiente = None
        self.origen = (monitor.x, monitor.y)
        self.lateral = False  # menú estrecho a la izquierda y Harmony Music a su derecha
        self.estrecho = False  # el panel aún no ha recuperado el ancho de la pantalla
        self.tapa = None  # franja que tapa la barra a la derecha del menú lateral
        self.estilo_tapa = ""
        self.columnas = [Columna(i + 1) for i in range(COLUMNAS)]
        self.medir(self.ancho, self.alto)
        self.connect("size-allocate", lambda _w, a: self.medir(a.width, a.height))

        fila = Gtk.Box(spacing=0)
        for c in self.columnas:
            fila.pack_start(c, False, False, 0)
            c.abrir(False)
        fondo = Gtk.EventBox()
        fondo.add(fila)
        fondo.connect("button-release-event", self.al_clic_fondo)
        capas = Gtk.Overlay()
        capas.add(self.cuadricula())
        capas.add_overlay(fondo)
        capas.add_overlay(self.barra_ayuda((("↑↓", "Seleccionar"), ("Enter", "Confirmar"),
                                            ("Esc", "Atrás"))))
        self.add(capas)

        self.abiertas = 1
        self.seccion = seccion
        self.columnas[0].llenar(self.pagina_principal())
        GLib.timeout_add_seconds(10, self.actualizar_hora)

    def cuadricula(self):
        """Celdas que se encienden al azar al abrir y se quedan tenues, como el fondo del original."""
        self.celdas = Gtk.Grid(row_homogeneous=True, column_homogeneous=True)
        self.celdas.get_style_context().add_class("cuadricula")
        for fila in range(CELDAS[1]):
            for col in range(CELDAS[0]):
                celda = Gtk.Box(hexpand=True, vexpand=True)
                celda.get_style_context().add_class("celda")
                celda.get_style_context().add_class(f"d{random.randrange(8)}")
                self.celdas.attach(celda, col, fila, 1, 1)
        return self.celdas

    def enfocar_primero(self, contenedor):
        """Lleva el cursor a lo primero seleccionable de contenedor (solo si se usa el teclado)."""
        if not self.por_teclado:
            return True
        pendientes = [contenedor]
        while pendientes:
            w = pendientes.pop(0)
            if w.get_can_focus() and w.get_visible() and w.get_sensitive() \
                    and isinstance(w, (Boton, Gtk.Button)):
                w.grab_focus()
                return True
            if isinstance(w, Gtk.Container):
                pendientes = w.get_children() + pendientes
        return False

    def entrar(self):
        """Animación de apertura: fondo, cuadrícula y primera columna deslizándose."""
        super().entrar()
        self.celdas.get_style_context().add_class("encendida")
        GLib.timeout_add(700, lambda: self.celdas.get_style_context().add_class("asentada") and False)
        self.columnas[0].abrir(True)
        primero = self.botones_seccion.get("sound")
        if primero and not self.seccion:
            primero.grab_focus()
        if self.seccion:
            boton = self.botones_seccion.get(self.seccion)
            if boton:
                GLib.timeout_add(150, lambda: boton.al_pulsar(boton) and False)
        return False

    def medir(self, ancho, alto):
        """Ajusta columnas, letra y fondo al tamaño real de la pantalla."""
        if self.estrecho:  # el ancho del menú lateral no cambia el tamaño de las columnas
            if ancho < self.pantalla[0]:
                return
            self.estrecho = False
        if getattr(self, "medido", None) == (ancho, alto) or ancho < 100 or alto < 100:
            return
        self.medido = (ancho, alto)
        self.ancho, self.alto = ancho, alto
        ventana = self.get_window()
        if ventana:
            g = Gdk.Display.get_default().get_monitor_at_window(ventana).get_geometry()
            self.pantalla = (g.width, g.height)
            self.origen = (g.x, g.y)
        # Mismo tamaño de letra que cuando el menú dejaba ver la barra
        self.e = (alto - altura_barra()) / 1080
        self.ancho_columna = ancho // COLUMNAS
        for c in self.columnas:
            c.set_size_request(self.ancho_columna, -1)
        self.aplicar_tema()

    # Tema y estilo
    def aplicar_tema(self):
        fondo = os.path.join(CARPETA, "fondos", f"yorha-{self.tema}.png")
        # El fondo del escritorio, desplazado lo que ocupa la barra, para que case con él
        arriba = max(0, self.pantalla[1] - self.alto)
        extra = (f"window.ajustes {{ background-image: url('file://{fondo}'); "
                 f"background-size: {self.pantalla[0]}px {self.pantalla[1]}px; "
                 f"background-position: 0 -{arriba}px; }}\n"
                 f".boton label, .deslizador label {{ font-size: {round(26 * self.e)}px; }}\n"
                 f".boton.grande label {{ font-size: {round(30 * self.e)}px; }}\n"
                 f".hora {{ font-size: {round(56 * self.e)}px; }}\n" + self.estilo_tapa)
        self.cargar_estilo(["ajustes.css"], self.e, extra)

    # Navegación entre columnas
    def ir_a(self, nivel, piezas, boton, enfocar=True):
        """Abre la columna nivel+1 con piezas, marcando el botón que la abrió.

        Con enfocar=False el cursor se queda donde está (al escribir en el buscador)."""
        if self.lateral and not (nivel == 1 and boton is self.botones_seccion.get("music")):
            self.salir_lateral()  # otra sección: Harmony se esconde y el menú vuelve a ser ancho
        self.columnas[nivel - 1].elegir(boton)
        for c in self.columnas[nivel + 1:]:
            c.abrir(False)
        self.columnas[nivel].llenar(piezas)
        self.columnas[nivel].abrir(True)
        self.abiertas = nivel + 1
        contenido = self.columnas[nivel].contenido
        # Si la página aún está cargando, el cursor entra en ella cuando termine
        if not enfocar:
            self.foco_pendiente = None
            return
        self.foco_pendiente = None if self.enfocar_primero(contenido) else contenido

    def atras(self):
        if self.lateral:  # Esc con Harmony a la vista: se esconde y queda la página Música
            self.salir_lateral()
            self.ir_a(1, self.pagina_musica(), self.botones_seccion["music"])
            return
        if self.abiertas <= 1:
            self.cerrar()
            return
        self.columnas[self.abiertas - 1].abrir(False)
        anterior = self.columnas[self.abiertas - 2]
        if anterior.elegido:
            anterior.elegido.grab_focus()  # el cursor vuelve al botón que abrió la columna
        anterior.elegir(None)
        self.abiertas -= 1

    def cerrar(self, *_):
        """Animación de cierre: las columnas se recogen y el fondo se apaga; luego termina."""
        if not self.saliendo:
            if self.lateral:
                lanzar(["sh", os.path.join(CARPETA, "harmony.sh"), "ocultar"])
            for c in reversed(self.columnas):
                c.abrir(False)
            self.celdas.get_style_context().remove_class("encendida")
        return super().cerrar(320)

    def al_teclear(self, tecla):
        # La flecha izquierda vuelve atrás, salvo dentro del buscador
        if tecla == Gdk.KEY_Left and not isinstance(self.get_focus(), Gtk.Entry):
            self.atras()
            return True
        return False

    def al_clic_fondo(self, _w, evento):
        # x_root es relativa a la superficie del panel, que ocupa toda la pantalla
        if evento.button == Gdk.BUTTON_PRIMARY and evento.x_root > self.ancho_columna * self.abiertas:
            self.atras()
        return False

    # Harmony Music al lado del menú: el panel deja de tapar la pantalla entera y se queda
    # con sus dos primeras columnas; la ventana de Harmony ocupa el resto, alineada con las
    # fichas. Una ventana normal no puede ir por encima del panel (es una capa), por eso
    # el panel se estrecha en vez de abrirla encima.
    def entrar_lateral(self):
        ancho_menu = 2 * self.ancho_columna
        arriba = altura_barra()
        x, y = self.origen[0] + ancho_menu, self.origen[1] + arriba
        ancho, alto = self.ancho - ancho_menu - 24, self.alto - arriba - 150
        self.tapar_barra(ancho_menu, arriba)
        self.lateral = self.estrecho = True
        for c in self.columnas[2:]:
            c.abrir(False)
            c.hide()
        # Con el teclado solo para el panel no se podría escribir en Harmony: así se
        # escribe en la ventana en la que se haga clic
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, False)
        self.set_size_request(ancho_menu, -1)  # las columnas van en una capa del Overlay y no cuentan
        self.resize(ancho_menu, self.alto)
        lanzar(["sh", os.path.join(CARPETA, "harmony.sh"), "lateral",
                str(x), str(y), str(ancho), str(alto)])

    def tapar_barra(self, ancho_menu, arriba):
        """A la derecha del menú estrecho la barra volvería a verse: la tapa una franja con
        el mismo fondo, para que no se pueda cambiar de escritorio con el menú abierto."""
        if self.tapa or arriba <= 0:
            return
        self.tapa = Gtk.Window(title="ajustes-tapa-yorha")
        self.tapa.get_style_context().add_class("tapa")
        GtkLayerShell.init_for_window(self.tapa)
        GtkLayerShell.set_namespace(self.tapa, "yorha-ajustes")
        GtkLayerShell.set_layer(self.tapa, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_keyboard_mode(self.tapa, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_exclusive_zone(self.tapa, -1)
        GtkLayerShell.set_anchor(self.tapa, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self.tapa, GtkLayerShell.Edge.RIGHT, True)
        self.tapa.set_size_request(self.pantalla[0] - ancho_menu, arriba)
        self.estilo_tapa = (f"window.tapa {{ background-color: @tan; background-image: "
                            f"url('file://{os.path.join(CARPETA, 'fondos', f'yorha-{self.tema}.png')}'); "
                            f"background-size: {self.pantalla[0]}px {self.pantalla[1]}px; "
                            f"background-position: -{ancho_menu}px 0; }}\n")
        self.aplicar_tema()
        self.tapa.show_all()

    def salir_lateral(self, ocultar=True):
        self.lateral = False
        if self.tapa:
            self.tapa.destroy()
            self.tapa = None
            self.estilo_tapa = ""
        for c in self.columnas[2:]:
            c.show()
        self.set_size_request(-1, -1)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        if ocultar:
            lanzar(["sh", os.path.join(CARPETA, "harmony.sh"), "ocultar"])

    def cargando(self, nombre, trabajo, construir, nivel=2):
        """Página que muestra Cargando... y se rellena cuando trabajo() termina."""
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.pack_start(aviso("Cargando..."), False, False, 0)

        def mostrar(resultado):
            for hijo in caja.get_children():
                caja.remove(hijo)
            for pieza in construir(resultado):
                caja.pack_start(pieza, False, False, 0)
            caja.show_all()
            if self.foco_pendiente and caja.is_ancestor(self.foco_pendiente):
                self.foco_pendiente = None
                self.enfocar_primero(caja)

        en_segundo_plano(trabajo, mostrar)
        return [titulo(nombre, nivel), caja]

    # Columna 1: SYSTEM, APPS, hora y tema
    def pagina_principal(self):
        self.botones_seccion = {}
        piezas = [titulo("SISTEMA")]
        lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lista.get_style_context().add_class("lista")
        for clave, texto, pagina, ayuda in (
                ("sound", "Sonido", self.pagina_sonido,
                 "Volumen del altavoz, del micrófono y de cada aplicación."),
                ("wifi", "Wi-Fi", self.pagina_wifi, "Activar el Wi-Fi y conectarse a una red."),
                ("bluetooth", "Bluetooth", self.pagina_bluetooth,
                 "Activar el Bluetooth y conectar los dispositivos emparejados."),
                ("screens", "Pantallas", self.pagina_pantallas,
                 "Monitor externo o proyector: extender el escritorio o usar solo uno."),
                ("music", "Música", self.pagina_musica, "Controlar el reproductor que esté abierto."),
                ("power", "Energía", self.pagina_energia,
                 "Bloquear, suspender, cerrar la sesión, reiniciar o apagar.")):
            boton = Boton(self, texto, lambda b, p=pagina: self.ir_a(1, p(), b), grande=True,
                          ayuda=ayuda)
            self.botones_seccion[clave] = boton
            lista.pack_start(boton, False, False, 0)
        piezas.append(lista)
        # Las aplicaciones están en Super+Tab; abajo del todo, la hora y el tema
        pie = Gtk.Box(spacing=20, vexpand=True, valign=Gtk.Align.END)
        pie.get_style_context().add_class("pie")
        self.hora = Gtk.Label(xalign=0)
        self.hora.get_style_context().add_class("hora")
        self.actualizar_hora()
        pie.pack_start(self.hora, False, False, 0)
        pie.pack_start(Boton(self, "Oscuro" if self.tema == "claro" else "Claro", self.cambiar_tema,
                             ayuda="Cambiar entre el tema claro y el oscuro."), True, True, 0)
        piezas.append(pie)
        return piezas

    def actualizar_hora(self):
        self.hora.set_text(time.strftime("%H:%M"))
        return True

    def cambiar_tema(self, boton):
        def fin(_r):
            self.tema = comun.tema_actual()
            self.aplicar_tema()
            for c in self.columnas[1:]:
                c.abrir(False)
            self.abiertas = 1
            self.columnas[0].llenar(self.pagina_principal())
        boton.esperar(lambda: orden(["sh", os.path.join(CARPETA, "tema.sh")]), fin)

    def interruptor(self, texto, inicial, alternar):
        """Botón Yes/No que cambia el estado en segundo plano y muestra el resultado real."""
        def pulsar(boton):
            boton.esperar(alternar, lambda r: boton.poner_valor("?" if r is None else si_no(r)))
        return Boton(self, texto, pulsar, valor=si_no(inicial), ayuda="Cambia entre Sí y No.")

    # Columna 2: secciones
    def pagina_sonido(self):
        def construir(estado):
            (vol_sink, mudo_sink), (vol_source, mudo_source) = estado
            if vol_sink is None:
                return [aviso("pactl no responde.")]
            piezas = [Deslizador("Altavoz", vol_sink, lambda v: poner_volumen("sink", v),
                                 mudo_sink, lambda: alternar_silencio("sink"))]
            if vol_source is not None:
                piezas.append(Deslizador("Micrófono", vol_source, lambda v: poner_volumen("source", v),
                                         mudo_source, lambda: alternar_silencio("source")))
            piezas.append(Boton(self, "Aplicaciones", ayuda="Volumen de cada programa que suena.", al_pulsar=
                                lambda b: self.ir_a(2, self.pagina_apps_sonido(), b)))
            return piezas
        return self.cargando("VOLUMEN", lambda: (volumen("sink"), volumen("source")), construir)

    def pagina_apps_sonido(self):
        def construir(apps):
            if not apps:
                return [aviso("Ningún programa está sonando.")]
            return [Deslizador(nombre, vol, lambda v, i=indice: orden(
                ["pactl", "set-sink-input-volume", i, f"{int(v)}%"]))
                for indice, nombre, vol in apps]
        return self.cargando("APLICACIONES", apps_con_sonido, construir)

    def pagina_wifi(self):
        def construir(activado):
            if activado is None:
                return [aviso("NetworkManager (nmcli) no está disponible.")]
            return [self.interruptor("Activado", activado, alternar_wifi),
                    Boton(self, "Conectar", lambda b: self.ir_a(2, self.pagina_redes(), b),
                          ayuda="Buscar redes cercanas.")]
        return self.cargando("WIFI", wifi_activado, construir)

    def pagina_redes(self):
        def construir(redes):
            if redes is None:
                return [aviso("nmcli no responde.")]
            if not redes:
                return [aviso("No se han encontrado redes.")]
            return [Boton(self, ssid, lambda b, s=ssid: self.conectar_wifi(b, s),
                          valor="●" if en_uso else f"{senal}%")
                    for ssid, senal, en_uso in redes]
        return self.cargando("REDES", redes_wifi, construir, nivel=2)

    def conectar_wifi(self, boton, ssid):
        """Prueba a conectar sin pedir nada (redes abiertas o ya guardadas en NetworkManager).

        Si hace falta la clave, abre una terminal donde nmcli la pide.
        """
        def fin(salida):
            if salida is None:
                lanzar(TERMINAL + ["-T", "Wi-Fi", "nmcli", "--ask", "device", "wifi", "connect", ssid])
            self.cerrar()
        boton.esperar(lambda: orden(["nmcli", "device", "wifi", "connect", ssid], tiempo=45), fin)

    def pagina_bluetooth(self):
        def construir(activado):
            if activado is None:
                return [aviso("bluetoothctl no está disponible, no hay adaptador "
                              "o este usuario no está en el grupo bluetooth.")]
            return [self.interruptor("Activado", activado, alternar_bluetooth),
                    Boton(self, "Dispositivos", lambda b: self.ir_a(2, self.pagina_dispositivos(), b)),
                    Boton(self, "Emparejar nuevo", ayuda="Abre bluetoothctl en una terminal.",
                          al_pulsar=lambda b: (lanzar(TERMINAL + ["-T", "Bluetooth",
                                                                          "bluetoothctl"]),
                                                       self.cerrar()))]
        return self.cargando("BLUETOOTH", bluetooth_activado, construir)

    def pagina_dispositivos(self):
        def alternar(boton, mac):
            def trabajo():
                accion = "disconnect" if conectado_bluetooth(mac) else "connect"
                orden(["bluetoothctl", accion, mac], tiempo=20)
                return conectado_bluetooth(mac)
            boton.esperar(trabajo, lambda c: boton.poner_valor("Conectado" if c else "Desconectado"))

        def construir(lista):
            if lista is None:
                return [aviso("bluetoothctl no responde.")]
            if not lista:
                return [aviso("No hay dispositivos emparejados.")]
            return [Boton(self, nombre, lambda b, m=mac: alternar(b, m),
                          valor="Conectado" if conectado else "Desconectado")
                    for mac, nombre, conectado in lista]
        return self.cargando("DISPOSITIVOS", dispositivos_bluetooth, construir)

    def pagina_pantallas(self):
        """Cada pantalla abre sus opciones."""
        def construir(lista):
            if lista is None:
                return [aviso("swaymsg no responde.")]
            piezas = [Boton(self, nombre if es_interna(nombre) else modelo or nombre,
                            lambda b, n=nombre: self.ir_a(2, self.pagina_pantalla(n, lista), b),
                            valor="Activa" if activa else "Apagada",
                            ayuda=f"{nombre}: {modelo or 'pantalla'}")
                      for nombre, modelo, activa, _ in lista]
            if len(lista) < 2:
                piezas.append(aviso("Conecta un monitor o un proyector para ver más opciones."))
            return piezas
        return self.cargando("PANTALLAS", pantallas, construir)

    def pagina_pantalla(self, nombre, lista):
        """Opciones de una pantalla. Nunca deja todas apagadas (Mod+Shift+p las enciende)."""
        anchos = {n: ancho for n, _, _, ancho in lista}
        activas = [n for n, _, activa, _ in lista if activa]
        otras = [n for n in anchos if n != nombre]
        opciones = []
        if otras:
            vecina = next((n for n in otras if es_interna(n)), otras[0])
            opciones += [
                ("Extender a la derecha", f"Esta pantalla a la derecha de {vecina}.",
                 [f"output {vecina} enable position 0 0",
                  f"output {nombre} enable position {anchos[vecina]} 0"]),
                ("Extender a la izquierda", f"Esta pantalla a la izquierda de {vecina}.",
                 [f"output {nombre} enable position 0 0",
                  f"output {vecina} enable position {anchos[nombre]} 0"]),
                ("Solo esta pantalla", "Apaga las demás. Mod+Shift+p las vuelve a encender.",
                 [f"output {nombre} enable position 0 0"] + [f"output {n} disable" for n in otras]),
            ]
        if nombre in activas and len(activas) > 1:
            opciones.append(("Apagar", "Apaga esta pantalla; las demás siguen.", [f"output {nombre} disable"]))
        if nombre not in activas:
            opciones.append(("Encender", "Enciende esta pantalla.", [f"output {nombre} enable"]))

        def aplicar(ordenes):
            # El panel se cierra antes: al recolocar pantallas sway puede cerrar la capa
            # sobre la que está dibujado. swaymsg va aparte y termina aunque el panel salga
            self.cerrar()
            lanzar(["swaymsg", "; ".join(ordenes)])

        return [titulo(nombre, 2)] + [Boton(self, texto, lambda _b, o=ordenes: aplicar(o), ayuda=ayuda)
                                      for texto, ayuda, ordenes in opciones]

    def pagina_musica(self):
        def trabajo():
            abierta = subprocess.run(["pgrep", "-u", str(os.getuid()), "-x", "harmonymusic"],
                                     stdout=subprocess.DEVNULL).returncode == 0
            return reproductores(), abierta

        def construir(resultado):
            lista, abierta = resultado or (None, False)
            piezas = self.piezas_harmony(abierta)
            if lista is None:
                return piezas + [aviso("No hay bus de sesión D-Bus.")]
            if not lista:
                return piezas + [aviso("No hay ningún reproductor abierto.")]
            for bus, cancion, artista, estado in lista:
                piezas += self.piezas_reproductor(bus, cancion, artista, estado)
            return piezas
        return self.cargando("MÚSICA", trabajo, construir)

    def rehacer_musica(self):
        """Vuelve a construir la página de música (cambió Harmony Music o el modo lateral)."""
        self.ir_a(1, self.pagina_musica(), self.botones_seccion["music"])

    def piezas_harmony(self, abierta):
        """Harmony Music va en esta misma ficha y se abre a la derecha del menú."""
        def mostrar(_b):
            self.entrar_lateral()
            self.rehacer_musica()

        def ocultar(_b):
            self.salir_lateral()
            self.rehacer_musica()

        def cerrar_harmony(boton):
            if self.lateral:
                self.salir_lateral(ocultar=False)
            boton.esperar(lambda: subprocess.run(["sh", os.path.join(CARPETA, "harmony.sh"),
                                                  "cerrar"]), lambda _r: self.rehacer_musica())

        nombre = Gtk.Label(label="Harmony Music", xalign=0)
        nombre.get_style_context().add_class("cancion")
        if not abierta and not self.lateral:  # (si está a la vista, quizá aún arrancando)
            return [nombre, Boton(self, "Abrir", mostrar,
                                  ayuda="Harmony Music se abre a la derecha del menú, "
                                        "sin ocupar un escritorio.")]
        if self.lateral:
            vista = Boton(self, "Ocultar", ocultar,
                          ayuda="Esconde Harmony Music; la música sigue sonando. También con Esc.")
        else:
            vista = Boton(self, "Mostrar", mostrar,
                          ayuda="Harmony Music a la derecha del menú, sin ocupar un escritorio.")
        return [nombre, vista, Boton(self, "Cerrar Harmony", cerrar_harmony,
                                     ayuda="Cierra Harmony Music y para la música.")]

    def piezas_reproductor(self, bus, cancion, artista, estado):
        """Título, artista y botones de un reproductor de la lista de MPRIS."""
        nombre = Gtk.Label(label=cancion, xalign=0)
        nombre.set_line_wrap(True)
        nombre.get_style_context().add_class("cancion")
        piezas = [nombre]
        if artista:
            piezas.append(aviso(artista))
        piezas.append(Boton(self, "Reproducir / Pausa",
                            lambda b: self.cambiar_musica(b, bus, "PlayPause"),
                            valor=ESTADOS_MUSICA.get(estado, estado)))
        piezas.append(Boton(self, "Anterior", lambda b: self.cambiar_musica(b, bus, "Previous")))
        piezas.append(Boton(self, "Siguiente", lambda b: self.cambiar_musica(b, bus, "Next")))
        return piezas

    def cambiar_musica(self, boton, bus, metodo):
        """Manda la orden al reproductor sin bloquear y enseña el resultado al volver."""
        def fin(estado):
            if metodo == "PlayPause":
                boton.poner_valor(ESTADOS_MUSICA.get(estado, estado or "?"))
            else:  # canción nueva: se rehace la página con el título actual
                musica = self.botones_seccion["music"]
                if self.columnas[1].abierta and self.columnas[0].elegido is musica:
                    self.ir_a(1, self.pagina_musica(), musica)
        boton.esperar(lambda: mandar_reproductor(bus, metodo), fin)

    def pagina_energia(self):
        acciones = (("Bloquear", ["sh", os.path.join(CARPETA, "bloquear.sh")], False),
                    ("Suspender", ["systemctl", "suspend"], True),
                    ("Cerrar sesión", ["swaymsg", "exit"], True),
                    ("Reiniciar", ["systemctl", "reboot"], True),
                    ("Apagar", ["systemctl", "poweroff"], True))
        piezas = [titulo("ENERGÍA", 2)]
        for texto, argv, confirmar in acciones:
            if confirmar:
                al_pulsar = (lambda b, t=texto, a=argv: self.ir_a(2, self.pagina_confirmar(t, a), b))
            else:
                al_pulsar = (lambda b, a=argv: (self.cerrar(), lanzar(a)))
            piezas.append(Boton(self, texto, al_pulsar))
        return piezas

    def pagina_confirmar(self, texto, argv):
        def si(_b):
            self.cerrar()
            lanzar(argv)
        return [titulo("¿SEGURO?", 2),
                aviso(f"{texto}."),
                Boton(self, "Sí", si),
                Boton(self, "No", lambda b: self.atras())]


def main():
    parser = argparse.ArgumentParser(description="Panel de ajustes YoRHa para sway")
    parser.add_argument("--seccion", choices=["sound", "wifi", "bluetooth", "screens", "music", "power"])
    args = parser.parse_args()
    ejecutar("ajustes", lambda tema: Panel(tema, args.seccion))


if __name__ == "__main__":
    main()
