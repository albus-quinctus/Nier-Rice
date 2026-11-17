# ARCHBOX

Gestor de archivos hecho a mano para sustituir a Thunar en la sesión Sway: Python 3 y GTK 3
(PyGObject, Gio), sin paquetes nuevos y sin shell (los programas se lanzan con Gio.AppInfo o
Gio.Subprocess y una lista de argumentos).

    python3 -I /srv/nier/yorha/archbox/archbox.py [CARPETA | ARCHIVO | URI ...]

Una carpeta se abre en una pestaña nueva. Con un archivo se abre su carpeta y el archivo queda
seleccionado. Solo corre una instancia: una segunda llamada abre la ventana en la que ya está.
Además, ARCHBOX atiende `org.freedesktop.FileManager1` en D-Bus (ShowFolders, ShowItems y
ShowItemProperties) para el «Mostrar en carpeta» de otros programas.

## Archivos

| Archivo | Qué hace |
|---|---|
| `archbox.py` | Aplicación: una sola instancia, D-Bus, tema, estado, volúmenes |
| `ventana.py` | Ventana, barra de ruta, paneles, pestañas, vista dividida, búsqueda, barra de estado |
| `acciones.py` | Órdenes, menús, atajos de teclado, «Abrir con» y acciones propias |
| `vista.py` | Contenido de la carpeta (monitor, búsqueda, papelera), las tres vistas, miniaturas y arrastrar |
| `operaciones.py` | Copiar, mover, papelera, borrar... en hilos, con la ventana de progreso, el portapapeles y deshacer |
| `dialogos.py` | Conflictos, renombrar (también varios a la vez), propiedades y permisos, «Abrir con otra aplicación» |
| `lugares.py` | Panel lateral: lugares, marcadores y dispositivos |
| `archivos.py` | Utilidades sin ventanas: nombres, tamaños, fechas, papelera freedesktop, plantillas |
| `archbox.css` | Aspecto YoRHa (claro y oscuro) |
| `archbox.desktop` | Lanzador; con `MimeType=inode/directory` sirve de gestor de archivos por defecto |

Estado por usuario:

- `~/.config/archbox/estado.json`: vista, zoom, orden, archivos ocultos, panel lateral y tamaño de la ventana.
- Marcadores: `~/.config/gtk-3.0/bookmarks`, el mismo archivo que usan Thunar y GTK.
- Miniaturas: `~/.cache/thumbnails/`, compartidas con otros programas según la norma freedesktop.

## Aspecto

ARCHBOX elige el aspecto según la configuración de Sway:

- Si `~/.config/sway/config` apunta a `.../yorha/sway.conf`, usa `archbox.css` con la paleta de
  `piezas.py`. Sigue el tema claro u oscuro de `comun.tema_actual()` y lo vuelve a leer al recibir
  SIGUSR1 o cuando cambia `$XDG_RUNTIME_DIR/yorha-tema`.
- En cualquier otro caso (sesión de trabajo) usa el tema GTK del sistema, con un CSS mínimo para que
  se parezca a Thunar.

## Funciones

- **Ventana**
  - Barra de herramientas y barra de ruta con botones. Con Ctrl+L se cambia a una entrada de texto
    que completa nombres.
  - Panel lateral con lugares, marcadores y dispositivos. Los marcadores se pueden renombrar, quitar
    y reordenar, y se puede soltar en ellos lo que se arrastra.
  - Barra de estado con la selección y el espacio libre.
- **Vistas**
  - Iconos, lista compacta y lista detallada (Ctrl+1/2/3), con zoom y orden por nombre (natural),
    tamaño, tipo o fecha. Las carpetas van siempre primero.
  - Archivos ocultos con Ctrl+H.
  - Miniaturas freedesktop de las imágenes, generadas en segundo plano.
- **Navegación**
  - Pestañas y vista dividida (F3). Se puede arrastrar de un lado a otro.
  - La carpeta se actualiza en vivo cuando cambia en el disco.
  - Escribir un nombre salta al archivo, sin distinguir mayúsculas ni tildes.
  - Ctrl+F filtra la carpeta o busca en todas sus subcarpetas; la búsqueda se puede cancelar.
- **Operaciones**
  - Copiar, mover, enlazar, papelera y borrar se hacen en segundo plano, con una ventana de progreso
    que permite pausar y cancelar.
  - Un diálogo resuelve los conflictos: reemplazar, saltar, renombrar o combinar, y aplicar a todos.
  - Deshacer y rehacer (Ctrl+Z, Ctrl+Shift+Z) recuerdan las últimas 30 operaciones.
  - El portapapeles es compatible con Thunar, Nautilus y Nemo (`x-special/gnome-copied-files`).
- **Arrastrar y soltar**
  - Sin tecla: mueve dentro del mismo disco y copia entre discos distintos.
  - Ctrl copia, Shift mueve, Ctrl+Shift crea un enlace y Alt pregunta qué hacer.
- **Archivos**
  - Renombrar (F2) y renombrar varios a la vez: buscar y reemplazar, numerar o cambiar mayúsculas.
  - Nueva carpeta, nuevo archivo vacío o creado desde las plantillas de `~/Plantillas`.
  - Duplicar y crear enlace.
- **Papelera** (norma freedesktop): ver, restaurar, borrar y vaciar. Incluye las papeleras
  `.Trash-UID` de otros discos.
- **Abrir**
  - Abrir con la aplicación por defecto, con el submenú «Abrir con» o eligiendo otra aplicación.
  - Los programas y guiones **no se ejecutan con doble clic**: se abren como texto. Para ejecutarlos
    hay que usar «Ejecutar» en el menú contextual.
  - Un `.desktop` solo se ejecuta si es del usuario, tiene permiso de ejecución y nadie más puede
    escribirlo.
  - No existe «Abrir como root».
- **Acciones propias**
  - «Abrir terminal aquí» (foot) y «Buscar aquí».
  - Comprimir y extraer con xarchiver. Estas entradas solo aparecen si xarchiver está instalado.
- **Propiedades**: nombre, tipo, tamaño (las carpetas se miden en segundo plano), fechas, aplicación
  por defecto y editor de permisos.
- **Volúmenes**: montar, desmontar y expulsar desde el panel lateral, con un aviso al conectar un
  dispositivo.

## Atajos

| Tecla | Acción | Tecla | Acción |
|---|---|---|---|
| Ctrl+L | Escribir la ruta | Ctrl+F | Buscar |
| Alt+← / Retroceso | Atrás | Alt+→ | Adelante |
| Alt+↑ | Carpeta superior | Alt+Inicio | Carpeta personal |
| F5 / Ctrl+R | Recargar | Ctrl+H | Archivos ocultos |
| Ctrl+1 / 2 / 3 | Iconos / compacta / detallada | Ctrl++ / Ctrl+- / Ctrl+0 | Zoom |
| Ctrl+T / Ctrl+W | Abrir / cerrar pestaña | Ctrl+RePág / AvPág | Cambiar de pestaña |
| Ctrl+N | Nueva ventana | F3 / F9 | Vista dividida / panel lateral |
| Ctrl+C / X / V | Copiar / cortar / pegar | Ctrl+A / Ctrl+I | Seleccionar todo / invertir |
| F2 | Renombrar | Ctrl+Shift+N | Nueva carpeta |
| Supr | Mover a la papelera | Shift+Supr | Borrar definitivamente |
| Ctrl+Z | Deshacer | Ctrl+Shift+Z / Ctrl+Y | Rehacer |
| Ctrl+D | Añadir marcador | Ctrl+Shift+D | Duplicar |
| Alt+Intro | Propiedades | Clic central | Abrir en pestaña nueva |

## Diferencias con Thunar

- **Sin gvfs**: no hay ubicaciones remotas (sftp:, smb:, ...). La papelera se lee directamente del
  disco, así que solo incluye la del usuario y las de los discos montados.
- **Miniaturas**: solo de imágenes. Las de vídeos y PDF aparecen únicamente si otro programa (como
  tumbler) ya las había guardado.
- **Vistas**: la lista compacta se ordena por filas y no por columnas. Los archivos cortados no se
  ven atenuados. No hay ajustes de vista distintos para cada carpeta.
- **Pausa**: en las copias locales rápidas solo se nota entre un archivo y otro. Al cancelar, se
  borra la copia a medias del archivo en curso.
- **Deshacer**: no recupera los archivos reemplazados ni deshace la combinación de carpetas.
- **Atajos**: como en Thunar, Ctrl+D añade un marcador, así que duplicar es Ctrl+Shift+D.
- **Sin complementos**: no hay acciones personalizadas configurables (las acciones son fijas), ni
  renombrado masivo con complementos, ni editor de acciones.
- **Volúmenes**: el manejo es mínimo. Monta, desmonta y expulsa mediante Gio, que necesita udisks2
  para los discos extraíbles.
