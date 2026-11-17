"""Gestión de ventanas del escritorio de trabajo (reparto, bordes, maximizar, Alt+Tab); la usa panel.py."""
import json

from gi.repository import Gdk, GLib  # versiones ya fijadas por panel.py

from barra import OBTENER_ARBOL
import emergentes

# Lo que reserva arriba el panel (panel.py), como el de lilith: el área útil empieza en y=65
RESERVA = 65
# Márgenes de xfwm4 (margin_*): al maximizar, la ventana deja 10 px a los lados y abajo
MARGEN_VENTANAS = 10
# Más ventanas que estas en un escritorio ya no se reparten: saldrían tan pequeñas que muchos
# programas (LibreWolf, Cursor...) no caben y se salen de la pantalla. La nueva aparece en el centro.
MAXIMO_REPARTO = 6
# Al bajar una ventana que quedó bajo el panel, si le quedaría menos alto que esto, se baja entera
ALTO_MINIMO = 150


def celda(e, columna=0, columnas=1, fila=0, filas=1):
    """(x, y, ancho, alto) de un hueco de una rejilla sobre el área útil e, con los márgenes de xfwm4:
    10 px a los lados, entre ventanas y abajo (arriba ya está la separación del panel). Los píxeles
    que sobran al dividir van a unos y a otros, así las ventanas llenan el sitio sin márgenes de más.
    Con los valores por omisión es la ventana maximizada."""
    m = MARGEN_VENTANAS

    def tramo(k, inicio, total, partes):
        libre = total - m * (partes + 1)
        return inicio + m + k * m + k * libre // partes, (k + 1) * libre // partes - k * libre // partes

    x, ancho = tramo(columna, e["x"], e["width"], columnas)
    y, alto = tramo(fila, e["y"] - m, e["height"] + m, filas)
    return x, y, ancho, alto


def caja(nodo):
    """(x, y, ancho, alto) de una ventana de sway contando su barra de título, que rect no incluye."""
    r, d = nodo["rect"], nodo.get("deco_rect") or {"height": 0}
    return r["x"], r["y"] - d["height"], r["width"], r["height"] + d["height"]


class Ventanas:
    """Reparto, bordes, diálogos, maximizar, minimizar y Alt+Tab de las ventanas de sway.

    Es una mezcla (mixin) de panel.Panel: usa su conexión con sway (self.sway y self.mandar), las
    ventanas del último repaso (self.ventanas, self.mru, self.del_escritorio()) y el estado que
    Panel.__init__ pone a cero (maximizadas, dialogos, repartidas, posiciones, esperadas...)."""

    def colocar(self, ident):
        """Como xfwm4 con placement_mode center: sway ya centra las ventanas nuevas en el área
        útil; aquí solo se ponen los diálogos (otra ventana del mismo programa, como Guardar
        como) centrados sobre la ventana de la que salen. La contraseña de administrador, al centro."""
        try:
            arbol = self.sway.pedir(OBTENER_ARBOL)
        except (ConnectionError, OSError, ValueError):
            return
        area = nueva = None
        otras = []
        pendientes = [(arbol, None)]
        while pendientes:
            nodo, escritorio = pendientes.pop()
            if nodo.get("type") == "workspace":
                escritorio = nodo
            if nodo.get("type") == "floating_con" and escritorio and escritorio["name"] != "__i3_scratch":
                if nodo["id"] == ident:
                    area, nueva = escritorio, nodo
                else:
                    otras.append((escritorio["id"], nodo))
            pendientes += [(n, escritorio) for n in nodo.get("floating_nodes", []) + nodo.get("nodes", [])]
        if not nueva:
            return
        if "polkit" in (nueva.get("app_id") or "").lower():
            self.dialogos.add(ident)  # la contraseña de administrador se queda en el centro
            return
        e, (ancho, alto) = area["rect"], caja(nueva)[2:]
        # Un diálogo es otra ventana del mismo programa más pequeña que la suya (Guardar como...)
        madres = {o["id"]: caja(o) for i, o in otras if i == area["id"] and nueva.get("pid")
                  and o.get("pid") == nueva["pid"] and ancho * alto < 0.8 * caja(o)[2] * caja(o)[3]}
        if not madres:
            return
        self.dialogos.add(ident)
        # La ventana de la que sale: la del mismo programa usada más recientemente
        madre = next((madres[i] for i in self.mru if i in madres), next(iter(madres.values())))
        mx, my, ma, mh = madre
        x = min(max(mx + (ma - ancho) // 2, e["x"]), e["x"] + max(e["width"] - ancho, 0))
        y = min(max(my + (mh - alto) // 2, e["y"]), e["y"] + max(e["height"] - alto, 0))
        self.poner(ident, x, y)

    def pedir_reparto(self):
        # Un momento después: sway aún tiene que hacerla flotar y colocar() marcar los diálogos
        if not self.pendiente_reparto:
            self.pendiente_reparto = True
            GLib.timeout_add(120, self.repartir)

    def repartir(self, solo_anotar=False):
        """Reparte la pantalla entre las ventanas de cada escritorio cuyo grupo de ventanas ha
        cambiado (se abrió, cerró, minimizó o llegó alguna). Si solo se movieron a mano, no las toca."""
        self.pendiente_reparto = False
        try:
            arbol = self.sway.pedir(OBTENER_ARBOL)
        except (ConnectionError, OSError, ValueError):
            return False
        grupos, areas = {}, {}
        pendientes = [(arbol, None)]
        while pendientes:
            nodo, escritorio = pendientes.pop()
            if nodo.get("type") == "workspace":
                escritorio = nodo
                if nodo["name"] != "__i3_scratch":
                    grupos.setdefault(nodo["name"], [])
                    areas[nodo["name"]] = nodo["rect"]
            if (nodo.get("type") == "floating_con" and escritorio and escritorio["name"] != "__i3_scratch"
                    and nodo["id"] not in self.dialogos and not nodo.get("fullscreen_mode")
                    and not nodo.get("sticky")):
                grupos[escritorio["name"]].append(nodo["id"])
            pendientes += [(n, escritorio) for n in nodo.get("floating_nodes", []) + nodo.get("nodes", [])]
        for nombre, ids in grupos.items():
            ids = tuple(sorted(ids))  # por orden de apertura: la más antigua a la izquierda
            if ids == self.repartidas.get(nombre):
                continue
            self.repartidas[nombre] = ids
            if not solo_anotar and 0 < len(ids) <= MAXIMO_REPARTO:
                self.colocar_en_rejilla(ids, areas[nombre])
        return False

    def colocar_en_rejilla(self, ids, e):
        """Columnas de la misma anchura; las primeras con menos ventanas (3 = una grande a la
        izquierda y dos a la derecha, 4 = dos por dos)."""
        columnas = 1
        while columnas * columnas < len(ids):
            columnas += 1
        por_columna = [len(ids) // columnas] * columnas
        for n in range(len(ids) % columnas):
            por_columna[columnas - 1 - n] += 1
        ordenes, k = [], 0
        for c, filas in enumerate(por_columna):
            for f in range(filas):
                ordenes.append(self.poner(ids[k], *celda(e, c, columnas, f, filas), mandar=False))
                k += 1
        self.mandar("; ".join(ordenes))

    def poner(self, i, x, y, ancho=None, alto=None, antes="", mandar=True):
        """Mueve la ventana i (y le cambia el tamaño, si se da) y apunta dónde va a quedar, para que
        vigilar_bordes no tome el movimiento por un arrastre. El tamaño incluye la barra de título."""
        self.maximizadas.pop(i, None)
        self.esperadas[i] = (x, y, ancho, alto)
        tam = f"resize set width {ancho} px height {alto} px, " if ancho else ""
        orden = f"[con_id={i}] {antes}{tam}move absolute position {x} px {y} px"
        return self.mandar(orden) if mandar else orden

    def vigilar_bordes(self):
        """Lo que xfwm4 hace al arrastrar (sway no avisa al mover, así que se mira cada poco): una
        ventana que se ha movido y ya está quieta, si quedó contra el borde izquierdo o derecho de la
        pantalla ocupa esa mitad; si quedó contra el panel, se maximiza. Y ninguna se queda bajo el
        panel: cuando está quieta, vuelve a bajar al área útil. Sway tampoco avisa al soltar el ratón:
        "quieta" es sin cambios en dos vueltas seguidas (0,8 s), para no adelantarse a una pausa."""
        try:
            arbol = self.sway.pedir(OBTENER_ARBOL)
        except (ConnectionError, OSError, ValueError):
            return True
        vistas, quietas = {}, {}
        pendientes = [(arbol, None)]
        while pendientes:
            nodo, escritorio = pendientes.pop()
            if nodo.get("type") == "workspace":
                escritorio = nodo
            if (nodo.get("type") == "floating_con" and escritorio and escritorio["name"] != "__i3_scratch"
                    and not nodo.get("fullscreen_mode")):
                self.vigilar(nodo["id"], caja(nodo), escritorio["rect"], vistas, quietas)
            pendientes += [(n, escritorio) for n in nodo.get("floating_nodes", []) + nodo.get("nodes", [])]
        self.posiciones, self.quietas = vistas, quietas
        self.arrastradas = {i: c for i, c in self.arrastradas.items() if i in vistas}
        self.esperadas = {i: c for i, c in self.esperadas.items() if i in vistas}
        return True

    def vigilar(self, i, ahora, area, vistas, quietas):
        vistas[i] = ahora
        antes = self.posiciones.get(i)
        quietas[i] = self.quietas.get(i, 0) + 1 if antes == ahora else 0
        esperada = self.esperadas.get(i)
        if esperada and ahora[:2] == esperada[:2]:
            # Llegó a donde la mandó el panel: no es un arrastre (el tamaño no se mira, hay
            # programas que no encogen por debajo de su mínimo)
            del self.esperadas[i]
            self.arrastradas.pop(i, None)
            return
        if esperada and quietas[i] >= 2:
            del self.esperadas[i]  # sway no la dejó ahí: ya no se espera nada
        if antes and antes[2:] == ahora[2:] and antes[:2] != ahora[:2]:
            self.esperadas.pop(i, None)  # la mueve el usuario
            self.arrastradas.setdefault(i, antes)  # moviéndose: se decide al soltarla
        elif quietas[i] < 2:
            return
        elif i in self.arrastradas:
            self.soltada(i, ahora, self.arrastradas.pop(i), area)
        elif ahora[1] < area["y"] and i not in self.esperadas:
            # Bajar su borde de arriba al área útil sin mover el de abajo; si casi no queda nada
            # por debajo del panel, bajarla entera
            x, y, ancho, alto = ahora
            if y + alto - area["y"] >= ALTO_MINIMO:
                alto = y + alto - area["y"]
            self.poner(i, x, area["y"], ancho, min(alto, area["height"] - MARGEN_VENTANAS))

    def soltada(self, i, ahora, antes, e):
        x, y, ancho, _alto = ahora
        if x <= e["x"]:
            self.ajustar(i, "izquierda", e)
        elif x + ancho >= e["x"] + e["width"]:
            self.ajustar(i, "derecha", e)
        elif y < e["y"]:
            self.ajustar(i, "arriba", e)
            self.maximizadas[i] = antes  # Maximizar otra vez la devuelve a como estaba

    def enfocada(self):
        return next((v for v in self.ventanas if v["enfocada"]), None)

    def minimizar(self, v=None):
        v = v or self.enfocada()
        if v and not v["minimizada"]:
            self.mandar(f"[con_id={v['id']}] mark --add _min_{v['escritorio']}_{v['id']}, move scratchpad")

    def restaurar(self, v):
        orden = f"[con_id={v['id']}] move container to workspace {json.dumps(v['escritorio'])}, focus"
        if v["marca"]:
            orden += f", unmark {v['marca']}"
        self.mandar(orden)

    def maximizar(self, v=None):
        """Alterna entre ocupar todo el escritorio (sin tapar el panel) y el tamaño anterior."""
        v = v or self.enfocada()
        if not v or v["minimizada"]:
            return
        i = v["id"]
        # El tamaño de ahora, preguntado a sway: cambiar el tamaño no avisa al panel
        r, d = self.rect_actual(i) or (v["rect"], v["deco"])
        d = d or {"height": 0}
        if i in self.maximizadas and r["width"] < self.get_allocated_width() * 0.9:
            del self.maximizadas[i]  # se movió o cambió de tamaño a mano: ya no está maximizada
        if i in self.maximizadas:
            self.poner(i, *self.maximizadas.pop(i))
        else:
            ax, ay, aa, ah = self.area_util(i)
            self.poner(i, *celda({"x": ax, "y": ay, "width": aa, "height": ah}), antes="floating enable, ")
            self.mandar(f"[con_id={i}] focus")
            # resize set cuenta también la barra de título, que rect no incluye
            self.maximizadas[i] = (r["x"], r["y"] - d["height"], r["width"], r["height"] + d["height"])

    def area_util(self, ident):
        """(x, y, ancho, alto) del escritorio de la ventana: la pantalla menos lo que reserva el panel."""
        try:
            pendientes = [(self.sway.pedir(OBTENER_ARBOL), None)]
        except (ConnectionError, OSError, ValueError):
            pendientes = []
        while pendientes:
            nodo, escritorio = pendientes.pop()
            if nodo.get("type") == "workspace":
                escritorio = nodo
            if nodo.get("id") == ident and escritorio:
                r = escritorio["rect"]
                return r["x"], r["y"], r["width"], r["height"]
            pendientes += [(n, escritorio) for n in nodo.get("floating_nodes", []) + nodo.get("nodes", [])]
        monitor = Gdk.Display.get_default().get_monitor(0).get_geometry()
        return monitor.x, monitor.y + RESERVA, monitor.width, monitor.height - RESERVA

    def rect_actual(self, ident):
        """(rect, deco_rect) de la ventana según sway ahora mismo, o None."""
        try:
            pendientes = [self.sway.pedir(OBTENER_ARBOL)]
        except (ConnectionError, OSError, ValueError):
            return None
        while pendientes:
            nodo = pendientes.pop()
            if nodo.get("id") == ident:
                return nodo["rect"], nodo.get("deco_rect")
            pendientes += nodo.get("floating_nodes", []) + nodo.get("nodes", [])
        return None

    def media_pantalla(self, lado):
        v = self.enfocada()
        if v:
            ax, ay, aa, ah = self.area_util(v["id"])
            self.ajustar(v["id"], lado, {"x": ax, "y": ay, "width": aa, "height": ah})

    def ajustar(self, i, lado, e):
        """Pone la ventana en la mitad izquierda o derecha del área útil (las medidas de la rejilla
        de dos ventanas), o la llena (arriba), con los márgenes de xfwm4."""
        hueco = celda(e) if lado == "arriba" else celda(e, 1 if lado == "derecha" else 0, 2)
        self.poner(i, *hueco, antes="floating enable, ")

    # --- Alt+Tab ---------------------------------------------------------------

    def alt_tab(self, paso):
        if self.selector and not self.selector.cerrada:
            self.selector.mover(paso)
            return
        lista = self.del_escritorio()
        if len(lista) < 2:
            if lista and lista[0]["minimizada"]:
                self.restaurar(lista[0])
            return
        posicion = {i: n for n, i in enumerate(self.mru)}
        lista.sort(key=lambda v: posicion.get(v["id"], len(posicion)))
        self.cerrar_emergente()
        self.selector = emergentes.Selector(self.cerrado, lista, self.elegida)
        if paso < 0:
            self.selector.indice = len(lista) - 1
            self.selector.marcar()
        self.selector.show_all()

    def elegida(self, v):
        if v["minimizada"]:
            self.restaurar(v)
        else:
            self.mandar(f"[con_id={v['id']}] focus")
