# MB COMPRENSOR IA · versión 1.0.13

Compresor de video de escritorio para Windows. Su función es transformar videos locales a MP4 de alta calidad, con un tamaño máximo por archivo.

## Abrir

Usa el acceso directo **MB COMPRENSOR IA** del escritorio.

Aplicación instalada: `%LOCALAPPDATA%\MBCompresorPro\versions\1.0.13\MB Compresor Pro.exe`.

Para abrir su carpeta, presiona **Win + R**, escribe `%LOCALAPPDATA%\MBCompresorPro` y pulsa Enter. También tienes una copia en `Aplicacion/MB Compresor Pro/MB Compresor Pro.exe`; conserva la carpeta completa junto a ese ejecutable.

## Una sola carpeta de salida

En **Configuración → Videos de salida** se elige dónde se guardan los videos comprimidos. Por defecto se guarda **en la misma carpeta que cada video original**. Puedes elegir Downloads con el botón Guardar en Downloads.

El campo Guardar en de la ventana principal muestra esa misma carpeta. Examinar permite cambiarla y aplica el cambio a la cola pendiente. Ya no aparecen carpeta de descargas, cookies, Telegram ni una pestaña de descarga/importación.

Los originales se conservan. Los resultados usan el mismo nombre base con `.mp4`; si ya existe ese nombre o coincide con el original, se agrega un sufijo.

## Comprimir

1. Agrega videos, una carpeta o arrastra archivos a la ventana.
2. Selecciona el tamaño máximo en GB y el códec. Si cambias ajustes globales después de cargar la cola, pulsa Aplicar estos ajustes a los pendientes.
3. Resolución, audio y subtítulos despliega las opciones dentro de cada fila. Los cambios se guardan automáticamente. Más opciones contiene FPS, recorte, modo de subtítulos, estéreo y ajuste adaptativo.
4. Inicia la compresión. Puedes pausar, reanudar o detener la cola.

Valores iniciales: 1080p sin aumentar resolución, primera pista de audio, calidad 512 kbps, primer subtítulo quemado y modo Máxima calidad. El motor usa dos pasadas y publica un resultado solo tras verificar que cumple el límite. 1 GB = 1 000 000 000 bytes.

H.264/H.265 son compresión con pérdida: el objetivo es conservar la mejor calidad posible dentro del límite solicitado. No se promete una copia idéntica al original ni mejora de detalles. El audio puede ajustarse si el presupuesto de tamaño es insuficiente. Admite los formatos que pueda decodificar el FFmpeg incluido; no elimina DRM.

## IA local

Ollama y el modelo local qwen3:0.6b proporcionan recomendaciones de compresión. Solo se consultan metadatos técnicos, sin enviar el video ni rutas a servicios externos. Puedes revisar las recomendaciones antes de aplicarlas. La compresión funciona sin el asesor.

## Configuración y motores

Tema claro, oscuro o automático (claro de 05:30 a 18:29), carpeta única de salida, Ollama y motores de compresión FFmpeg/FFprobe. El panel verifica versiones, consulta actualizaciones oficiales y permite actualizar o reparar los motores; informa cuando no puede verificar una versión remota. AMF/NVENC se comprueban con una codificación real de un cuadro. La compresión usa CPU en dos pasadas para controlar el tamaño.

Actualizaciones de la aplicación: https://github.com/floresroldanluis/MBCompresorVideoIA . Buscar actualización compara la versión instalada con el último Release estable. Instalar actualización y reiniciar el programa descarga el ZIP compatible, verifica SHA256, cierra la aplicación, guarda una copia anterior y reinicia. Se conservan settings.json, queue.json y los videos; ante un error durante el reemplazo se intenta restaurar la copia anterior. Si aún no hay Releases, no hay una versión más reciente o el paquete no es compatible, instalar queda deshabilitado.

## Diseño y validación

La apariencia toma como referencia la paleta y los componentes de MB Downloader instalado: azules y cian, botones degradados, tarjetas redondeadas y estados de interacción. Se mantuvo el tema oscuro y las opciones desplegables.

Se verificaron inicio del ejecutable, carpeta por defecto, ausencia de campos duplicados y funciones de descarga en la interfaz, guardado de ajustes y flujo de compresión. El motor pasó siete pruebas de integración con FFmpeg: H.264/H.265, límite de bytes, recorte, audio, subtítulos de texto, nombres, colisiones y cancelación. Los subtítulos de imagen requieren validación con una muestra real.

El código está en app.py, engine.py, settings_dialog.py, local_ai.py y motors.py. Construir.ps1 genera el ejecutable; Instalar.ps1 instala la copia completa. FFmpeg, Qt/PySide6 y Ollama tienen sus propias licencias.

En 1.0.4, los motores se gestionan automáticamente: solo aparecen Actualizar motores y Reparar FFmpeg. La comprobación de versiones se realiza al abrir Configuración; ya no hay campos manuales de ejecutables. El acceso directo apunta a la instalación del perfil real de Windows, fuera de las carpetas internas de Codex.


En 1.0.5, el título MB COMPRENSOR IA y el subtítulo están centrados. Tema, Configuración y Acerca de usan iconos con ayudas accesibles. Configuración y Acerca de se abren como popups modales centrados con fondo atenuado. Acerca de incluye el logo, QR y enlace de MB Downloader. Se retiraron del paquete los módulos y motores de descarga; Ollama solo se consulta cuando se solicita una recomendación. El valor inicial vuelve a la carpeta del original; una selección posterior de Downloads se conserva.



Para publicar futuras actualizaciones, compila la versión con Construir.ps1 y genera sus archivos con Preparar-Release.ps1 -ReleaseVersion 1.0.6 (cambia la versión cuando corresponda). En el repositorio crea un Release estable con etiqueta v1.0.6 y adjunta el ZIP generado y SHA256SUMS. No subas solo el ejecutable: necesita la carpeta _internal y los motores. El actualizador espera el paquete ZIP de Windows de esta aplicación; un instalador de otro programa no se ejecuta automáticamente. El ZIP y la etiqueta deben corresponder a la versión de app.py. La instalación conserva respaldos en AppData\\Local\\MBCompresorPro\\updates y registra el resultado en last-result.json.


La versión 1.0.13 incorpora una transición de aparición al abrir (260 ms) y desvanecimiento al cerrar (220 ms), sin bloquear la interfaz. La cola se guarda antes de cerrar. Si hay una compresión o consulta de IA en curso, el cierre sigue protegido. La instalación de actualizaciones utiliza también este cierre animado antes de reiniciar.

