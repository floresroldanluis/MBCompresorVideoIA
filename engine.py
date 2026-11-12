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
import re
import shutil
import sys
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field

class EncodeError(RuntimeError): pass
class EncodeCancelled(EncodeError): pass

class OutputPublishError(EncodeError):
    """A verified MP4 is intact at recovery_path and can be saved again."""
    cancelled=False
    def __init__(self, message, recovery_path, desired_target):
        self.recovery_path=Path(recovery_path)
        self.desired_target=Path(desired_target)
        super().__init__(f'{message}\nMP4 completo conservado en: {self.recovery_path}')

class OutputPublishCancelled(OutputPublishError, EncodeCancelled):
    cancelled=True

PUBLISH_RETRY_SECONDS=20.0
PUBLISH_RETRY_INTERVAL=.25


def finalize_output(result, desired_target, control=None, log=None, source=None):
    """Save an already verified MP4, including one retained after a failed save.

    No existing path is replaced. Windows rename reserves and publishes the name
    in one atomic operation, avoiding an empty .mp4 that Explorer/AV can open.
    The caller owns the result's work directory and its eventual cleanup.
    """
    result=Path(result).resolve(); desired_target=Path(desired_target)
    desired_target=desired_target.parent.resolve()/desired_target.name
    source=Path(source).resolve() if source is not None else None
    control=control or Control(); log=log or (lambda message: None)
    if result==source or result==desired_target:
        raise OutputPublishError('La ruta del MP4 pendiente no es válida para guardar.',result,desired_target)
    try:
        if result.stat().st_size<=0:
            raise OutputPublishError('El MP4 pendiente está vacío.',result,desired_target)
    except OSError as exc:
        raise OutputPublishError(f'No se pudo acceder al MP4 pendiente: {exc}',result,desired_target) from exc
    deadline=time.monotonic()+PUBLISH_RETRY_SECONDS
    waiting=False
    for index in range(10000):
        target=desired_target.with_name(desired_target.stem+(f'_{index}' if index else '')+desired_target.suffix)
        if target==source:continue
        while True:
            if control.cancelled.is_set():
                raise OutputPublishCancelled('Guardado cancelado; puedes reintentarlo sin volver a comprimir.',result,desired_target)
            try:
                if os.name=='nt':
                    # Unlike os.replace, Windows rename never overwrites a path
                    # another queue or application created in the meantime.
                    os.rename(result,target)
                else:
                    # Both paths are on the output volume. link is exclusive on
                    # POSIX, where rename would silently replace an existing file.
                    os.link(result,target)
                    try:result.unlink()
                    except OSError:pass  # The caller cleans up this leftover link.
            except FileExistsError:
                break
            except OSError as exc:
                remaining=deadline-time.monotonic()
                if getattr(exc,'winerror',None) not in (32,33):
                    raise OutputPublishError(f'No se pudo guardar el MP4: {exc}',result,desired_target) from exc
                if remaining<=0:
                    message='Windows mantiene el archivo ocupado. Puedes reintentar el guardado sin volver a comprimir.'
                    try:
                        from file_locks import describe_locking_processes
                        detail=describe_locking_processes([result,target])
                    except Exception:
                        detail=''
                    log(detail or 'Windows mantiene el archivo ocupado; no se pudo identificar el proceso que lo bloquea.')
                    if detail:message+='\n'+detail
                    raise OutputPublishError(message,result,desired_target) from exc
                if not waiting:
                    log(f'MP4 completado. Windows mantiene un archivo ocupado; esperando hasta {PUBLISH_RETRY_SECONDS:g} s para guardar {target.name}.')
                    waiting=True
                control.cancelled.wait(min(PUBLISH_RETRY_INTERVAL,remaining))
                continue
            if waiting:log('Archivo liberado; el MP4 completo se ha guardado.')
            return target
    raise OutputPublishError('No se pudo elegir un nombre de salida libre.',result,desired_target)

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

def _file_identity(path):
    stat=path.stat()
    return [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]


def _analysis_file_name(name):
    return bool(re.fullmatch(r'pass(?:-\d+\.log(?:\.mbtree)?|(?:\.cutree)?)(?:\.temp)?',name))


def _work_result_name(name):
    return bool(re.fullmatch(r'payload_[0-9a-f]{12}\.part',name))


def _reveal_recovery_directory(directory, desired_target, log, result):
    try:
        owned_files={}
        for item in directory.iterdir():
            if _analysis_file_name(item.name) and item.is_file() and not item.is_symlink():
                owned_files[item.name]=_file_identity(item)
        marker={'owner':'MBCompresorPro.output-recovery.v2','directory':str(directory),
                'desired_target':str(desired_target),'result_name':result.name,
                'result_identity':_file_identity(result),'files':owned_files}
        # An unrelated file added to the workspace must never be overwritten.
        with (directory/'recovery.json').open('x',encoding='utf-8') as manifest:
            json.dump(marker,manifest,ensure_ascii=False)
    except OSError as exc:
        log(f'No se pudo registrar la carpeta de recuperación: {exc}')
    if os.name=='nt':
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetFileAttributesW.argtypes=[ctypes.c_wchar_p];kernel.GetFileAttributesW.restype=ctypes.c_uint32
        kernel.SetFileAttributesW.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32];kernel.SetFileAttributesW.restype=ctypes.c_int
        attributes=kernel.GetFileAttributesW(str(directory))
        if attributes!=0xffffffff:
            kernel.SetFileAttributesW(str(directory),attributes&~2)
    log(f'Guardado pendiente. MP4 completo conservado en: {result}')


def cleanup_recovery_workspace(result_path, log=None):
    """Remove only unchanged work files recorded when this recovery was made.

    Call after finalize_output succeeds. Unknown or changed files are retained;
    a directory without our recovery marker is never cleaned up.
    """
    result=Path(result_path).resolve(); directory=result.parent
    if (result.name!='result.mp4' and not _work_result_name(result.name) or
        not directory.name.startswith('.mb-compress-') or result.exists()):
        return False
    manifest=directory/'recovery.json'
    try:
        if manifest.is_symlink():return False
        manifest_identity=_file_identity(manifest)
        marker=json.loads(manifest.read_text(encoding='utf-8'))
        if marker.get('directory')!=str(directory):return False
        if marker.get('owner')=='MBCompresorPro.output-recovery.v1':
            if result.name!='result.mp4':return False
        elif marker.get('owner')=='MBCompresorPro.output-recovery.v2':
            identity=marker.get('result_identity')
            if (not _work_result_name(result.name) or marker.get('result_name')!=result.name or
                not isinstance(identity,list) or len(identity)!=4 or
                any(type(value) is not int for value in identity)):return False
        else:return False
        for name,identity in marker.get('files',{}).items():
            if not _analysis_file_name(name):continue
            path=directory/name
            if path.is_symlink():continue
            try:
                if path.is_file() and _file_identity(path)==identity:path.unlink()
            except FileNotFoundError:pass
        if manifest.is_symlink() or _file_identity(manifest)!=manifest_identity:return False
        manifest.unlink()
        directory.rmdir()
        return True
    except (OSError,ValueError,TypeError,AttributeError) as exc:
        if log:log(f'La carpeta de trabajo se conserva: {directory} ({exc})')
        return False


def _retry_video_bitrate(result, bitrate, cap, duration, audio_bitrate, ffprobe):
    """Reduce video only: audio, subtitles and container bytes do not shrink with it."""
    actual=Path(result).stat().st_size
    fixed=min(audio_bitrate*duration/8, actual*.75)
    try:
        video=probe(result,ffprobe)['video']
        video_bytes=float(video['bit_rate'])*float(video['duration'])/8
        if math.isfinite(video_bytes) and 0<video_bytes<=actual:
            fixed=actual-video_bytes
    except (EncodeError,OSError,ValueError,KeyError,TypeError):
        # Some muxers omit stream bit_rate. The selected audio budget is a
        # conservative fallback; every subsequent result is still byte-checked.
        pass
    available=cap*.95-fixed
    if available<=0:
        raise EncodeError('El límite solicitado no permite conservar el audio y los subtítulos.')
    return int(bitrate*min(.95,available/(actual-fixed)))

def _capped_audio_copy_rates(audio, options, audio_rate):
    """Copy eligible AAC tracks only when their known bitrate fits the budget."""
    rates=[None]*len(audio)
    if options.downmix_audio or options.trim_start!=0 or options.trim_end is not None:
        return rates
    for index,stream in enumerate(audio):
        if stream.get('codec_name')!='aac':continue
        try:rate=int(stream.get('bit_rate',0))
        except (TypeError,ValueError,OverflowError):continue
        if 0<rate<=audio_rate:rates[index]=rate
    return rates

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
    log(('NVIDIA NVENC: codificación por GPU con alta calidad.' if backend=='nvidia' else 'CPU: codificación de calidad constante sin límite de tamaño.' if options.unlimited else 'CPU: codificación de dos pasadas para priorizar calidad por tamaño.'))
    info=probe(source,ffprobe); source=Path(source).resolve(); out=Path(output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    if options.codec not in ('h264','h265'): raise EncodeError('Códec no válido.')
    if not options.unlimited and (not math.isfinite(options.max_gb) or options.max_gb<=0): raise EncodeError('El límite de tamaño debe ser positivo.')
    cap=None if options.unlimited else int(options.max_gb*1_000_000_000)
    output_name=options.output_name
    if output_name is not None:
        output_name=output_name.strip()
        if (not output_name or Path(output_name).name!=output_name or
            any(c in output_name for c in '<>:"/\\|?*') or
            any(ord(c)<32 for c in output_name) or
            not output_name.lower().endswith('.mp4') or output_name.endswith(' ') or
            output_name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}):
            raise EncodeError('El nombre de salida debe ser un nombre de archivo MP4 válido, sin carpetas.')
        if not output_name[:-4].strip('. '): raise EncodeError('El nombre de salida no puede estar vacío.')
        if len(output_name[:-4].encode('utf-16-le'))>360:
            raise EncodeError('El nombre base de salida es demasiado largo; usa hasta 180 caracteres.')
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
    bitrate=0;audio_copy_rates=[None]*len(audio);audio_budget=audio_rate*len(audio)
    if not options.unlimited:
        budget=cap*8/duration*.95
        if audio: audio_rate=min(audio_rate,int(budget*.25/len(audio)))
        if audio and audio_rate<24000: raise EncodeError('El límite es demasiado pequeño para conservar las pistas de audio.')
        audio_copy_rates=_capped_audio_copy_rates(audio,options,audio_rate)
        audio_budget=sum(rate if rate is not None else audio_rate for rate in audio_copy_rates)
        bitrate=int(budget-audio_budget)
        if bitrate<20000: raise EncodeError('El límite de tamaño es demasiado pequeño para este video.')
    else:
        log('Sin límite de tamaño: calidad visual alta (CRF/CQ 18), sin reducir bitrate para alcanzar GB. El tamaño final depende del contenido.')
    height=min(info['height'],options.height or info['height']); width=info['width']
    if options.height:
        max_width=round(options.height*16/9/2)*2
        height=min(height,int(info['height']*max_width/width))
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
    if output_name is not None:
        stem=Path(output_name).stem
    else:
        # Leave room for the suffix on Windows, including names with surrogate pairs.
        stem=source.stem.encode('utf-16-le')[:336].decode('utf-16-le',errors='ignore').rstrip('. ')+'_comprimido'
    desired_target=out/(stem+'_'+uuid.uuid4().hex[:12]+'.mp4')
    temp=Path(tempfile.mkdtemp(prefix='.mb-compress-',dir=out))
    preserve_result=False
    try:
        # A .part file avoids advertising an unfinished video to thumbnail handlers.
        # Its muxer is specified explicitly rather than inferred from the extension.
        result=temp/('payload_'+uuid.uuid4().hex[:12]+'.part'); stats=temp/'pass'
        _hide_work_directory(temp,log)
        space_guard=DiskSpaceGuard(out,log)
        analysis_ready=False
        for attempt in range(4):
            gpu=backend=='nvidia'
            passes=(1,) if gpu or options.unlimited else (2,) if analysis_ready else (1,2)
            total_passes=len(passes)
            codec=('h264_nvenc' if options.codec=='h264' else 'hevc_nvenc') if gpu else ('libx264' if options.codec=='h264' else 'libx265')
            log(f'Intento {attempt+1}: '+('calidad constante · ' if options.unlimited else f'{bitrate//1000} kb/s · ')+('NVIDIA, una codificación con análisis interno.' if gpu else 'CPU, calidad constante.' if options.unlimited else 'CPU, análisis reutilizado.' if analysis_ready else 'CPU, dos pasadas.'))
            if not gpu and not options.unlimited and analysis_ready:
                log('Se reutiliza el análisis CPU del mismo video; solo se repite la compresión con el bitrate ajustado.')
            try:
                for progress_pass,pn in enumerate(passes,1):
                    first_analysis=not gpu and not options.unlimited and pn==1
                    log('Compresión NVIDIA: GPU con análisis adaptativo de calidad.' if gpu else 'Compresión CPU: calidad constante sin límite de tamaño.' if options.unlimited else 'Compresión CPU con análisis reutilizado.' if total_passes==1 else 'Etapa 1/2: análisis del video para distribuir la calidad.' if pn==1 else 'Etapa 2/2: compresión y creación del MP4.')
                    cmd=[ffmpeg,'-hide_banner','-y','-nostdin','-loglevel','warning','-ss',str(start),'-i',str(source)]
                    if not options.unlimited or options.trim_end is not None:cmd+=['-t',str(duration)]
                    if bitmap_burn:
                        cmd+=['-filter_complex',f"[0:{info['video']['index']}][0:{subs[0]['index']}]overlay=eof_action=pass,{vf}[v]",'-map','[v]']
                    else: cmd+=['-map',f"0:{info['video']['index']}",'-vf',vf]
                    if gpu:
                        gpu_preset={'rapido':'p4','equilibrado':'p5','calidad':'p6'}.get(options.speed,'p5')
                        cmd+=['-c:v',codec,'-preset',gpu_preset,'-tune','hq','-rc','vbr','-b:v','0' if options.unlimited else str(bitrate)]
                        # CQ overrides the average bitrate target in NVENC VBR.
                        # Use it only when file size is unrestricted; otherwise
                        # spend the available bitrate without quality-target overshoot.
                        if options.unlimited:cmd+=['-cq','18']
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
                        if not options.unlimited:
                            for index,rate in enumerate(audio_copy_rates):
                                if rate is not None:
                                    cmd+=['-c:a:'+str(index),'copy']
                                    log('Audio '+str(audio[index]['index'])+': AAC original sin recodificar; bitrate reservado según la pista.')
                        if options.unlimited and not options.downmix_audio:
                            for index,stream in enumerate(audio):
                                if stream.get('codec_name') in ('aac','ac3','eac3','alac','mp3'):
                                    cmd+=['-c:a:'+str(index),'copy']
                                    log('Audio '+str(stream['index'])+': copia sin recodificar.')
                                else:
                                    cmd+=['-c:a:'+str(index),'alac']
                                    log('Audio '+str(stream['index'])+': ALAC sin pérdida adicional para MP4.')
                        if options.codec=='h265':cmd+=['-tag:v','hvc1']
                        cmd+=['-f','mp4',str(result)]
                    cmd[1:1]=['-progress','pipe:1','-stats_period','0.3']
                    _run(cmd,control,duration,progress,progress_pass,log,space_guard,total_passes,backend)
                    if first_analysis:analysis_ready=True
            except EncodeCancelled:raise
            except EncodeError as exc:
                if gpu and options.mode=='auto':
                    log('Automático: falló NVIDIA; se reintentará con CPU. '+str(exc)[-500:])
                    backend='cpu';ffmpeg=primary_ffmpeg;continue
                raise
            actual=result.stat().st_size
            if actual<=0:raise EncodeError('FFmpeg no produjo un MP4 válido; el archivo está vacío.')
            if options.unlimited or actual<=cap:
                preserve_result=True
                break
            bitrate=_retry_video_bitrate(result,bitrate,cap,duration,audio_budget,ffprobe)
            log('Se ajustará el bitrate de video para cumplir el tamaño máximo, conservando las pistas seleccionadas.')
            if bitrate<10000: raise EncodeError('El límite solicitado no permite una salida válida.')
        else: raise EncodeError('No se alcanzó el tamaño máximo; no se publicó ningún archivo.')
        target=finalize_output(result,desired_target,control,log,source)
        preserve_result=False
        if progress: progress(dict(percent=100,speed=0,eta=0,pass_number=total_passes,total_passes=total_passes,backend=backend,status='Completado'))
        log(f'Guardado: {target.name} ({actual/1e6:.2f} MB).')
        return target
    finally:
        if preserve_result:
            _reveal_recovery_directory(temp,desired_target,log,result)
        else:
            # A scanner may briefly retain analysis files too. Cleanup must not
            # turn a successfully published MP4 into a reported encode failure.
            shutil.rmtree(temp,ignore_errors=True)
