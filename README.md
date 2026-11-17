# Nier-Rice

Escritorio **YoRHa** inspirado en NieR:Automata para **Sway** (Wayland, sin Xwayland), hecho a mano
para un ThinkPad T480 con Debian Trixie. Rehecho desde cero a partir de la idea de
`hyprland-yorha`: barra de escritorios, panel de ajustes, lanzador, visor, gestor de archivos
(ARCHBOX), pantalla de bloqueo y pantalla de inicio (greetd), todo en Python 3 + GTK 3 y `sh`.

Tiene tema claro y oscuro (`Mod+t`) y un segundo aspecto, `xfce/`, para un escritorio de trabajo
con la apariencia de XFCE sobre el mismo Sway.

## Contenido

| Ruta | Qué es |
|---|---|
| `yorha/sway.conf`, `yorha/sway-comun.conf` | Configuración de Sway (YoRHa y lo común con el de trabajo) |
| `yorha/barra.py` | Barra de escritorios con animación de cambio, avisos de volumen, brillo y batería |
| `yorha/ajustes.py` | Panel de ajustes (tecla Super): red, Bluetooth, sonido, pantallas, música, energía |
| `yorha/lanzador.py` | Buscador de aplicaciones (`Mod+Tab`) |
| `yorha/visor.py` | Visor de imágenes, Markdown, vídeo y audio |
| `yorha/archbox/` | ARCHBOX, gestor de archivos (ver `archbox/LEEME.md`) |
| `yorha/greeter/` | Pantalla de inicio para greetd (ver `greeter/LEEME.md`) |
| `yorha/xfce/` | Escritorio de trabajo con aspecto XFCE: panel, bandeja, avisos |
| `yorha/fondo.py`, `yorha/fondos/` | Generador de los fondos y los fondos ya generados |
| `yorha/*.sh` | Tema, bloqueo, teclas del portátil, sesión e instaladores por usuario |
| `yorha/foot*.ini`, `yorha/mako.conf` | Terminal y notificaciones |

## Requisitos

Debian Trixie (o similar) con:

```bash
apt install sway swayidle swaylock foot mako-notifier grim slurp wl-clipboard python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1 pulseaudio-utils network-manager bluez gnome-keyring mate-polkit fonts-dejavu-core
```

Opcionales: `greetd` (pantalla de inicio) y `mpv` (vídeo y audio en el visor).

La fuente del tema es **FOT-Rodin Pro**, que no se incluye porque no es libre. Sin ella se usa
DejaVu Sans.

## Instalación

Los archivos esperan estar en **`/srv/nier/yorha`** (las rutas están escritas así en la
configuración de Sway y en los instaladores).

1. Copiar el rice, como root:

   ```bash
   install -d -m 755 /srv/nier /srv/nier/antiguo
   cp -r yorha /srv/nier/yorha
   chown -R root:root /srv/nier/yorha
   chmod -R u=rwX,go=rX /srv/nier/yorha
   ```

2. Activarlo para tu usuario (sin root). Enlaza `~/.config/sway/config` y guarda la que hubiera:

   ```bash
   sh /srv/nier/yorha/instalar-usuario.sh
   ```

   Con `xfce` como argumento se activa el escritorio de trabajo en su lugar.

3. Cerrar la sesión y entrar en Sway (o con `sh /srv/nier/yorha/sesion.sh` desde una consola).

4. Opcional, pantalla de inicio propia con greetd: ver [`yorha/greeter/LEEME.md`](yorha/greeter/LEEME.md).

5. Opcional, solo portátiles ThinkPad: `instalar-carga-semanal.sh` (como root) carga la batería al
   100 % una vez por semana y luego vuelve al límite de 75-80 %.

## Atajos principales

| Tecla | Acción |
|---|---|
| `Super` (soltar) | Panel de ajustes |
| `Super+Tab` | Lanzador de aplicaciones |
| `Super+Return` | Terminal |
| `Super+e` | ARCHBOX |
| `Super+1…6` | Cambiar de escritorio (con animación) |
| `Super+Shift+1…6` | Mover la ventana a otro escritorio |
| `Super+t` | Tema claro / oscuro |
| `Super+l` | Bloquear |
| `Super+p` | Pantallas y proyector |
| `Super+Escape` | Energía (suspender, reiniciar, apagar) |
| `Super+q` | Cerrar ventana |
| `Super+f` / `Super+Space` | Pantalla completa / flotante |
| `Imprimir` | Captura de una zona al portapapeles |
| `Super+Shift+c` / `Super+Shift+e` | Recargar Sway / salir |

## Regenerar los fondos

```bash
python3 -I yorha/fondo.py yorha/fondos 1920 1080
```

## Licencia

MIT, ver [LICENSE](LICENSE). La fuente FOT-Rodin Pro no se distribuye y tiene su propia licencia.
