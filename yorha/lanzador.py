#!/usr/bin/env python3
"""Buscador de aplicaciones YoRHa (Mod+Tab): solo aplicaciones, sin el panel de ajustes.

Una ficha en el centro, como el inventario del juego: a la izquierda el buscador y
la lista; a la derecha, la aplicación seleccionada con su icono, descripción y
categoría. Escribir filtra, ↑↓ eligen, Enter abre y Esc (o un clic fuera) cierra.
Si ya está abierto, la misma orden lo cierra. Usa las piezas de piezas.py, las
aplicaciones de sistema.py y el estilo de ajustes.css, y solo vive mientras está abierto.
"""
import bisect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python3 -I no la añade
import piezas  # noqa: E402
import sistema  # noqa: E402
from piezas import GLib, Gtk, titulo  # noqa: E402

MAXIMO = 60  # aplicaciones en la lista a la vez


class Buscador(piezas.Pantalla):
    def __init__(self, tema):
        super().__init__("lanzador", "buscador", tema)
        pantalla = piezas.Gdk.Display.get_default().get_monitor(0).get_geometry()
        e = pantalla.height / 1080
        self.cargar_estilo(["ajustes.css", "lanzador.css"], e,
                           f".detalle .texto {{ font-size: {round(20 * e)}px; }}\n")

        # Ficha central de ancho fijo (60 % de la pantalla, mitad lista y mitad detalle):
        # son los textos los que se ajustan dentro, la ficha no cambia según lo seleccionado
        ficha = Gtk.Box(spacing=0, homogeneous=True)
        ficha.get_style_context().add_class("ficha")
        lado = round(pantalla.width * 0.2)
        ficha.set_margin_start(lado)
        ficha.set_margin_end(lado)
        ficha.set_valign(Gtk.Align.CENTER)
        ficha.set_size_request(-1, round(pantalla.height * 0.62))
        izquierda = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        izquierda.pack_start(titulo("APLICACIONES"), False, False, 0)
        self.buscar = Gtk.Entry(placeholder_text="escribe para buscar")
        self.buscar.get_style_context().add_class("buscar")
        izquierda.pack_start(self.buscar, False, False, 0)
        desplazable = Gtk.ScrolledWindow()
        desplazable.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.lista = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.lista.get_style_context().add_class("lista-apps")
        # Cada botón se muestra u oculta al filtrar: que show_all() no los muestre todos
        self.lista.set_no_show_all(True)
        self.lista.show()
        desplazable.add(self.lista)
        self.lista.set_focus_vadjustment(desplazable.get_vadjustment())
        izquierda.pack_start(desplazable, True, True, 0)
        ficha.pack_start(izquierda, True, True, 0)
        ficha.pack_start(self.detalle(), True, True, 0)

        fondo = Gtk.EventBox()  # un clic fuera de la ficha cierra
        fondo.connect("button-release-event",
                      lambda _w, ev: ev.button == piezas.Gdk.BUTTON_PRIMARY and self.cerrar())
        capas = Gtk.Overlay()
        capas.add(fondo)
        capas.add_overlay(ficha)
        capas.add_overlay(self.barra_ayuda((("↑↓", "Seleccionar"), ("Enter", "Abrir"),
                                            ("Esc", "Cerrar"))))
        self.add(capas)

        self.apps = sistema.aplicaciones()
        self.indice = {id(a): i for i, a in enumerate(self.apps)}
        # Los botones se crean la primera vez que hacen falta y luego solo se ocultan o
        # muestran: filtrar no rehace la lista. creados va en el orden de self.apps.
        self.botones = {}
        self.creados = []
        self.encontradas = []
        self.pendiente = None
        self.buscar.connect("changed", self.al_escribir)
        self.buscar.connect("activate", self.abrir_primera)
        self.filtrar()

    def detalle(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.get_style_context().add_class("detalle")
        self.nombre = titulo("", 2)
        self.nombre.set_ellipsize(3)
        self.nombre.set_max_width_chars(30)  # textos largos no ensanchan la ficha
        self.icono = Gtk.Image()
        self.icono.set_pixel_size(96)
        self.icono.set_halign(Gtk.Align.START)
        self.icono.get_style_context().add_class("icono")
        self.descripcion = Gtk.Label(xalign=0)
        self.descripcion.set_line_wrap(True)
        self.descripcion.set_max_width_chars(45)
        self.descripcion.get_style_context().add_class("texto")
        self.tipo = Gtk.Label(xalign=0)
        self.tipo.get_style_context().add_class("texto")
        self.tipo.get_style_context().add_class("tipo")
        for w in (self.nombre, self.icono, self.descripcion, self.tipo):
            caja.pack_start(w, False, False, 0)
        return caja

    def describir(self, boton):
        super().describir(boton)
        self.mostrar(boton.dato)

    def mostrar(self, app):
        """Pone en la ficha de la derecha la aplicación seleccionada."""
        self.nombre.set_text(app.get_display_name() if app else "")
        icono = app.get_icon() if app else None
        if icono:
            self.icono.set_from_gicon(icono, Gtk.IconSize.DIALOG)
            self.icono.set_pixel_size(96)
        else:
            self.icono.clear()
        self.descripcion.set_text((app.get_description() or "Sin descripción.") if app
                                  else "No hay ninguna aplicación con ese nombre.")
        self.tipo.set_text(f"Categoría: {sistema.categoria(app)}" if app else "")

    def al_escribir(self, *_):
        # Espera a que se deje de teclear para no filtrar en cada letra
        if self.pendiente:
            GLib.source_remove(self.pendiente)
        self.pendiente = GLib.timeout_add(120, self.filtrar)

    def boton(self, indice):
        """Botón de self.apps[indice]; si aún no existe, se crea en su sitio de la lista."""
        boton = self.botones.get(indice)
        if not boton:
            app = self.apps[indice]
            boton = self.botones[indice] = piezas.nuevo_boton_app(self, app, self.abrir)
            sitio = bisect.bisect(self.creados, indice)
            self.creados.insert(sitio, indice)
            self.lista.pack_start(boton, False, False, 0)
            self.lista.reorder_child(boton, sitio)
            boton.show_all()
        return boton

    def filtrar(self, *_):
        self.pendiente = None
        texto = self.buscar.get_text().lower().strip()
        self.encontradas = sistema.buscar_apps(self.apps, texto, MAXIMO)
        visibles = {self.indice[id(a)] for a in self.encontradas}
        for i in visibles:
            self.boton(i).show()
        for i, boton in self.botones.items():
            if i not in visibles:
                boton.hide()
        self.mostrar(self.encontradas[0] if self.encontradas else None)
        return False

    def abrir_primera(self, *_):
        if self.pendiente:  # Enter antes de que acabe la espera: se filtra ya
            GLib.source_remove(self.pendiente)
            self.filtrar()
        if self.encontradas:
            self.abrir(self.encontradas[0])

    def abrir(self, app):
        app.launch([], None)
        self.cerrar()

    def entrar(self):
        self.buscar.grab_focus()
        return super().entrar()


if __name__ == "__main__":
    piezas.ejecutar("lanzador", Buscador)
