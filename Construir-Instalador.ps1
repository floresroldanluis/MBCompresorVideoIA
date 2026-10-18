param([string]$ReleaseDirectory='C:\Users\Luis\Documents\Codex\2026-10-10\qui\outputs\CompresorVideo\release')
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
New-Item -ItemType Directory -Path $ReleaseDirectory -Force | Out-Null
$definition=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Instalador.iss') -Raw
$versionMatch=[regex]::Match($definition,'#define AppVersion "(\d+\.\d+\.\d+)"')
if(-not $versionMatch.Success){throw 'No se pudo leer la versión del instalador.'}
$installerName='MBCompresorVideoIA-Setup-v'+$versionMatch.Groups[1].Value+'-windows-x64.exe'
foreach($installer in Get-Item -LiteralPath (Join-Path $PSScriptRoot ('Instalador\'+$installerName))){
    $destination=Join-Path $ReleaseDirectory $installer.Name
    if(Test-Path -LiteralPath $destination){
        if((Get-FileHash -LiteralPath $destination).Hash -ne (Get-FileHash -LiteralPath $installer.FullName).Hash){throw ('Ya existe otro instalador con el mismo nombre: '+$destination)}
    }else{Copy-Item -LiteralPath $installer.FullName -Destination $destination}
}
Write-Output ('Instaladores conservados en '+$ReleaseDirectory)
