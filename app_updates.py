"""GitHub Releases portable ZIP updater; preserves preferences and source videos."""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

DEFAULT_REPOSITORY='https://github.com/floresroldanluis/MBCompresorVideoIA'
APP_EXE='MB Compresor Pro.exe'

def repository_id(url):
    parsed=urllib.parse.urlsplit(url.strip())
    parts=parsed.path.strip('/').split('/')
    if parsed.scheme!='https' or parsed.hostname!='github.com' or parsed.username or parsed.password or parsed.query or parsed.fragment or len(parts) not in (2,3) or (len(parts)==3 and parts[2]!='releases'):
        raise ValueError('Introduce un repositorio HTTPS de github.com.')
    if not all(re.fullmatch(r'[A-Za-z0-9_.-]+',p) and p not in ('.','..') for p in parts[:2]):raise ValueError('Repositorio inválido.')
    return '/'.join(parts[:2])

def version_tuple(version):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(version))
    if not match:raise ValueError('La versión publicada debe usar el formato v1.2.3.')
    return tuple(map(int,match.groups()))

def _request(url):
    return urllib.request.Request(url,headers={'User-Agent':'MBCompresorVideoIA-Updater','Accept':'application/vnd.github+json'})

def _read(url,limit=2_000_000):
    with urllib.request.urlopen(_request(url),timeout=20) as response:content=response.read(limit+1)
    if len(content)>limit:raise RuntimeError('Respuesta demasiado grande.')
    return content

def _asset_url(url,repo):
    u=urllib.parse.urlsplit(url)
    if u.scheme!='https' or u.hostname!='github.com' or u.username or u.password or not u.path.startswith('/'+repo+'/releases/download/'):
        raise ValueError('El archivo no pertenece a los Releases del repositorio configurado.')
    return url

def check_update(repository,current_version):
    repo=repository_id(repository)
    result=dict(repository=repo,current=current_version,latest=None,available=False,installable=False,message='',asset=None)
    try:release=json.loads(_read('https://api.github.com/repos/'+repo+'/releases/latest'))
    except urllib.error.HTTPError as exc:
        if exc.code==404:return dict(result,message='Todavía no hay Releases públicos disponibles en este repositorio.')
        if exc.code in (403,429):raise RuntimeError('GitHub limitó temporalmente las consultas. Intenta más tarde.') from exc
        raise
    if release.get('draft') or release.get('prerelease'):return dict(result,message='No hay una versión estable publicada.')
    latest=release.get('tag_name','');result['latest']=latest
    result['available']=version_tuple(latest)>version_tuple(current_version)
    if not result['available']:return dict(result,message=f'Ya tienes la versión más reciente ({current_version}).')
    assets=release.get('assets',[])
    candidates=[a for a in assets if str(a.get('name','')).lower().endswith('.zip') and re.search(r'mbcompr?es|mbcompres',str(a.get('name','')),re.I)]
    if len(candidates)!=1:return dict(result,message=f'Versión {latest} disponible. Falta un único paquete ZIP compatible de MBCompresorVideoIA para Windows.')
    asset=dict(candidates[0]);_asset_url(asset.get('browser_download_url',''),repo)
    digest=asset.get('digest','') or ''
    expected=digest[7:] if digest.startswith('sha256:') else None
    if not expected:
        checks=[a for a in assets if a.get('name') in ('SHA256SUMS','SHA256SUMS.txt',asset['name']+'.sha256')]
        if checks:
            text=_read(_asset_url(checks[0]['browser_download_url'],repo),100_000).decode('utf-8-sig')
            for line in text.splitlines():
                parts=line.split()
                if len(parts)==2 and parts[1].lstrip('*')==asset['name']:expected=parts[0];break
                if len(parts)==1 and checks[0]['name']==asset['name']+'.sha256':expected=parts[0]
    if not expected or not re.fullmatch(r'[0-9a-fA-F]{64}',expected):return dict(result,message=f'Versión {latest} disponible; falta una suma SHA256 verificable del ZIP.')
    asset['sha256']=expected.lower();result.update(asset=asset,installable=True,message=f'Actualización disponible: {latest}. Lista para descargar e instalar.')
    return result

def extract_package(archive,destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)>10000 or sum(i.file_size for i in entries)>2_000_000_000:raise ValueError('El paquete supera el tamaño permitido.')
        names=set()
        for info in entries:
            path=PurePosixPath(info.filename.replace('\\','/'))
            if path.is_absolute() or '..' in path.parts or any(':' in p or p.endswith((' ','.')) for p in path.parts) or stat.S_ISLNK(info.external_attr>>16):raise ValueError('El paquete contiene rutas no válidas.')
            normalized=str(path).casefold().rstrip('/')
            if normalized in names:raise ValueError('El paquete contiene rutas duplicadas.')
            names.add(normalized)
            target=destination.joinpath(*path.parts)
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('Ruta fuera del paquete.')
            if info.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(info) as source,target.open('wb') as out:shutil.copyfileobj(source,out)
    executables=list(destination.rglob(APP_EXE))
    if len(executables)!=1:raise ValueError('El paquete no contiene una única aplicación compatible.')
    root=executables[0].parent
    for item in ('_internal/python314.dll','tools/ffmpeg.exe','tools/ffprobe.exe'):
        if not (root/item).is_file():raise ValueError('El ZIP está incompleto: falta '+item)
    if executables[0].read_bytes()[:2]!=b'MZ':raise ValueError('El ejecutable no es una aplicación de Windows válida.')
    return root

def prepare_update(release,updates_dir):
    if not release.get('installable'):raise ValueError('Primero busca una actualización instalable.')
    updates_root=_make_dir(updates_dir).resolve();stage=Path(tempfile.mkdtemp(prefix='release-',dir=updates_root)).resolve()
    try:
        asset=release['asset'];url=_asset_url(asset['browser_download_url'],release['repository']);archive=stage/'package.zip';digest=hashlib.sha256();total=0
        with urllib.request.urlopen(_request(url),timeout=60) as response,archive.open('wb') as out:
            while chunk:=response.read(1024*1024):
                total+=len(chunk)
                if total>1_000_000_000:raise ValueError('La actualización supera el tamaño permitido.')
                digest.update(chunk);out.write(chunk)
        if digest.hexdigest()!=asset['sha256']:raise ValueError('La verificación SHA256 falló; la aplicación instalada se conserva.')
        package=extract_package(archive,stage/'extracted')
        return dict(stage=str(stage),package=str(package),version=release['latest'])
    except BaseException:
        if stage!=updates_root and stage.is_relative_to(updates_root):shutil.rmtree(stage)
        raise

def _make_dir(path):
    path=Path(path);path.mkdir(parents=True,exist_ok=True);return path

def make_install_plan(prepared,install_root,pid,restart=True):
    stage=Path(prepared['stage']).resolve();package=Path(prepared['package']).resolve();install=Path(install_root).resolve()
    if not package.is_relative_to(stage) or package==install:raise ValueError('Carpeta de actualización inválida.')
    if not (install/APP_EXE).is_file():raise ValueError('No se encuentra la instalación actual.')
    manifest=dict(install=str(install),package=str(package),backup=str(stage/'backup'),pid=int(pid),restart=restart,version=prepared['version'],exe=APP_EXE,hash=hashlib.sha256((package/APP_EXE).read_bytes()).hexdigest(),result=str(stage.parent/'last-result.json'))
    plan=stage/'install-plan.json';plan.write_text(json.dumps(manifest),encoding='utf-8')
    script=stage/'install-update.ps1';script.write_text(UPDATE_SCRIPT,encoding='utf-8-sig')
    return script,plan

def launch_installer(script,plan):
    return subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Manifest',str(plan)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

UPDATE_SCRIPT=r'''
param([Parameter(Mandatory=$true)][string]$Manifest)
$ErrorActionPreference='Stop'
$m=Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
$install=[IO.Path]::GetFullPath($m.install).TrimEnd('\')
$package=[IO.Path]::GetFullPath($m.package).TrimEnd('\')
$backup=[IO.Path]::GetFullPath($m.backup).TrimEnd('\')
$stage=[IO.Path]::GetFullPath((Split-Path -Parent $Manifest)).TrimEnd('\')
$replaced=@();$saved=@();$success=$false;$errorText=''
try {
 if(-not $package.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $backup.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Rutas de actualización inválidas'}
 if($m.exe -ne 'MB Compresor Pro.exe'){throw 'Nombre de aplicación inválido'}
 if($m.pid -gt 0){$parentApp=Get-Process -Id $m.pid -ErrorAction SilentlyContinue;if($parentApp -and -not $parentApp.WaitForExit(60000)){throw 'La aplicación no se cerró a tiempo'}}
 $otherApps=Get-Process -ErrorAction SilentlyContinue | Where-Object {$_.Path -and (Split-Path $_.Path -Parent) -eq $install}
 if($otherApps){throw 'Cierra las otras ventanas de la aplicación y vuelve a intentar la actualización'}
 $sha=[Security.Cryptography.SHA256]::Create();$stream=[IO.File]::OpenRead((Join-Path $package $m.exe))
 try{$actualHash=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}finally{$stream.Dispose();$sha.Dispose()}
 if($actualHash -ne $m.hash){throw 'El ejecutable preparado fue modificado'}
 New-Item -ItemType Directory -Force -Path $backup | Out-Null
 foreach($name in @('_internal','tools',$m.exe)){
  $target=[IO.Path]::GetFullPath((Join-Path $install $name));$source=[IO.Path]::GetFullPath((Join-Path $package $name));$prior=[IO.Path]::GetFullPath((Join-Path $backup $name))
  if(-not $target.StartsWith($install+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $source.StartsWith($package+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $prior.StartsWith($backup+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Destino fuera de la instalación'}
  if(-not(Test-Path -LiteralPath $source)){throw 'El paquete está incompleto'}
  if(Test-Path -LiteralPath $target){Move-Item -LiteralPath $target -Destination $prior;$saved+=,$name}
  $replaced+=,$name;Copy-Item -LiteralPath $source -Destination $target -Recurse
 }
 $success=$true
}catch{
 $errorText=$_.Exception.Message
 foreach($name in $replaced){$target=[IO.Path]::GetFullPath((Join-Path $install $name));if($target.StartsWith($install+'\',[StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $target)){Remove-Item -LiteralPath $target -Recurse -Force}}
 foreach($name in $saved){$prior=[IO.Path]::GetFullPath((Join-Path $backup $name));$target=[IO.Path]::GetFullPath((Join-Path $install $name));try{if(-not $target.StartsWith($install+'\',[StringComparison]::OrdinalIgnoreCase) -or -not $prior.StartsWith($backup+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Ruta de restauración inválida'};if(Test-Path -LiteralPath $target){Remove-Item -LiteralPath $target -Recurse -Force};Move-Item -LiteralPath $prior -Destination $target}catch{$errorText+='; no se pudo restaurar '+$name+': '+$_.Exception.Message}}
}
@{ok=$success;version=$m.version;message=$errorText;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath $m.result -Encoding UTF8
if($m.restart -and (Test-Path -LiteralPath (Join-Path $install $m.exe))){Start-Process -FilePath (Join-Path $install $m.exe) -WorkingDirectory $install}
if(-not $success){exit 1}
'''
