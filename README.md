# MB Compresor de Video IA

<p align="center"><img src="assets/mb-logo-02.png" width="180" alt="MB Compresor de Video IA"></p>

Compresor de escritorio para Windows de 64 bits. Exporta videos locales a MP4 de alta calidad con control de tamaño máximo. Versión **1.0.45**.

## Descargar

En [Releases](https://github.com/floresroldanluis/MBCompresorVideoIA/releases/latest), descarga **MBCompresorVideoIA-Setup-v1.0.45-windows-x64.exe** y sigue el asistente. Incluye FFmpeg y FFprobe; no requiere Python. Para usar la versión portátil, extrae toda la carpeta del ZIP antes de abrir `MB Compresor Pro.exe`.

## Funciones

- Cola con arrastrar y soltar, selección de archivos y carpetas.
- MP4 H.264/H.265 con Automático, NVIDIA rápido con alta calidad y CPU para priorizar calidad por tamaño.
- Límite decimal en GB con verificación final, o calidad constante sin límite de tamaño. La casilla sin límite siempre inicia desmarcada.
- Cola dinámica: agregar o quitar pendientes durante la compresión. Desplazamiento manual libre y enfoque automático solo al iniciar el siguiente video.
- Estado explícito de instalación, compatibilidad y actualización de motores.
- 1080p sin ampliar videos pequeños, selección de audio y subtítulos, recorte y FPS.
- Pausar, reanudar, detener y consultar el progreso.
- Salida junto al original por defecto, nombres seguros y originales conservados.
- Recomendaciones opcionales con IA local, temas y actualizaciones desde GitHub.

La compresión tiene pérdida: se busca conservar la mejor calidad posible dentro del tamaño solicitado. Los formatos admitidos dependen de FFmpeg.

## IA local opcional

Instala [Ollama](https://ollama.com/), ejecuta `ollama pull qwen3:0.6b` y revisa su conexión en Configuración. El modelo se instala por separado en cada PC. La compresión funciona sin el asesor; solo se consultan metadatos técnicos.

## Ejecutar y compilar

Con Python 3.14 en Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

FFmpeg/FFprobe pueden estar en PATH o en `tools/`. Configuración también permite reparar los motores.

Para compilar, coloca `ffmpeg.exe` y `ffprobe.exe` en `tools/` (puedes obtenerlos del ZIP publicado) y ejecuta `Construir.ps1`. Con [Inno Setup 6](https://jrsoftware.org/isinfo.php) instalado, `Construir-Instalador.ps1` genera el instalador.

`Preparar-Release.ps1 -ReleaseVersion 1.0.45` genera el ZIP del actualizador y `SHA256SUMS`. La etiqueta del Release, el paquete y `app.py` deben usar la misma versión.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m unittest test_engine test_app_updates -v
```

Las pruebas de compresión requieren FFmpeg/FFprobe; las del instalador del actualizador usan PowerShell en Windows.

## Componentes

FFmpeg/FFprobe, Qt/PySide6 y Ollama conservan sus propias licencias. Motores Windows: [Gyan](https://www.gyan.dev/ffmpeg/builds/). Código y licencias de FFmpeg: [ffmpeg.org](https://ffmpeg.org/).

[Luis Flores Roldán](https://linktr.ee/floresroldanluis)

## Complemento NVIDIA para compilar

Además de FFmpeg y FFprobe en `tools/`, el empaquetado requiere `tools/nvenc/ffmpeg.exe`. Para obtener el componente fijado y comprobar su SHA256, desde el entorno Python del proyecto:

```powershell
.\.venv\Scripts\python.exe -c "from acceleration import COMPAT_URL,COMPAT_SHA256; from motors import _archive; _archive(COMPAT_URL,None,['ffmpeg'],'tools/nvenc',expected_sha256=COMPAT_SHA256)"
```

El instalador y ZIP publicados ya incluyen este complemento. No requieren CUDA Toolkit. La disponibilidad de NVENC depende de la GPU y su controlador.

## Optimización de codificación 1.0.41

Revisada con Astra: VBR por tamaño en NVIDIA para reducir reintentos, análisis CPU reutilizable y copia de audio AAC compatible sin pérdida adicional cuando cabe en el presupuesto. No cambia el códec elegido ni reduce la resolución para acelerar. [Mediciones y límites de la comparación](MEJORAS_CODIFICACION.md).

## Guardado y Full HD 1.0.43

El MP4 se publica sin crear un archivo vacío que pueda bloquearse. Los bloqueos de Windows se reintentan hasta20segundos; si persisten, el archivo completo se conserva y puede guardarse con Reintentar guardado, sin recodificar. Recuperar guardados pendientes permite recuperar esas filas después de reiniciar manteniendo la cola inicialmente vacía.

Full HD limita a1920×1080 conservando proporción y sin ampliar videos pequeños. El perfil de alta calidad y audio512kbps es el predeterminado; el detalle alcanzable depende del origen y del límite deGB elegido.

Actualizar motores comprueba FFmpeg, FFprobe y NVIDIA compatible, informa cuando no puede verificar versiones y conserva respaldos si Windows bloquea una sustitución. yt-dlp y Deno no son requisitos de la compresión local. No se instalan controladores gráficos ni CUDA Toolkit.

Pruebas:96 aprobadas y1omitida por permisos de symlink. Incluyen bloqueos Windows reales, publicación concurrente, recuperación sin recodificar, Full HD y límites de tamaño.

## Nombres de salida 1.0.45

El MP4 final conserva el nombre original: `Video.mkv` → `Video.mp4`. Si ya existe, se usan `Video_1.mp4`, `Video_2.mp4`, etc. Los nombres personalizados siguen la misma regla. El original y los archivos existentes nunca se sobrescriben, incluso al guardar simultáneamente.

Los identificadores aleatorios se usan únicamente en temporales `.part`, con contenedor MP4 explícito. Se conservan los reintentos ante bloqueos y la recuperación sin volver a comprimir. Restart Manager intenta identificar qué aplicaciones usan el archivo mediante una consulta local; no se fuerzan cierres de programas ni servicios.
