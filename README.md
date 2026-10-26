# MB Compresor de Video IA

<p align="center"><img src="assets/mb-logo-02.png" width="180" alt="MB Compresor de Video IA"></p>

Compresor de escritorio para Windows de 64 bits. Exporta videos locales a MP4 de alta calidad con control de tamaño máximo. Versión **1.0.39**.

## Descargar

En [Releases](https://github.com/floresroldanluis/MBCompresorVideoIA/releases/latest), descarga **MBCompresorVideoIA-Setup-v1.0.39-windows-x64.exe** y sigue el asistente. Incluye FFmpeg y FFprobe; no requiere Python. Para usar la versión portátil, extrae toda la carpeta del ZIP antes de abrir `MB Compresor Pro.exe`.

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

`Preparar-Release.ps1 -ReleaseVersion 1.0.39` genera el ZIP del actualizador y `SHA256SUMS`. La etiqueta del Release, el paquete y `app.py` deben usar la misma versión.

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
