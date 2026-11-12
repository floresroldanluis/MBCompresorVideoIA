"""Inspect and explicitly maintain verified, per-user desktop engines."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile


def managed_dir():
    return (Path(os.environ.get('USERPROFILE',str(Path.home())))/'AppData'/'Local' if os.name=='nt' else Path(os.environ.get('LOCALAPPDATA',str(Path.home()))))/'MBCompresorPro'/'tools'


def _run(path, args, timeout=20):
    return subprocess.run([str(path), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _read(url, limit=2_000_000):
    req = urllib.request.Request(url, headers={'User-Agent': 'MBCompresorPro/1.0'})
    with urllib.request.urlopen(req, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError('Respuesta de actualización demasiado grande.')
    return data


def _version(path, name):
    if not path:
        return None, 'No encontrado'
    try:
        r = _run(path, ['-version'] if name in ('ffmpeg', 'ffprobe') else ['--version'])
        if r.returncode:
            return None, (r.stderr or r.stdout)[-350:]
        first = r.stdout.splitlines()[0]
        if name in ('ffmpeg', 'ffprobe'):
            match = re.search(r'\bversion\s+(\S+)', first)
            return (match.group(1), '') if match else (None, 'Salida de versión no reconocida')
        if name == 'deno':
            match = re.match(r'deno\s+(\S+)', first)
            return (match.group(1), '') if match else (None, 'Salida de versión no reconocida')
        return (first.strip(), '') if re.match(r'^\d{4}\.\d{2}\.\d{2}', first) else (None, 'Salida de versión no reconocida')
    except (OSError, subprocess.SubprocessError, IndexError) as exc:
        return None, str(exc)


def discover(name, settings=None):
    settings = settings or {}
    configured = settings.get(name) if name in ('ffmpeg', 'ffprobe', 'deno') else None
    if configured:
        return str(Path(configured).resolve())
    base = Path(__file__).resolve().parent
    app = Path(sys.executable).parent if getattr(sys, 'frozen', False) else base
    for root in (managed_dir(), app / 'tools', base / 'tools'):
        path = root / (name + '.exe')
        if path.is_file():
            return str(path)
    return shutil.which(name)


def _compare(local, remote, name):
    if not local or not remote:
        return None
    pattern = r'^(\d+)\.(\d+)(?:\.(\d+))?(?:-essentials_build[^\s]*)?$'
    # Git snapshots have no reliable ordering against released semver builds.
    a, b = re.match(pattern, local.lstrip('v')), re.match(pattern, remote.lstrip('v'))
    return tuple(int(x or 0) for x in a.groups()) < tuple(int(x or 0) for x in b.groups()) if a and b else None


def _latest():
    values, errors = {}, {}
    for name, url in (
        ('ffmpeg', 'https://www.gyan.dev/ffmpeg/builds/release-version'),):
        try:
            raw = _read(url).decode()
            version = raw.strip()
            if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
                raise RuntimeError('Versión oficial no reconocida.')
            values[name] = version
        except Exception as exc:
            errors[name] = str(exc)
    values['ffprobe'] = values.get('ffmpeg')
    if 'ffmpeg' in errors:
        errors['ffprobe'] = errors['ffmpeg']
    return values, errors


def inspect_motors(settings, online=True):
    latest, errors = _latest() if online else ({}, {})
    entries = {}
    names=('ffmpeg','ffprobe') if settings.get('compression_only') else ('yt-dlp','ffmpeg','ffprobe','deno')
    for name in names:
        path = discover(name, settings)
        version, error = _version(path, name)
        entries[name] = dict(installed=bool(path and Path(path).is_file()), path=path, version=version, healthy=bool(version), latest=latest.get(name), update_available=_compare(version, latest.get(name), name), error=error, update_error=errors.get(name, ''))
    gpu = {}
    if entries['ffmpeg']['healthy']:
        for codec in ('h264_amf', 'h264_nvenc'):
            try:
                result = _run(entries['ffmpeg']['path'], ['-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=640x360:r=25', '-frames:v', '1', '-an', '-c:v', codec, '-f', 'null', '-'], timeout=12)
                gpu[codec] = {'available': result.returncode == 0, 'error': result.stderr[-450:] if result.returncode else ''}
            except (OSError, subprocess.SubprocessError) as exc:
                gpu[codec] = {'available': False, 'error': str(exc)}
    else:
        gpu = {c: {'available': False, 'error': 'FFmpeg no disponible'} for c in ('h264_amf', 'h264_nvenc')}
    roots = (managed_dir().resolve(), (Path(sys.executable).parent / 'tools').resolve(), (Path(__file__).parent / 'tools').resolve())
    autonomous = all(e['healthy'] and any(Path(e['path']).resolve().is_relative_to(root) for root in roots) for e in entries.values())
    from acceleration import find_nvenc,nvidia_info,compatible_candidates
    nvenc={c:find_nvenc(entries['ffmpeg']['path'],c) for c in ('h264','h265')} if entries['ffmpeg']['healthy'] else {}
    encoders=set()
    for candidate in compatible_candidates(entries['ffmpeg']['path']) if entries['ffmpeg']['path'] else []:
        try:
            listing=_run(candidate,['-hide_banner','-encoders'])
            encoders.update(re.findall(r'^\s*[A-Z.]{6}\s+(\S+)',listing.stdout,re.M))
        except (OSError,subprocess.SubprocessError):pass
    chosen=next((s['path'] for s in nvenc.values() if s['available']),None)
    nvidia_version=_version(chosen,'ffmpeg')[0] if chosen else None
    return {'entries': entries, 'ready': all(e['healthy'] for e in entries.values()), 'autonomous': autonomous, 'gpu': gpu,'installed_encoders':sorted(encoders),'nvenc':nvenc,'nvidia_version':nvidia_version,'nvidia_device':nvidia_info()}


def _retry_file_operation(operation, attempts=7):
    """Allow short antivirus/Windows sharing locks to clear without looping forever."""
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == attempts - 1:
                raise
            time.sleep(0.15 * (attempt + 1))


def _publish_staged(staged, destination):
    backups, published = {}, []
    backup_folder = Path(tempfile.mkdtemp(prefix='motor-respaldo-', dir=destination))
    recovery = []
    target = None
    try:
        # Back up the complete pair before changing either executable.
        for name, path in staged.items():
            target = destination / path.name
            if target.exists():
                backup = backup_folder / (name + '.backup')
                _retry_file_operation(lambda: shutil.copy2(target, backup))
                backups[name] = backup
        for name, path in staged.items():
            target = destination / path.name
            _retry_file_operation(lambda: path.replace(target))
            published.append(name)
    except OSError as exc:
        failed_target = target
        for name in reversed(published):
            target = destination / (name + '.exe')
            try:
                if name in backups:
                    _retry_file_operation(lambda: backups[name].replace(target))
                else:
                    _retry_file_operation(lambda: target.unlink(missing_ok=True))
            except OSError as rollback_error:
                if name in backups:
                    recovery.append(f'{target}; copia anterior en {backups[name]} ({rollback_error})')
                else:
                    recovery.append(str(target))
        message = f'No se pudo sustituir {failed_target}: {exc}.'
        if getattr(exc, 'winerror', None) in (5, 32, 33):
            message += ' El motor está en uso; espera a que terminen las tareas e inténtalo de nuevo.'
        if recovery:
            message += ' Revisa los archivos de recuperación: ' + ', '.join(recovery)
        else:
            message += ' Se conservaron los motores anteriores.'
        raise RuntimeError(message) from exc
    finally:
        if not recovery:
            # Backup files live outside the download staging folder so a failed
            # rollback cannot lose them when that staging folder is removed.
            try:
                for backup in backup_folder.iterdir():
                    _retry_file_operation(backup.unlink)
                backup_folder.rmdir()
            except OSError:
                pass


def _archive(url, checksum_url, names, destination, expected_sha256=None):
    """Extract only known executable basenames; validate before publishing as a group."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    checksum_matches = re.findall(r'\b[a-fA-F0-9]{64}\b', expected_sha256 if expected_sha256 else _read(checksum_url).decode())
    if len(checksum_matches) != 1:
        raise RuntimeError('Suma SHA256 oficial inválida.')
    checksum = checksum_matches[0].lower()
    with tempfile.TemporaryDirectory(prefix='motor-', dir=destination, ignore_cleanup_errors=True) as folder:
        folder = Path(folder)
        archive = folder / 'download.zip'
        req = urllib.request.Request(url, headers={'User-Agent': 'MBCompresorPro/1.0'})
        digest = hashlib.sha256()
        with urllib.request.urlopen(req, timeout=90) as response, archive.open('wb') as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 350_000_000:
                    raise RuntimeError('Archivo de motores demasiado grande.')
                digest.update(chunk); out.write(chunk)
        if digest.hexdigest() != checksum:
            raise RuntimeError('SHA256 no coincide. No se modificó ningún motor.')
        staged = {}
        with zipfile.ZipFile(archive) as z:
            for name in names:
                matches = [i for i in z.infolist() if Path(i.filename).name == name + '.exe']
                if len(matches) != 1 or matches[0].file_size > 300_000_000:
                    raise RuntimeError('Contenido del archivo de motores inválido.')
                path = folder / (name + '.exe')
                with z.open(matches[0]) as source, path.open('wb') as out:
                    shutil.copyfileobj(source, out)
                version, error = _version(path, name)
                if not version:
                    raise RuntimeError(f'{name} no inició: {error}')
                staged[name] = path
        _publish_staged(staged, destination)
    return {name: str(destination / (name + '.exe')) for name in names}


def update_deno(destination=None):
    release = json.loads(_read('https://api.github.com/repos/denoland/deno/releases/latest'))
    tag = release['tag_name']
    asset = 'deno-x86_64-pc-windows-msvc.zip'
    base = f'https://github.com/denoland/deno/releases/download/{tag}/'
    dest = Path(destination) if destination else managed_dir() / 'deno.exe'
    paths = _archive(base + asset, base + asset + '.sha256sum', ['deno'], dest.parent)
    return paths['deno']


def _install_ffmpeg():
    base = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
    return _archive(base, base + '.sha256', ['ffmpeg', 'ffprobe'], managed_dir())

def ensure_nvidia(settings=None,log=None,allow_download=True):
    from acceleration import find_nvenc,nvidia_info,compatible_candidates,COMPAT_URL,COMPAT_SHA256,COMPAT_VERSION
    log=log or (lambda _:None)
    primary=discover('ffmpeg',settings)
    if not primary:return ['Primero repara FFmpeg.']
    status=find_nvenc(primary)
    if status['available']:return ['Complemento NVIDIA NVENC verificado.']
    if not nvidia_info():return ['Sin GPU NVIDIA detectada. Automático usará CPU.']
    for candidate in compatible_candidates(primary):
        if Path(candidate).parent.name != 'nvenc':continue
        version, error=_version(candidate,'ffmpeg')
        if version and version.lstrip('v').split('-',1)[0] == COMPAT_VERSION:
            return ['Complemento NVIDIA instalado (FFmpeg '+COMPAT_VERSION+'). NVENC no se pudo activar; Automático usará CPU. '+status.get('error','')]
    if not allow_download:return ['NVIDIA NVENC no disponible. No se descargó el complemento porque no se pudo consultar la versión oficial; CPU sigue disponible.']
    log('Instalando complemento NVIDIA compatible con verificación SHA256…')
    paths=_archive(COMPAT_URL,None,['ffmpeg'],managed_dir()/'nvenc',expected_sha256=COMPAT_SHA256)
    status=find_nvenc(paths['ffmpeg'])
    return ['Complemento NVIDIA instalado y verificado.'] if status['available'] else ['Complemento NVIDIA instalado. El controlador no permite usar NVENC; Automático usará CPU. '+status['error']]

def _with_nvidia(paths,settings,log,messages,allow_download=True):
    try:messages+=ensure_nvidia({**settings,**paths},log,allow_download=allow_download)
    except Exception as exc:messages+=['NVIDIA no pudo repararse; CPU sigue disponible. '+str(exc)]
    return {**paths,'messages':messages}


def repair_ffmpeg(settings, log=None):
    log = log or (lambda message: None)
    paths = {n: discover(n, settings) for n in ('ffmpeg', 'ffprobe')}
    if all(_version(paths[n], n)[0] for n in paths):
        log('FFmpeg y FFprobe configurados funcionan correctamente.')
        return _with_nvidia(paths,settings,log,['Motores actuales verificados.'])
    base = Path(__file__).resolve().parent
    app = Path(sys.executable).parent if getattr(sys, 'frozen', False) else base
    for root in (managed_dir(), app / 'tools', base / 'tools'):
        bundled = {n: str(root / (n + '.exe')) for n in ('ffmpeg', 'ffprobe')}
        if all(_version(bundled[n], n)[0] for n in bundled):
            return _with_nvidia(bundled,{**settings,**bundled},log,['Se recuperaron los motores incluidos.'])
    log('Descargando FFmpeg y FFprobe oficiales con verificación SHA256…')
    paths=_install_ffmpeg()
    return _with_nvidia(paths,{**settings,**paths},log,['FFmpeg y FFprobe reparados en la carpeta de la aplicación.'])


def update_motors(settings, log=None):
    log=log or (lambda _:None)
    log('Comprobando FFmpeg, FFprobe y compatibilidad NVIDIA NVENC…')
    state=inspect_motors(dict(settings,compression_only=True),online=True)
    entries=state['entries']
    if all(e['healthy'] and e['update_available'] is not True for e in entries.values()):
        errors=list(dict.fromkeys(e.get('update_error','') for e in entries.values() if e.get('update_error')))
        if errors:
            messages=['FFmpeg y FFprobe funcionan. No se pudo comprobar si hay actualizaciones: '+'; '.join(errors)+'. Se conservaron los motores instalados.']
        elif all(e['update_available'] is False for e in entries.values()):
            messages=['FFmpeg y FFprobe comprobados con la versión oficial; no requieren actualización.']
        else:
            messages=['FFmpeg y FFprobe funcionan. La versión instalada no se puede comparar con la publicación oficial; actualización no comprobada. Se conservaron los motores instalados.']
        return _with_nvidia({n:e['path'] for n,e in entries.items()},settings,log,messages,allow_download=not errors)
    log('Descargando FFmpeg y FFprobe oficiales con verificación SHA256…')
    paths=_install_ffmpeg()
    return _with_nvidia(paths,{**settings,**paths},log,['FFmpeg y FFprobe actualizados y verificados con SHA256.'])
