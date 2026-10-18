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
        ('yt-dlp', 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest'),
        ('deno', 'https://api.github.com/repos/denoland/deno/releases/latest'),
        ('ffmpeg', 'https://www.gyan.dev/ffmpeg/builds/release-version')):
        try:
            raw = _read(url).decode()
            values[name] = json.loads(raw)['tag_name'].lstrip('v') if name != 'ffmpeg' else raw.strip()
        except Exception as exc:
            errors[name] = str(exc)
    values['ffprobe'] = values.get('ffmpeg')
    return values, errors


def inspect_motors(settings, online=True):
    latest, errors = _latest() if online else ({}, {})
    entries = {}
    names=('ffmpeg','ffprobe') if settings.get('compression_only') else ('yt-dlp','ffmpeg','ffprobe','deno')
    for name in names:
        path = discover(name, settings)
        version, error = _version(path, name)
        entries[name] = dict(path=path, version=version, healthy=bool(version), latest=latest.get(name), update_available=_compare(version, latest.get(name), name), error=error, update_error=errors.get(name, ''))
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
    return {'entries': entries, 'ready': all(e['healthy'] for e in entries.values()), 'autonomous': autonomous, 'gpu': gpu}


def _archive(url, checksum_url, names, destination):
    """Extract only known executable basenames; validate before publishing as a group."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    checksum_matches = re.findall(r'\b[a-fA-F0-9]{64}\b', _read(checksum_url).decode())
    if len(checksum_matches) != 1:
        raise RuntimeError('Suma SHA256 oficial inválida.')
    checksum = checksum_matches[0].lower()
    with tempfile.TemporaryDirectory(prefix='motor-', dir=destination) as folder:
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
        backups, published = {}, []
        try:
            for name, path in staged.items():
                target = destination / path.name
                if target.exists():
                    backup = folder / (name + '.backup')
                    shutil.copy2(target, backup); backups[name] = backup
                path.replace(target); published.append(name)
        except BaseException:
            for name in published:
                target = destination / (name + '.exe')
                if name in backups:
                    backups[name].replace(target)
                else:
                    target.unlink(missing_ok=True)
            raise
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


def repair_ffmpeg(settings, log=None):
    log = log or (lambda message: None)
    paths = {n: discover(n, settings) for n in ('ffmpeg', 'ffprobe')}
    if all(_version(paths[n], n)[0] for n in paths):
        log('FFmpeg y FFprobe configurados funcionan correctamente.')
        return {**paths, 'messages': ['Motores actuales verificados.']}
    base = Path(__file__).resolve().parent
    app = Path(sys.executable).parent if getattr(sys, 'frozen', False) else base
    for root in (managed_dir(), app / 'tools', base / 'tools'):
        bundled = {n: str(root / (n + '.exe')) for n in ('ffmpeg', 'ffprobe')}
        if all(_version(bundled[n], n)[0] for n in bundled):
            return {**bundled, 'messages': ['Se recuperaron los motores incluidos.']}
    log('Descargando FFmpeg y FFprobe oficiales con verificación SHA256…')
    return {**_install_ffmpeg(), 'messages': ['FFmpeg y FFprobe reparados en la carpeta de la aplicación.']}


def update_motors(settings, log=None):
    state=inspect_motors(dict(settings,compression_only=True),online=True)
    entries=state['entries']
    if all(e['healthy'] and e['update_available'] is not True for e in entries.values()):
        return {**{n:e['path'] for n,e in entries.items()},'messages':['Motores verificados. No hay actualizaciones confirmadas.']}
    return {**_install_ffmpeg(),'messages':['FFmpeg y FFprobe actualizados y verificados con SHA256.']}
