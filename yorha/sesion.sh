#!/bin/sh
# sesion.sh — arranca la sesión YoRHa (la lanza greetd desde la pantalla de inicio, greeter/saludo.py).
# No hay Xwayland: estas variables hacen que cada programa use Wayland directamente.
export XDG_CURRENT_DESKTOP=sway XDG_SESSION_DESKTOP=sway XDG_SESSION_TYPE=wayland
export MOZ_ENABLE_WAYLAND=1                  # LibreWolf
export ELECTRON_OZONE_PLATFORM_HINT=auto     # Obsidian, Claude Desktop, Cursor (Electron)
export QT_QPA_PLATFORM="wayland;xcb"         # KeePassXC y demás programas Qt
export _JAVA_AWT_WM_NONREPARENTING=1         # programas Java
exec sway
