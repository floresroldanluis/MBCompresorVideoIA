$ErrorActionPreference='Stop'
$candidates=@(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
)
$compiler=$candidates | Where-Object {Test-Path -LiteralPath $_} | Select-Object -First 1
if(-not $compiler){throw 'Instala Inno Setup 6 para generar el instalador.'}
& (Join-Path $PSScriptRoot 'Construir.ps1')
& $compiler (Join-Path $PSScriptRoot 'Instalador.iss')
if($LASTEXITCODE -ne 0){throw 'No se pudo generar el instalador.'}
