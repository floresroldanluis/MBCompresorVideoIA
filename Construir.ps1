$ErrorActionPreference='Stop'
$projectRoot=$PSScriptRoot
$pythonPath=Join-Path $projectRoot '.venv\Scripts\python.exe'
$distRoot=Join-Path $projectRoot 'Aplicacion'
$buildRoot=Join-Path $projectRoot '.build'
$assetRoot=Join-Path $projectRoot 'assets'
& $pythonPath -m PyInstaller --noconfirm --windowed --onedir --name 'MB Compresor Pro' --icon (Join-Path $assetRoot 'app-logo-02.ico') --add-data ($assetRoot+';assets') --hidden-import settings_dialog --hidden-import local_ai --hidden-import popups --exclude-module telethon --exclude-module yt_dlp --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtPdf --distpath $distRoot --workpath $buildRoot --specpath $buildRoot (Join-Path $projectRoot 'app.py')
if($LASTEXITCODE -ne 0){throw 'La compilación falló.'}
$packageRoot=Join-Path $distRoot 'MB Compresor Pro'
# Qt 6.12 uses the Windows system ICU interface. An unrelated ICU from PATH
# can be collected under this filename and prevent QtCore from importing.
$foreignIcu=Join-Path $packageRoot '_internal\icuuc.dll'
if(Test-Path -LiteralPath $foreignIcu){Remove-Item -LiteralPath $foreignIcu}
New-Item -ItemType Directory -Force -Path (Join-Path $packageRoot 'tools') | Out-Null
foreach($toolName in @('ffmpeg.exe','ffprobe.exe')){Copy-Item -LiteralPath (Join-Path $projectRoot ('tools\'+$toolName)) -Destination (Join-Path $packageRoot 'tools') -Force}
Copy-Item -LiteralPath (Join-Path $projectRoot 'LEEME.md') -Destination $packageRoot -Force
Write-Output 'Aplicación compilada. Ejecuta Instalar.ps1 para actualizar la instalación local.'
