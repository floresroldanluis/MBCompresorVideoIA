"""Local FFmpeg compression engine (stdlib only).

Public API: discover_tools(), probe(path), CompressionOptions, Control,
compress(source, output_dir, options, control=None, progress=None, log=None).
Stream selections are absolute ffprobe stream indices. None audio selects all;
subtitles are opt-in text streams, converted to MP4 selectable mov_text tracks.
max_gb uses decimal GB. A result is published only after verifying its byte cap.
Callbacks run on the calling worker thread. No network or external AI service.
"""
from __future__ import annotations
import ctypes
import json
import math
import os
from pathlib import Path
import queue
import shutil
import sys
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

class EncodeError(RuntimeError): pass
class EncodeCancelled(EncodeError): pass

def _flags():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

def discover_tools():
    base = Path(__file__).resolve().parent
    appbase=Path(sys.executable).parent if getattr(sys,'frozen',False) else base
    roots = [Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local'/'MBCompresorPro'/'tools',appbase/'tools',base/'tools', base/'ffmpeg'/'bin', Path(os.environ.get('LOCALAPPDATA',''))/'MB Compresor de Video IA'/'ffmpeg'/'bin']
    def find(name):
        for root in roots:
            p = root/(name + ('.exe' if os.name == 'nt' else ''))
            if p.is_file(): return str(p)
        return shutil.which(name)
    return find('ffmpeg'), find('ffprobe')

def probe(path, ffprobe=None):
    ffprobe = ffprobe or discover_tools()[1]
    if not ffprobe: raise EncodeError('No se encontró FFprobe. Configura los motores.')
    p = Path(path).resolve()
    r = subprocess.run([ffprobe,'-v','error','-show_format','-show_streams','-of','json',str(p)], capture_output=True, text=True, encoding='utf-8',errors='replace', **_flags())
    if r.returncode: raise EncodeError(r.stderr[-2000:])
    data=json.loads(r.stdout); streams=data.get('streams',[])
    videos=[s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')]
    if not videos: raise EncodeError('El archivo no contiene una pista de video.')
    v=videos[0]
    duration=float(data.get('format',{}).get('duration') or v.get('duration') or 0)
    if not math.isfinite(duration) or duration<=0: raise EncodeError('No se pudo determinar la duración del video.')
    return dict(path=str(p),duration=duration,width=int(v['width']),height=int(v['height']),size=p.stat().st_size,video=v,audio=[s for s in streams if s.get('codec_type')=='audio'],subtitles=[s for s in streams if s.get('codec_type')=='subtitle'])

@dataclass
class CompressionOptions:
    mode: str='auto'
    max_gb: float=1.95
    unlimited: bool=False
    codec: str='h264'
    speed: str='calidad'
    height: int|None=1080
    audio_indices: list[int]|None=None
    subtitle_indices: list[int]=field(default_factory=list)
    subtitle_mode: str='selectable'
    fps: float|None=None
    audio_kbps: int=512
    trim_start: float=0
    trim_end: float|None=None
    local_ai: bool=False
    output_name: str|None=None
    downmix_audio: bool=False

class Control:
    def __init__(self):
        self.cancelled=threading.Event(); self.paused=threading.Event()
    def cancel(self): self.cancelled.set()
    def pause(self): self.paused.set()
    def resume(self): self.paused.clear()

def _suspend(proc, pause):
    if os.name=='nt':
        fn=getattr(ctypes.WinDLL('ntdll'), 'NtSuspendProcess' if pause else 'NtResumeProcess')
        fn.argtypes=[ctypes.c_void_p]; fn.restype=ctypes.c_long
        if fn(int(proc._handle)) != 0: raise EncodeError('No se pudo pausar/reanudar FFmpeg.')
    else:
        import signal
        os.kill(proc.pid, signal.SIGSTOP if pause else signal.SIGCONT)

MIN_FREE_BYTES = 2_000_000_000

class DiskSpaceGuard:
    def __init__(self, directory, log, interval=1.0):
        self.directory=Path(directory);self.log=log;self.interval=interval
        self.paused=False;self.next_check=0.
    def check(self, control):
        now=time.monotonic()
        if now<self.next_check:return self.paused
        self.next_check=now+self.interval
        try:free=shutil.disk_usage(self.directory).free
        except OSError as exc:raise EncodeError('No se pudo comprobar el espacio del disco de salida.') from exc
        low=free<MIN_FREE_BYTES
        if low!=self.paused:
            self.paused=low
            if low:self.log(f'Pausa automática: quedan {free/1e9:.2f} GB libres en {self.directory.anchor}; se requieren al menos 2 GB. Se reanudará al liberar espacio.')
            elif control.paused.is_set():self.log(f'Espacio recuperado: {free/1e9:.2f} GB libres. Se mantiene la pausa manual.')
            else:self.log(f'Espacio recuperado: {free/1e9:.2f} GB libres. Reanudando automáticamente la compresión.')
        return self.paused

def _hide_work_directory(directory, log):
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetFileAttributesW.argtypes=[ctypes.c_wchar_p];kernel.GetFileAttributesW.restype=ctypes.c_uint32
        kernel.SetFileAttributesW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32];kernel.SetFileAttributesW.restype=ctypes.c_int
        attributes=kernel.GetFileAttributesW(str(directory))
        if attributes==0xffffffff or not kernel.SetFileAttributesW(str(directory),attributes|2):
            log('No se pudo ocultar la carpeta temporal; la compresión puede continuar.')

def _run(cmd, control, duration, callback, pass_number, log, space_guard=None, total_passes=2, backend='cpu'):
    notified=False
    while space_guard and space_guard.check(control):
        if control.cancelled.is_set():raise EncodeCancelled('Compresión cancelada.')
        if callback and not notified:
            callback(dict(percent=(pass_number-1)*100/total_passes,speed=0,eta=None,pass_number=pass_number,total_passes=total_passes,backend=backend,status='Pausado: espacio insuficiente',disk_paused=True));notified=True
        control.cancelled.wait(.15)
    if control.cancelled.is_set():raise EncodeCancelled('Compresión cancelada.')
    lines=queue.Queue(); tail=[]
    proc=subprocess.Popen(cmd, stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace', **_flags())
    def reader():
        for line in proc.stdout: lines.put(line.rstrip())
        lines.put(None)
    threading.Thread(target=reader,daemon=True).start(); suspended=False; speed=0.; seconds=0.;last_status=None
    def report(status):
        if callback:callback(dict(percent=min(99.5,((pass_number-1)+seconds/duration)*100/total_passes),speed=0 if suspended else speed,eta=None if suspended else max(0,duration-seconds)/speed if speed>0 else None,pass_number=pass_number,total_passes=total_passes,backend=backend,status=status,disk_paused=bool(space_guard and space_guard.paused)))
    try:
        while True:
            if control.cancelled.is_set(): raise EncodeCancelled('Compresión cancelada.')
            disk_paused=space_guard.check(control) if space_guard else False
            desired_pause=control.paused.is_set() or disk_paused
            if desired_pause!=suspended and proc.poll() is None:
                _suspend(proc,desired_pause); suspended=desired_pause
            status='Pausado: espacio insuficiente' if disk_paused else 'Pausado' if control.paused.is_set() else 'Analizando video' if pass_number==1 and total_passes==2 else 'Comprimiendo'
            if status!=last_status:report(status);last_status=status
            try: line=lines.get(timeout=.15)
            except queue.Empty:
                if proc.poll() is not None: break
                continue
            if line is None: break
            tail.append(line); tail=tail[-35:]
            if line.startswith('speed='):
                try: speed=float(line.split('=',1)[1].rstrip('x'))
                except ValueError: pass
            if line.startswith('out_time_us='):
                try: seconds=float(line.split('=',1)[1])/1e6
                except ValueError: continue
                report(status)
        if proc.wait(): raise EncodeError('\n'.join(tail)[-5000:])
    finally:
        if proc.poll() is None:
            if suspended: _suspend(proc,False)
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait()
        proc.stdout.close()

def compress(source, output_dir, options, control=None, progress=None, log=None, ffmpeg=None, ffprobe=None):
    control=control or Control(); log=log or (lambda message: None)
    ffmpeg=ffmpeg or discover_tools()[0]
    if not ffmpeg: raise EncodeError('No se encontró FFmpeg.')
    if options.mode not in ('auto','nvidia','cpu'):raise EncodeError('Modo de compresión no válido.')
    primary_ffmpeg=ffmpeg;backend='cpu'
    if options.mode!='cpu':
        from acceleration import find_nvenc
        status=find_nvenc(ffmpeg,options.codec)
        if status['available']:ffmpeg=status['path'];backend='nvidia'
        elif options.mode=='nvidia':raise EncodeError('NVIDIA no está disponible. Usa Actualizar motores o Reparar FFmpeg. '+status['error'])
        else:log('Automático: NVIDIA no está disponible; se usará CPU. '+status['error'])
    log(('NVIDIA NVENC: codificación por GPU con alta calidad.' if backend=='nvidia' else 'CPU: codificación de dos pasadas para priorizar calidad por tamaño.'))
    info=probe(source,ffprobe); source=Path(source).resolve(); out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    if options.codec not in ('h264','h265'): raise EncodeError('Códec no válido.')
    if not options.unlimited and (not math.isfinite(options.max_gb) or options.max_gb<=0): raise EncodeError('El límite de tamaño debe ser positivo.')
    cap=None if options.unlimited else int(options.max_gb*1_000_000_000)
    output_name=options.output_name
    if output_name is not None:
        output_name=output_name.strip()
        if (not output_name or Path(output_name).name!=output_name or
            any(c in output_name for c in '<>:"/\\|?*') or
            not output_name.lower().endswith('.mp4') or output_name.endswith(' ') or
            output_name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}):
            raise EncodeError('El nombre de salida debe ser un nombre de archivo MP4 válido, sin carpetas.')
        if not output_name[:-4].strip('. '): raise EncodeError('El nombre de salida no puede estar vacío.')
    start=float(options.trim_start); end=float(options.trim_end) if options.trim_end is not None else info['duration']
    if not 0<=start<end<=info['duration']+.01: raise EncodeError('Intervalo de recorte no válido.')
    duration=end-start
    audio=[s for s in info['audio'] if options.audio_indices is None or s['index'] in options.audio_indices]
    subs=[s for s in info['subtitles'] if s['index'] in options.subtitle_indices]
    valid={s['index'] for s in info['audio']}
    if options.audio_indices is not None and not set(options.audio_indices)<=valid: raise EncodeError('Pista de audio no válida.')
    if len(subs)!=len(set(options.subtitle_indices)): raise EncodeError('Pista de subtítulos no válida.')
    text_subs={'subrip','srt','ass','ssa','mov_text','webvtt','text','subviewer'}
    if options.subtitle_mode not in ('selectable','burn'): raise EncodeError('Modo de subtítulos no válido.')
    burn=options.subtitle_mode=='burn' and bool(subs)
    if burn and len(subs)!=1: raise EncodeError('Elige una sola pista para incrustar en la imagen.')
    if not burn and any(s.get('codec_name') not in text_subs for s in subs): raise EncodeError('Los subtítulos de imagen requieren el modo incrustado.')
    audio_rate=min(max(32,int(options.audio_kbps)),512)*1000
    bitrate=0
    if not options.unlimited:
        budget=cap*8/duration*.95
        if audio: audio_rate=min(audio_rate,int(budget*.25/len(audio)))
        if audio and audio_rate<24000: raise EncodeError('El límite es demasiado pequeño para conservar las pistas de audio.')
        bitrate=int(budget-audio_rate*len(audio))
        if bitrate<20000: raise EncodeError('El límite de tamaño es demasiado pequeño para este video.')
    else:
        log('Sin límite de tamaño: calidad visual alta (CRF/CQ 18), sin reducir bitrate para alcanzar GB. El tamaño final depende del contenido.')
    height=min(info['height'],options.height or info['height']); width=info['width']
    if height<2: raise EncodeError('Resolución no válida.')
    if options.local_ai and not options.unlimited:
        # Offline adaptive bitrate-per-pixel heuristic; explicitly not a neural model.
        fps=info['video'].get('avg_frame_rate','30/1')
        try:
            n,d=map(float,fps.split('/')); fps=n/d if d else 30
        except (ValueError,ZeroDivisionError): fps=30
        while height>480 and bitrate/(width*height*(height/info['height'])*max(1,fps))<.035:
            height=max(480, int(height*.8))
        log(f'Ajuste adaptativo: resolución hasta {height}p, video {bitrate//1000} kb/s; cálculo heurístico local.')
    vf=f'scale=-2:{height//2*2}:flags=lanczos,setsar=1,format=yuv420p'
    if options.fps is not None:
        if not math.isfinite(options.fps) or not 1<=options.fps<=240: raise EncodeError('Los FPS deben estar entre 1 y 240.')
        vf+=f',fps={options.fps}'
    bitmap_burn=burn and subs[0].get('codec_name') not in text_subs
    if burn and not bitmap_burn:
        # FFmpeg subtitle filter paths need their own escaping, independent of shell quoting.
        escaped=source.as_posix().replace('\\','\\\\').replace(':','\\:').replace("'", "'\\\\''").replace(',','\\,').replace('[','\\[').replace(']','\\]')
        relative=next(i for i,s in enumerate(info['subtitles']) if s['index']==subs[0]['index'])
        vf=f"subtitles=filename='{escaped}':si={relative},"+vf
    preset={'rapido':'veryfast','equilibrado':'medium','calidad':'slow'}.get(options.speed,options.speed)
    if options.mode=='cpu':preset='slow'
    if preset not in ('ultrafast','superfast','veryfast','faster','fast','medium','slow','slower','veryslow'): raise EncodeError('Velocidad no válida.')
    with tempfile.TemporaryDirectory(prefix='.mb-compress-',dir=out) as temp:
        temp=Path(temp); result=temp/'result.mp4'; stats=temp/'pass'
        _hide_work_directory(temp,log)
        space_guard=DiskSpaceGuard(out,log)
        for attempt in range(4):
            gpu=backend=='nvidia';total_passes=1 if gpu or options.unlimited else 2
            codec=('h264_nvenc' if options.codec=='h264' else 'hevc_nvenc') if gpu else ('libx264' if options.codec=='h264' else 'libx265')
            log(f'Intento {attempt+1}: '+('calidad constante · ' if options.unlimited else f'{bitrate//1000} kb/s · ')+('NVIDIA, una codificación con análisis interno.' if gpu else 'CPU, calidad constante.' if options.unlimited else 'CPU, dos pasadas.'))
            try:
                for pn in range(1,total_passes+1):
                    first_analysis=not gpu and not options.unlimited and pn==1
                    log('Compresión NVIDIA: GPU con análisis adaptativo de calidad.' if gpu else 'Compresión CPU: calidad constante sin límite de tamaño.' if options.unlimited else 'Etapa 1/2: análisis del video para distribuir la calidad.' if pn==1 else 'Etapa 2/2: compresión y creación del MP4.')
                    cmd=[ffmpeg,'-hide_banner','-y','-nostdin','-loglevel','warning','-ss',str(start),'-i',str(source)]
                    if not options.unlimited or options.trim_end is not None:cmd+=['-t',str(duration)]
                    if bitmap_burn:
                        cmd+=['-filter_complex',f"[0:{info['video']['index']}][0:{subs[0]['index']}]overlay=eof_action=pass,{vf}[v]",'-map','[v]']
                    else: cmd+=['-map',f"0:{info['video']['index']}",'-vf',vf]
                    if gpu:
                        gpu_preset={'rapido':'p4','equilibrado':'p5','calidad':'p6'}.get(options.speed,'p5')
                        cmd+=['-c:v',codec,'-preset',gpu_preset,'-tune','hq','-rc','vbr','-cq','18' if options.unlimited else '19','-b:v','0' if options.unlimited else str(bitrate)]
                        if not options.unlimited:cmd+=['-maxrate',str(int(bitrate*1.5)),'-bufsize',str(bitrate*2)]
                        cmd+=['-multipass','qres','-spatial-aq','1','-aq-strength','8','-rc-lookahead','20','-bf','3']
                    else:
                        cmd+=['-c:v',codec,'-preset',preset]
                        if options.unlimited:cmd+=['-crf','18']
                        else:cmd+=['-b:v',str(bitrate)]
                        if not options.unlimited and codec=='libx264':cmd+=['-pass',str(pn),'-passlogfile',str(stats)]
                        elif not options.unlimited:cmd+=['-x265-params',f'pass={pn}:stats={stats.as_posix().replace(":", "\\:")}']
                    if first_analysis:cmd+=['-an','-sn','-f','null',os.devnull]
                    else:
                        for s in audio+([] if burn else subs): cmd+=['-map',f"0:{s['index']}"]
                        cmd+=['-c:a','aac','-b:a',str(audio_rate),'-c:s','mov_text','-map_metadata','0','-movflags','+faststart']
                        if options.downmix_audio:cmd+=['-ac','2']
                        if options.unlimited and not options.downmix_audio:
                            for index,stream in enumerate(audio):
                                if stream.get('codec_name') in ('aac','ac3','eac3','alac','mp3'):
                                    cmd+=['-c:a:'+str(index),'copy']
                                    log('Audio '+str(stream['index'])+': copia sin recodificar.')
                                else:
                                    cmd+=['-c:a:'+str(index),'alac']
                                    log('Audio '+str(stream['index'])+': ALAC sin pérdida adicional para MP4.')
                        if options.codec=='h265':cmd+=['-tag:v','hvc1']
                        cmd+=[str(result)]
                    cmd[1:1]=['-progress','pipe:1','-stats_period','0.3']
                    _run(cmd,control,duration,progress,pn,log,space_guard,total_passes,backend)
            except EncodeCancelled:raise
            except EncodeError as exc:
                if gpu and options.mode=='auto':
                    log('Automático: falló NVIDIA; se reintentará con CPU. '+str(exc)[-500:])
                    backend='cpu';ffmpeg=primary_ffmpeg;continue
                raise
            actual=result.stat().st_size
            if options.unlimited or actual<=cap: break
            bitrate=int(bitrate*cap/actual*.93); log('Se ajustará el bitrate para cumplir el tamaño máximo.')
            if bitrate<10000: raise EncodeError('El límite solicitado no permite una salida válida.')
        else: raise EncodeError('No se alcanzó el tamaño máximo; no se publicó ningún archivo.')
        if control.cancelled.is_set(): raise EncodeCancelled('Compresión cancelada.')
        # Reserve each candidate exclusively, then replace reservation atomically.
        for i in range(10000):
            stem=Path(output_name).stem if output_name is not None else source.stem
            target=out/(stem+(f'_{i}' if i else '')+'.mp4')
            if target==source: continue
            try: fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY); os.close(fd)
            except FileExistsError: continue
            try: os.replace(result,target)
            except BaseException: target.unlink(missing_ok=True); raise
            if progress: progress(dict(percent=100,speed=0,eta=0,pass_number=total_passes,total_passes=total_passes,backend=backend,status='Completado'))
            log(f'Guardado: {target.name} ({actual/1e6:.2f} MB).')
            return target
        raise EncodeError('No se pudo elegir un nombre de salida libre.')
