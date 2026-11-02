param([string]$ReleaseVersion='1.0.40')
$ErrorActionPreference='Stop'
if($ReleaseVersion -notmatch '^\d+\.\d+\.\d+$'){throw 'Usa una versión como 1.0.6'}
$package=Join-Path $PSScriptRoot 'Aplicacion\MB Compresor Pro'
if(-not(Test-Path -LiteralPath (Join-Path $package 'MB Compresor Pro.exe'))){throw 'Compila primero con Construir.ps1'}
$releaseRoot=Join-Path $PSScriptRoot ('Releases\v'+$ReleaseVersion)
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$assetName='MBCompresorVideoIA-v'+$ReleaseVersion+'-windows-x64.zip'
$zipPath=Join-Path $releaseRoot $assetName
if(Test-Path -LiteralPath $zipPath){throw 'El ZIP ya existe. Publica versiones inmutables o usa otra carpeta.'}
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($package,$zipPath,[IO.Compression.CompressionLevel]::Optimal,$true)
$sha=[Security.Cryptography.SHA256]::Create();$stream=[IO.File]::OpenRead($zipPath)
try{$digest=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}finally{$stream.Dispose();$sha.Dispose()}
[IO.File]::WriteAllText((Join-Path $releaseRoot 'SHA256SUMS'),$digest+'  '+$assetName+[Environment]::NewLine,[Text.Encoding]::ASCII)
Write-Output ('Listo: publica un Release v'+$ReleaseVersion+' y adjunta el ZIP y SHA256SUMS de '+$releaseRoot)
