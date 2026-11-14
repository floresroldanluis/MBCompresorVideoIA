# Calidad por tamaño 1.0.46

## Diagnóstico comprobado

Se comparó el original con su MP4 de una película de 3:08:24 y 1920×1080. El MP4 mantuvo resolución y 24 FPS, pero el video H.264 Main recibió 865 kb/s. Pesó 1.691 GB decimales (1.575 GiB) para un máximo de 1.95 GB. Parte de la pérdida de detalle se explica por la fuerte reducción de bitrate; el margen fijo del 5% y un resultado NVENC menor al bitrate solicitado también dejaron espacio sin usar.

El tamaño máximo es un techo. Ni 1080p ni ocupar exactamente ese techo garantizan identidad visual con el original. Se busca retener más detalle sin excederlo, sin relleno y sin inventar detalle ausente.

## Cambios

- Perfil inicial H.265/HEVC con Full HD y máxima calidad; se puede seleccionar H.264 y la elección posterior se respeta.
- Presupuesto inicial del 98.5%, con verificación final estricta de bytes.
- Refino medido y acotado de NVIDIA cuando el resultado infrautiliza claramente el presupuesto y se espera una ganancia útil en bitrate de video. Se descuenta audio, subtítulos y contenedor; se conserva el mejor candidato válido si un intento adicional falla o supera el límite.
- NVENC H.264 usa High en lugar del Main predeterminado de este motor. Se conservan p6/hq, qres, AQ y lookahead; no se impone CQ fijo al modo con límite.
- HEVC puede copiar audio AAC/AC-3/E-AC-3 compatible con MP4 si el bitrate es conocido y cabe en el presupuesto, sin recorte ni downmix. H.264 con límite mantiene AAC. En la muestra real, E-AC-3 a 256 kb/s conservó los seis canales y los bytes del audio, evitó otra recodificación y dejó más espacio para el video. Se comprobó contenedor y decodificación local, no compatibilidad universal con todos los reproductores.
- No se reduce la resolución ni los FPS como parte de esta mejora; el modo adaptativo de resolución sigue siendo una opción explícita por video.

El refino puede requerir otra codificación. No es una promesa de mayor velocidad; prioriza detalle y mejor uso del límite. La CPU conserva sus dos pasadas y reutilización del análisis.

## Muestras de contenido real

Dos segmentos de 8 segundos, 1920×1080 y 24 FPS, comparados con el original decodificado. La primera comparación solicita 1150 kb/s de video sin audio. Una segunda prueba HEVC solicita 2200 kb/s con los mismos parámetros. VMAF evalúa uno de cada tres cuadros. No se ha recomprimido la película completa para esta comparación. Los bitrates locales del MP4 anterior varían por su VBR de película completa; la tabla no implica igual gasto de bytes en cada escena.

| Escena | MP4 anterior | H.264 Main | H.264 High | H.265/HEVC |
|---|---:|---:|---:|---:|
| 49:24, VMAF | 70.26 | 65.01 | 68.88 | 79.80 |
| 1:40:00, VMAF | 87.06 | 91.55 | 91.67 | 93.47 |

HEVC retuvo más detalle medido en ambas muestras. Los tiempos de codificación de cada muestra fueron aproximadamente 1.1–1.4 s para H.264 y 1.6 s para HEVC en este equipo; no incluyen toda la aplicación ni representan tiempos de una película completa. p7/qres produjo resultados idénticos a p6/qres en estas muestras. Fullres aumentó tiempo y bytes sin evidencia suficiente para activarlo globalmente.

También se midió HEVC a 2200 kb/s: VMAF 88.25 en la escena de 49:24 y 94.73 en la de 1:40:00, frente a 79.80 y 93.47 a 1150 kb/s. Con esta duración y audio de 256 kb/s, un límite de aproximadamente 3.5 GB permite ese orden de presupuesto de imagen. Es una alternativa para conservar más detalle; no una predicción exacta del tamaño o calidad de toda la película.

El fotograma original ya tiene cierta suavidad de enfoque. La codificación no recupera detalle ausente. VMAF es un indicador aproximado, no un porcentaje de calidad ni sustituto de la evaluación visual. Los videos y capturas del usuario permanecen locales.

## Referencias técnicas

- [NVIDIA: Using FFmpeg with NVIDIA GPU Hardware Acceleration](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/): control de bitrate, multipass y evaluación de calidad.
- [FFmpeg: Two-pass encoding](https://ffmpeg.org/ffmpeg.html): análisis y asignación de bitrate en dos pasadas.
