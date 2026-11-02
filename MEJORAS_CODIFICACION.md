# Codificación 1.0.41 revisada con Astra

## Cambios conservados

- NVIDIA con límite: VBR con objetivo de bitrate, sin imponer simultáneamente CQ fijo. El modo sin límite conserva CQ18.
- Se mantienen p6/hq, análisis qres, lookahead y AQ; se descartaron variantes fullres y temporal AQ que produjeron más reintentos en estas mediciones.
- CPU mantiene el preset slow y dos pasadas; si la salida excede el límite, reutiliza el análisis y solo repite la codificación final.
- El ajuste de reintentos descuenta audio, subtítulos y contenedor medidos para reducir solo el presupuesto del video.
- Audio AAC ya compatible, sin recorte ni downmix y con bitrate conocido que cabe en el presupuesto: copia exacta, sin pérdida generacional. Su bitrate real libera la reserva sobrante para el video. Otras pistas mantienen la conversión configurada.
- Se mantienen la comprobación final de bytes, pausa automática bajo 2 GB, cancelación y conservación del original.

## Muestra y método

Referencia sintética testsrc2 FFV1 sin pérdida, 1920×1080, 30 FPS, 10 segundos, audio PCM convertido a AAC128. Comparación del motor 1.0.40 frente al nuevo, misma muestra y límite decimal de 2 MB. VMAF con FFmpeg/libvmaf, muestreo de 1 de cada 3 cuadros. Los tiempos incluyen comprobaciones del motor. Había otra compresión del usuario activa: los tiempos son orientativos, no un benchmark aislado ni una promesa universal. VMAF es un indicador aproximado y no reemplaza la inspección visual de contenido real.

| Códec NVIDIA | Versión | Tiempo (s) | Tamaño (bytes) | VMAF | Codificaciones completas |
|---|---|---:|---:|---:|---:|
| h264 | 1.0.40 | 5.341 | 1887600 | 87.327 | 2 |
| h265 | 1.0.40 | 7.424 | 1935989 | 83.413 | 2 |
| h264 | 1.0.41 | 2.455 | 1916132 | 87.419 | 1 |
| h265 | 1.0.41 | 6.952 | 1989268 | 84.031 | 2 |

H.264 evitó la segunda codificación y mantuvo una calidad medida similar. H.265 necesitó un reajuste en esta muestra, con una calidad medida ligeramente mayor. En la muestra de 6 MB con el audio configurado a512kbps, ambos códecs pasaron de dos codificaciones a una y VMAF se mantuvo similar o ligeramente mejor. Con un límite muy ajustado, cada decisión de presupuesto puede cambiar el equilibrio entre tamaño y detalle; no se garantiza que todos los videos mejoren simultáneamente en tiempo, calidad y tamaño.

La CPU no se acelera por sustituir slow con un preset inferior. Su ganancia está en reutilizar trabajo cuando hay reintentos. No hay un modelo Astra ejecutándose dentro de la aplicación: Astra se utilizó para revisar y mejorar el código. Los videos siguen procesándose localmente.

## Referencias técnicas

- NVIDIA: [Using FFmpeg with NVIDIA GPU Hardware Acceleration](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/ffmpeg-with-nvidia-gpu/index.html). VBR por bitrate y VBR-CQ tienen objetivos distintos.
- x265: [CLI multipass](https://x265.readthedocs.io/en/master/cli.html). Las estadísticas del primer paso se reutilizan en el segundo.
