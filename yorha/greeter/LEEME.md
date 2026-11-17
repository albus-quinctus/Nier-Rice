# Pantalla de inicio YoRHa (greetd)

Sustituye a LightDM por **greetd** (paquete de Debian, 0.10) con una pantalla de inicio
propia: `saludo.py` (Python 3 + GTK 3 + gtk-layer-shell), solo Wayland, sin Xwayland.

## Cómo funciona

1. systemd arranca greetd en la consola 7 (`/etc/greetd/config.toml`, copia de `config.toml`).
2. greetd abre una sesión para el usuario `_greetd` y ejecuta
   `sway --config /srv/nier/yorha/greeter/sway.conf`: un sway mínimo sin barra, sin
   Xwayland y **sin ningún atajo** (no se puede abrir una terminal ni nada más).
3. Ese sway arranca `saludo.py`, que habla con greetd por `$GREETD_SOCK`:
   `create_session` → contesta a lo que pregunte PAM (contraseña, avisos, otros pasos) →
   `start_session` con `sh /srv/nier/yorha/sesion.sh`. Al terminar, `swaymsg exit`.
4. greetd lanza `sesion.sh` como el usuario elegido; su `~/.config/sway/config` decide si
   es YoRHa o el aspecto de trabajo. Al salir de esa sesión vuelve la pantalla de inicio.

En pantalla: hora y fecha, los usuarios (uid ≥ 1000 con un shell de `/etc/shells`),
campo de contraseña con aviso de BLOQ MAYÚS y SUSPENDER / REINICIAR / APAGAR
(reiniciar y apagar piden un segundo clic). Teclas: Intro entra, ↑ ↓ cambian de usuario,
Esc borra (y cancela una pregunta de PAM a medias).

- **Último usuario**: se guarda en `~_greetd/ultimo-usuario`. En Debian el HOME de
  `_greetd` es (creo) `/var/lib/greetd`; compruébalo con `getent passwd _greetd`.
  El instalador crea esa carpeta si falta y dice si `_greetd` puede escribir en ella.
- **Energía**: `systemctl --no-ask-password suspend|reboot|poweroff`. logind lo permite
  sin contraseña porque la sesión de `_greetd` es la sesión activa del asiento (polkit,
  `allow_active`). Si hubiera otro usuario con sesión abierta en otra consola, logind
  puede negarlo; el error sale en la pantalla.
- **Llavero**: el instalador añade a `/etc/pam.d/greetd` las mismas dos líneas que tenía
  LightDM (`-auth … pam_gnome_keyring.so` tras `@include common-auth` y
  `-session … pam_gnome_keyring.so auto_start` tras `@include common-session`).

## Instalar (como root)

    apt install greetd        # lo instalas tú; el script no instala nada
    su -
    sh /srv/nier/yorha/greeter/instalar-greetd.sh

Comprueba lo necesario, guarda copias de `/etc/greetd/config.toml` y `/etc/pam.d/greetd`
en `/srv/nier/antiguo` (con fecha), instala la configuración, añade el llavero a PAM,
revisa que `_greetd` pueda leer el saludo y el fondo y, tras preguntar `[s/N]`, hace
`systemctl disable lightdm` y `systemctl enable greetd`. Surte efecto al reiniciar.
Si `apt install greetd` pregunta qué gestor de pantalla usar por defecto, da igual la
respuesta: el script deja `display-manager.service` apuntando a greetd.

## Deshacer

    sh /srv/nier/yorha/greeter/instalar-greetd.sh --deshacer

Vuelve a LightDM y reinicia. Desde el 15-10-2026 LightDM está desinstalado: antes habría
que instalarlo con `apt install lightdm` (el script lo avisa). Las copias de la configuración
de greetd de antes están en `/srv/nier/antiguo`.

**Si la pantalla gráfica no aparece**: Ctrl+Alt+F2, entra como root y pasa greetd a modo
texto con `agreety` (viene con greetd): en
`/etc/greetd/config.toml` cambia la línea `command` por
`command = "agreety --cmd 'sh /srv/nier/yorha/sesion.sh'"` y `systemctl restart greetd`.

## Probar sin reiniciar

- **Solo el aspecto**, desde tu sesión YoRHa (se cierra sola a los 20 s, porque sin greetd
  no hay forma de entrar y la capa se queda con el teclado):

      timeout 20 python3 -I /srv/nier/yorha/greeter/saludo.py

  Dirá «Sin conexión con greetd»; es lo esperado.
- **De verdad, en otra consola**, como root: copia la configuración con otra consola
  (`sed 's/^vt = 7/vt = 8/' /etc/greetd/config.toml > /tmp/greetd-8.toml`, después de
  instalarla) y ejecuta `greetd --config /tmp/greetd-8.toml`. Saltará a la consola 8 con
  la pantalla de inicio; entra con **trabajo** (lilith ya tiene sesión abierta).
  Ctrl+C en la consola de root lo para. No uses la 7, que es la de greetd.

## Limitaciones

- Una sesión cada vez: no hay cambio de usuario con una sesión abierta (hay que salir).
- Siempre arranca `sesion.sh`: no hay selector de sesión (XFCE u otras no se ofrecen).
- Solo tema claro; letra FOT-Rodin Pro (o DejaVu Sans si no está).
- Si no existe `/etc/pam.d/greetd-greeter`, la sesión de `_greetd` también pasa por
  `/etc/pam.d/greetd`; la línea del llavero no hace nada útil ahí, pero tampoco daña.
- Probado con un greetd falso (mismo protocolo) en un sway sin pantalla; no probado con
  PAM real, greetd real, cambio de consola ni logind.
