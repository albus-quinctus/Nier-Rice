#!/usr/bin/env python3
"""Fondo YoRHa (claro y oscuro) inspirado en hyprland-yorha, dibujado desde cero.

Uso: python3 -I fondo.py CARPETA [ANCHO ALTO]
Crea CARPETA/yorha-claro.png y CARPETA/yorha-oscuro.png, y los punteros de la
barra en CARPETA/puntero-claro.png y CARPETA/puntero-oscuro.png.

Dibuja arcos dobles, cables diagonales, una cuadrícula fina cada 10 px y el
oscurecido de los laterales. En el original eso último lo hacía un shader en
cada fotograma; aquí se calcula una sola vez y queda en la imagen.
"""
import math
import os
import sys

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

PALETAS = {
    "claro": {"fondo": (0xCD, 0xC8, 0xB0), "linea": (0xBA, 0xB5, 0x9F), "franja": (0x48, 0x46, 0x3D)},
    "oscuro": {"fondo": (0x57, 0x54, 0x4A), "linea": (0x7D, 0x7A, 0x6B), "franja": (0xC2, 0xBD, 0xA6)},
}
FRANJA_ARRIBA = 95    # justo debajo de la barra (su alto reservado)
FRANJA_ABAJO = 65     # distancia desde el borde inferior, como en el original
CUADRICULA = 10       # separación de la cuadrícula, en px
SOMBRA_CUADRICULA = 0.97
LATERALES = 0.45      # cuánto se oscurecen los bordes izquierdo y derecho


class Lienzo:
    def __init__(self, ancho, alto, color):
        self.ancho, self.alto = ancho, alto
        self.px = bytearray(bytes(color) * (ancho * alto))

    def tramo(self, y, x0, x1, color):
        if not 0 <= y < self.alto:
            return
        x0, x1 = max(0, x0), min(self.ancho, x1)
        if x1 > x0:
            i = (y * self.ancho + x0) * 3
            self.px[i:i + (x1 - x0) * 3] = bytes(color) * (x1 - x0)

    def anillo(self, cx, cy, r, grosor, color):
        """Circunferencia de radio r y grosor dado (solo la parte visible)."""
        r_in, r_out = r - grosor / 2, r + grosor / 2
        for y in range(max(0, int(cy - r_out)), min(self.alto, int(cy + r_out) + 1)):
            dy2 = (y - cy) ** 2
            if dy2 > r_out ** 2:
                continue
            fuera = math.sqrt(r_out ** 2 - dy2)
            dentro = math.sqrt(r_in ** 2 - dy2) if dy2 < r_in ** 2 else 0.0
            self.tramo(y, round(cx - fuera), round(cx - dentro) + 1, color)
            self.tramo(y, round(cx + dentro), round(cx + fuera) + 1, color)

    def circulo(self, cx, cy, r, color):
        for dy in range(-r, r + 1):
            dx = int(math.sqrt(r * r - dy * dy) + 0.5)
            self.tramo(cy + dy, cx - dx, cx + dx + 1, color)

    def diagonal(self, x0, y0, x1, y1, grosor, color):
        pasos = max(abs(x1 - x0), abs(y1 - y0), 1)
        for t in range(pasos + 1):
            x = round(x0 + (x1 - x0) * t / pasos)
            y = round(y0 + (y1 - y0) * t / pasos)
            for k in range(grosor):
                self.tramo(y + k, x, x + grosor, color)

    def sombrear(self):
        """Cuadrícula y laterales oscurecidos, como el shader del original."""
        factor_x = [LATERALES * math.sin(math.pi * x / self.ancho) + (1 - LATERALES)
                    for x in range(self.ancho)]
        for y in range(self.alto):
            fila_rejilla = y % CUADRICULA == 0
            base = y * self.ancho * 3
            for x in range(self.ancho):
                f = factor_x[x]
                if fila_rejilla or x % CUADRICULA == 0:
                    f *= SOMBRA_CUADRICULA
                i = base + x * 3
                self.px[i] = int(self.px[i] * f)
                self.px[i + 1] = int(self.px[i + 1] * f)
                self.px[i + 2] = int(self.px[i + 2] * f)

    def guardar(self, ruta):
        GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(self.px)), GdkPixbuf.Colorspace.RGB, False, 8,
            self.ancho, self.alto, self.ancho * 3,
        ).savev(ruta, "png", [], [])


def mezcla(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def franja(lienzo, y, e, paleta, abajo=False):
    """Línea fina con bloque, dos puntos y un punto tenue cada 110 px (borde NieR).

    Abajo se dibuja reflejada: la línea queda debajo y el dibujo encima.
    """
    color, tenue = paleta["franja"], mezcla(paleta["fondo"], paleta["franja"], 0.25)
    paso, r = round(110 * e), max(2, round(4 * e))
    s = -1 if abajo else 1
    lienzo.tramo(y, 0, lienzo.ancho, color)
    for x in range(0, lienzo.ancho, paso):
        alto_bloque = round(8 * e)
        y_bloque = y + s * round(2 * e) - (alto_bloque if abajo else 0)
        for k in range(alto_bloque):
            lienzo.tramo(y_bloque + k, x + round(93 * e), x + paso, color)
        lienzo.circulo(x + round(39 * e), y + s * round(10 * e), r, color)
        lienzo.circulo(x + round(54 * e), y + s * round(10 * e), r, color)
        lienzo.circulo(x + round(46 * e), y + s * round(23 * e), r, tenue)


def dibujar(ancho, alto, paleta):
    e = alto / 1080
    lienzo = Lienzo(ancho, alto, paleta["fondo"])
    linea, g = paleta["linea"], max(2, round(2 * e))

    # Arcos dobles en dos esquinas opuestas
    for cx, cy in ((0, 0), (ancho, alto)):
        for r in (585, 605):
            lienzo.anillo(cx, cy, r * e, g, linea)

    # Cables diagonales en paralelo, de arriba a la izquierda hacia abajo a la derecha
    for x0, y0, x1, y1 in (
        (0, 60, 740, 800), (40, 0, 825, 785), (0, 205, 670, 875),
        (660, 10, 1440, 790), (695, 115, 1490, 910), (825, 115, 1920, 1210),
    ):
        lienzo.diagonal(round(x0 * ancho / 1920), round(y0 * e),
                        round(x1 * ancho / 1920), round(y1 * e), max(1, round(e)), linea)

    lienzo.sombrear()
    # Las franjas van después del sombreado para que queden nítidas
    franja(lienzo, FRANJA_ARRIBA, e, paleta)  # la barra mide 95 px a cualquier resolución
    franja(lienzo, alto - round(FRANJA_ABAJO * e), e, paleta, abajo=True)
    return lienzo


def puntero(ruta, color, escala=1):
    """Puntero NieR (flecha con un hueco redondo y dos cuadritos), con bordes suavizados."""
    ancho, alto, sub = 40 * escala, 26 * escala, 4
    flecha = [(0, 13), (10, 3), (34, 13), (10, 23)]

    def dentro(x, y):
        # Flecha convexa: el punto está dentro si queda del mismo lado de todos los bordes
        lados = [(x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
                 for (x0, y0), (x1, y1) in zip(flecha, flecha[1:] + flecha[:1], strict=True)]
        if not (all(v >= 0 for v in lados) or all(v <= 0 for v in lados)):
            return False
        return (x - 10) ** 2 + (y - 13) ** 2 > 2.2 ** 2  # el hueco redondo

    def cuadrito(x, y):
        return 36 <= x <= 39 and (1 <= y <= 4 or 22 <= y <= 25)

    px = bytearray(ancho * alto * 4)
    for py in range(alto):
        for pxx in range(ancho):
            n = 0
            for sy in range(sub):
                for sx in range(sub):
                    x = (pxx + (sx + 0.5) / sub) / escala
                    y = (py + (sy + 0.5) / sub) / escala
                    n += dentro(x, y) or cuadrito(x, y)
            i = (py * ancho + pxx) * 4
            px[i:i + 3] = bytes(color)
            px[i + 3] = round(255 * n / sub ** 2)
    GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(px)), GdkPixbuf.Colorspace.RGB, True, 8, ancho, alto, ancho * 4,
    ).savev(ruta, "png", [], [])


def main():
    carpeta = sys.argv[1]
    ancho = int(sys.argv[2]) if len(sys.argv) > 2 else 1920
    alto = int(sys.argv[3]) if len(sys.argv) > 3 else 1080
    for nombre, paleta in PALETAS.items():
        ruta = os.path.join(carpeta, f"yorha-{nombre}.png")
        dibujar(ancho, alto, paleta).guardar(ruta)
        print(f"Fondo guardado en {ruta} ({ancho}x{alto})")
    puntero(os.path.join(carpeta, "puntero-claro.png"), (0x48, 0x46, 0x3D))
    puntero(os.path.join(carpeta, "puntero-oscuro.png"), (0xC2, 0xBD, 0xA6))
    print("Punteros guardados")


if __name__ == "__main__":
    main()
