#define AppVersion "1.0.13"
[Setup]
AppId={{C52EF970-71D0-42E7-93CD-29D96781F120}
AppName=MB COMPRENSOR IA
AppVersion={#AppVersion}
AppPublisher=Luis Flores Roldán
AppPublisherURL=https://linktr.ee/floresroldanluis
AppSupportURL=https://github.com/floresroldanluis/MBCompresorVideoIA
DefaultDirName={localappdata}\MBCompresorPro
DefaultGroupName=MB COMPRENSOR IA
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Instalador
OutputBaseFilename=MBCompresorVideoIA-Setup-v{#AppVersion}-windows-x64
SetupIconFile=assets\app-logo-02.ico
UninstallDisplayIcon={app}\versions\{#AppVersion}\MB Compresor Pro.exe
WizardStyle=modern
WizardSizePercent=120
WizardImageFile=assets\installer-welcome.bmp
WizardSmallImageFile=assets\installer-logo.bmp
WizardImageStretch=no
WizardImageBackColor=$FFFFFF
DisableWelcomePage=no
DisableProgramGroupPage=yes
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=MB COMPRENSOR IA
VersionInfoDescription=Instalador de MB Compresor de Video IA

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
spanish.WelcomeLabel1=Menos tamaño. Más posibilidades.
spanish.WelcomeLabel2=Instala MB COMPRENSOR IA en tu equipo.%n%nConvierte tus videos a MP4 con control de tamaño, audio de alta calidad y recomendaciones con IA local.%n%nIncluye FFmpeg y FFprobe. Las recomendaciones de IA requieren Ollama y un modelo local, configurables desde la aplicación.%n%nTus videos originales se conservan.
spanish.FinishedHeadingLabel=Todo listo para comprimir
spanish.FinishedLabel=MB COMPRENSOR IA ya está instalado.%n%nAgrega tus videos, elige el tamaño máximo y comienza a comprimir. Por defecto, los archivos se guardan junto al video original.

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"

[Files]
Source: "Aplicacion\MB Compresor Pro\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MB COMPRENSOR IA"; Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; WorkingDir: "{app}\versions\{#AppVersion}"; IconFilename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; AppUserModelID: "FloresRoldanLuis.MBCompresorVideoIA"
Name: "{autodesktop}\MB COMPRENSOR IA"; Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; WorkingDir: "{app}\versions\{#AppVersion}"; IconFilename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; AppUserModelID: "FloresRoldanLuis.MBCompresorVideoIA"; Tasks: desktopicon

[Run]
Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; Description: "Abrir MB COMPRENSOR IA"; Flags: nowait postinstall skipifsilent
