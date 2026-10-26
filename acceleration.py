"""Local NVENC capability checks and an isolated compatibility engine."""
import os
from pathlib import Path
import subprocess
import sys
import time

COMPAT_VERSION='8.0.1'
COMPAT_URL='https://github.com/GyanD/codexffmpeg/releases/download/8.0.1/ffmpeg-8.0.1-essentials_build.zip'
COMPAT_SHA256='e2aaeaa0fdbc397d4794828086424d4aaa2102cef1fb6874f6ffd29c0b88b673'
MODES=[('Automático','auto'),('NVIDIA rápido con alta calidad','nvidia'),('CPU para priorizar calidad por tamaño','cpu')]
_cache={}

def compatible_candidates(primary):
    base=Path(__file__).resolve().parent
    app=Path(sys.executable).parent if getattr(sys,'frozen',False) else base
    managed=Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local'/'MBCompresorPro'/'tools'
    candidates=[Path(primary),managed/'nvenc'/'ffmpeg.exe',app/'tools'/'nvenc'/'ffmpeg.exe',base/'tools'/'nvenc'/'ffmpeg.exe',Path(primary).parent/'nvenc'/'ffmpeg.exe']
    return list(dict.fromkeys(str(p.resolve()) for p in candidates if p.is_file()))

def nvenc_status(path,codec='h264'):
    try:
        stat=Path(path).stat();key=(str(Path(path).resolve()),stat.st_mtime_ns,stat.st_size,codec)
        cached=_cache.get(key)
        if cached and time.monotonic()-cached[0]<30:return dict(cached[1])
        encoder='h264_nvenc' if codec=='h264' else 'hevc_nvenc'
        cmd=[str(path),'-hide_banner','-loglevel','error','-f','lavfi','-i','color=s=320x240:r=25','-frames:v','30','-an','-c:v',encoder,'-preset','p5','-tune','hq','-rc','vbr','-multipass','qres','-spatial-aq','1','-rc-lookahead','20','-bf','3','-b:v','500k','-f','null','-']
        result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        report=dict(available=result.returncode==0,path=str(path),error='\n'.join(result.stderr.splitlines()[:2]) if result.returncode else '')
        _cache[key]=(time.monotonic(),report);return dict(report)
    except (OSError,subprocess.SubprocessError) as exc:return dict(available=False,path=str(path),error=str(exc))

def find_nvenc(primary,codec='h264'):
    errors=[]
    for path in compatible_candidates(primary):
        status=nvenc_status(path,codec)
        if status['available']:return status
        errors.append(status['error'])
    return dict(available=False,path=None,error='; '.join(errors) or 'No se encontró un motor NVIDIA compatible.')

def nvidia_info():
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],capture_output=True,text=True,timeout=8,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        return result.stdout.strip() if result.returncode==0 else ''
    except (OSError,subprocess.SubprocessError):return ''
