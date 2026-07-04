# Ayuda — Project Manager

## Resumen
Project Manager gestiona una gran cantidad de proyectos en una sola carpeta.
Escanea la carpeta raíz de proyectos, lo muestra todo en una tabla y permite
iniciar la CLI de Claude Code o Codex dentro de cualquier proyecto con un clic.

Funciones principales:
• Tabla de todos los proyectos con ordenación y búsqueda
• Análisis del proyecto con IA mediante DeepSeek (qué es y en qué estado)
• Inicio de Claude / Codex dentro del proyecto seleccionado
• Apertura de varios proyectos como pestañas de Windows Terminal (presets)
• Fijar proyectos importantes arriba
• Gestor de tareas con recordatorios
• Interfaz multilingüe (5 idiomas) que cambia al instante

Consejo: pulsa F1 en cualquier momento para abrir esta ayuda.

## Lista de proyectos
La pestaña «Proyectos» muestra cada carpeta de la raíz de proyectos.

• Búsqueda — el campo superior filtra por nombre, descripción y stack.
• Ordenación — clic en un encabezado de columna.
• Doble clic en un proyecto — inicia Claude Code.
• Clic derecho — un menú contextual con todas las acciones.

«🔄 Escanear (forzado)» vuelve a escanear la carpeta ignorando la caché.
«📂 Carpeta» abre la raíz de proyectos en el Explorador.
«📁 Datos» abre la carpeta con la configuración y la base de datos (%APPDATA%).

## Fijar y ordenar
Los proyectos fijados aparecen siempre arriba de la lista, resaltados en amarillo.

• Fijar / desfijar — el botón «📌 Fijar» o el menú contextual.
• Reordenar — arrastra un proyecto fijado hacia arriba o abajo con el ratón,
  o usa Alt+↑ / Alt+↓.

El orden se guarda automáticamente y se mantiene tras un reinicio.

## Iniciar Claude y Codex
Selecciona un proyecto y pulsa «▶ Claude» o «▶ Codex» — se abre una nueva
ventana de terminal con el agente ya ejecutándose dentro de la carpeta del
proyecto.

El inicio usa scripts del escritorio (Claude-BypassProxy, Codex-BypassProxy)
que configuran el entorno y eluden el proxy.

«✨ Nuevo» crea una nueva carpeta de proyecto e inicia un agente en ella.

## Pestañas de terminal y presets
«🖥 Abrir en pestañas» abre un diálogo para iniciar varios proyectos a la
vez — cada uno en su propia pestaña de Windows Terminal con su color y título.

Flujo de trabajo:
1. Marca los proyectos que necesitas (menú contextual → «Marcar para terminal»).
2. Abre el diálogo, reordena y renombra pestañas si es necesario.
3. Pulsa «🚀 Abrir».

Un preset es un conjunto de proyectos guardado. Guárdalo con
«💾 Guardar como…» y la próxima vez abre todo el conjunto con un solo clic —
útil para la rutina matutina «abrir todo en lo que estoy trabajando».

El título de pestaña se puede establecer con doble clic en la lista, o desde
el menú contextual de la tabla principal. El título también se muestra en la
columna «Título de pestaña» de la tabla principal.

## Restaurar títulos
Tras un comando /resume, Claude reescribe el título de la pestaña de terminal.
El botón «🏷 Restaurar títulos» devuelve los títulos a su sitio.

El programa encuentra las pestañas de terminal abiertas, detecta qué proyecto
hay en cada una y las renombra de vuelta a sus títulos configurados. No se
necesita vinculación con un preset — la detección es dinámica.

## Gestor de tareas
La pestaña «Gestor de tareas» es una lista de tareas, ideas y notas por
proyecto.

• Una tarea puede estar vinculada a un proyecto o ser «sin proyecto».
• Una tarea tiene tipo, estado, prioridad, etiquetas, fecha límite y
  recordatorio.
• Un recordatorio se dispara dentro del programa; también puedes crear un
  recordatorio del sistema de Windows (mediante el Programador de tareas) que
  se dispara incluso con el programa cerrado.
• Los botones «🚀 Probar en Claude / Codex» crean una carpeta de idea,
  escriben IDEA.md e inician un agente para desarrollar la idea.

## Análisis DeepSeek
El análisis describe qué es el proyecto, cómo funciona y en qué etapa está.

• «🤖 Análisis DS» — analiza el proyecto seleccionado.
• «🤖 DS: nuevos» — analiza solo los proyectos sin descripción.
• «🤖 DS: todos» — vuelve a analizar todos los proyectos.
• «⏹ Parar» — aborta el análisis masivo.

El resultado se guarda en caché y se muestra en el panel derecho y la tabla.

## Configuración e idioma
• Tamaño de fuente — los botones A− / A+ o Ctrl + rueda del ratón.
• Idioma — el desplegable superior. La interfaz cambia al instante, sin
  reiniciar. Disponibles ruso, inglés, alemán, español y chino.

Toda la configuración y los datos se guardan en %APPDATA%\ProjectManager.
Una copia de seguridad diaria se guarda en Documents\ProjectManager-Backups.
