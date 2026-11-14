#define AppVersion "1.0.46"
[Setup]
AppId={{C52EF970-71D0-42E7-93CD-29D96781F120}
AppName=MB Compresor de Video IA
AppVersion={#AppVersion}
AppPublisher=Azvl
AppPublisherURL=https://linktr.ee/floresroldanluis
AppSupportURL=https://github.com/floresroldanluis/MBCompresorVideoIA
DefaultDirName={autopf}\MBCompresorVideo
UsePreviousAppDir=no
UsePreviousPrivileges=no
DefaultGroupName=MB Compresor de Video IA
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Instalador
OutputBaseFilename=MBCompresorVideoIA-Setup-v{#AppVersion}-windows-x64
SetupIconFile=assets\app-logo-02.ico
UninstallDisplayIcon={app}\versions\{#AppVersion}\MB Compresor Pro.exe
WizardStyle=modern
WizardSizePercent=100
WizardImageFile=assets\installer-welcome.bmp
WizardSmallImageFile=assets\installer-logo.bmp
WizardImageStretch=no
WizardImageBackColor=$FFFFFF
DisableWelcomePage=no
DisableDirPage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
DirExistsWarning=no
EnableDirDoesntExistWarning=no
Compression=lzma2/fast
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=MB Compresor de Video IA
VersionInfoDescription=Instalador de MB Compresor de Video IA
VersionInfoCompany=Azvl

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
spanish.SetupWindowTitle=Instalación de MB Compresor de Video IA
spanish.SelectDirBrowseLabel=Si deseas cambiar la carpeta, pulsa Examinar. Pulsa Instalar para comenzar.
spanish.WelcomeLabel1=Menos tamaño. Más posibilidades.
spanish.WelcomeLabel2=Comprime tus videos a MP4 con alta calidad y control de tamaño.%n%nIncluye los motores de conversión. IA local opcional con Ollama.
spanish.FinishedHeadingLabel=Todo listo para comprimir
spanish.FinishedLabel=MB Compresor de Video IA ya está instalado.%n%nAgrega tus videos, elige el tamaño máximo y comienza a comprimir. Por defecto, los archivos se guardan junto al video original.

[Files]
Source: "Aplicacion\MB Compresor Pro\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\MB Compresor de Video IA"; Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; WorkingDir: "{app}\versions\{#AppVersion}"; IconFilename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; AppUserModelID: "FloresRoldanLuis.MBCompresorVideoIA"
Name: "{autodesktop}\MB Compresor de Video IA"; Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; WorkingDir: "{app}\versions\{#AppVersion}"; IconFilename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; AppUserModelID: "FloresRoldanLuis.MBCompresorVideoIA"

[InstallDelete]
Type: files; Name: "{autodesktop}\MB Comprensor de Video IA.lnk"
Type: files; Name: "{autoprograms}\MB Comprensor de Video IA.lnk"
Type: files; Name: "{autodesktop}\MB COMPRENSOR IA.lnk"
Type: files; Name: "{autoprograms}\MB COMPRENSOR IA.lnk"

[Run]
Filename: "{app}\versions\{#AppVersion}\MB Compresor Pro.exe"; Description: "Abrir MB Compresor de Video IA"; Flags: nowait postinstall skipifsilent

[Code]
function CopyInstallerFile(ExistingFile, NewFile: String; FailIfExists: Boolean): Boolean;
  external 'CopyFileW@kernel32.dll stdcall';
function AnimateInstallerWindow(Window: HWND; Duration, Flags: LongWord): Boolean;
  external 'AnimateWindow@user32.dll stdcall';
function InstallerWindowVisible(Window: HWND): Boolean;
  external 'IsWindowVisible@user32.dll stdcall';
function ShowInstallerWindow(Window: HWND; Command: Integer): Boolean;
  external 'ShowWindow@user32.dll stdcall';

var
  ScopePage: TInputOptionWizardPage;
  ScopePathLabel: TNewStaticText;
  Relaunching: Boolean;
  PreviousWizardShow: TNotifyEvent;
  PreviousWizardClose: TCloseEvent;

procedure InstallerShown(Sender: TObject);
var
  Animated: Boolean;
begin
  if (PreviousWizardShow <> nil) then PreviousWizardShow(Sender);
  if WizardSilent then Exit;
  if InstallerWindowVisible(WizardForm.Handle) then ShowInstallerWindow(WizardForm.Handle, 0);
  Animated := AnimateInstallerWindow(WizardForm.Handle, 260, $00080000);
  Log('Installer fade-in 260ms: ' + IntToStr(Ord(Animated)));
  if not Animated then ShowInstallerWindow(WizardForm.Handle, SW_SHOWNORMAL);
end;

procedure FadeInstallerOut;
var
  Animated: Boolean;
begin
  if WizardSilent then Exit;
  if InstallerWindowVisible(WizardForm.Handle) then
  begin
    Animated := AnimateInstallerWindow(WizardForm.Handle, 220, $00090000);
    Log('Installer fade-out 220ms: ' + IntToStr(Ord(Animated)));
  end;
end;

procedure InstallerClosing(Sender: TObject; var Action: TCloseAction);
begin
  if (PreviousWizardClose <> nil) then PreviousWizardClose(Sender, Action);
  if Action <> caNone then FadeInstallerOut;
end;

function ScopeDirectory: String;
begin
  if ScopePage.SelectedValueIndex = 0 then
    Result := ExpandConstant('{commonpf}\MBCompresorVideo')
  else Result := ExpandConstant('{localappdata}\Programs\MBCompresorVideo');
end;

procedure UpdateScopePath(Sender: TObject);
var
  Directory: String;
begin
  Directory := ScopeDirectory;
  ScopePathLabel.Caption := 'Carpeta de instalación: ' + Directory;
  if DirExists(Directory) then
    ScopePathLabel.Caption := ScopePathLabel.Caption + #13#10 + 'La carpeta ya existe. Se reinstalará o actualizará en esta ubicación.';
end;

procedure InitializeWizard;
begin
  PreviousWizardShow := WizardForm.OnShow;
  PreviousWizardClose := WizardForm.OnClose;
  WizardForm.OnShow := @InstallerShown;
  WizardForm.OnClose := @InstallerClosing;
  ScopePage := CreateInputOptionPage(wpWelcome, '¿Quién puede usar esta aplicación?',
    'Selecciona el tipo de instalación.',
    'Elige si deseas instalar para todos los usuarios o solo para tu cuenta.', True, False);
  ScopePage.Add('Cualquiera que utilice este ordenador (todos los usuarios).');
  ScopePage.Add('Solo para mí.');
  if IsAdminInstallMode then ScopePage.SelectedValueIndex := 0
    else ScopePage.SelectedValueIndex := 1;
  ScopePathLabel := TNewStaticText.Create(WizardForm);
  ScopePathLabel.Parent := ScopePage.Surface;
  ScopePathLabel.Left := 0;
  ScopePathLabel.Top := ScopePage.SurfaceHeight - ScaleY(74);
  ScopePathLabel.Width := ScopePage.SurfaceWidth;
  ScopePathLabel.Height := ScaleY(70);
  ScopePathLabel.AutoSize := False;
  ScopePathLabel.WordWrap := True;
  ScopePage.CheckListBox.OnClickCheck := @UpdateScopePath;
  UpdateScopePath(nil);
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = ScopePage.ID then UpdateScopePath(nil);
  if CurPageID = wpSelectDir then WizardForm.NextButton.Caption := 'Instalar';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (ExpandConstant('{param:SCOPESELECTED|0}') = '1') and
    ((PageID = wpWelcome) or (PageID = ScopePage.ID));
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  ErrorCode: Integer;
  Params: String;
  HandoffExe: String;
  Verb: String;
begin
  Result := True;
  if CurPageID = wpFinished then begin FadeInstallerOut; Exit; end;
  if CurPageID <> ScopePage.ID then Exit;
  if ((ScopePage.SelectedValueIndex = 0) and IsAdminInstallMode) or
     ((ScopePage.SelectedValueIndex = 1) and not IsAdminInstallMode) then Exit;
  Params := '/SCOPESELECTED=1 /LANG=spanish';
  if ScopePage.SelectedValueIndex = 0 then Params := Params + ' /ALLUSERS'
    else Params := Params + ' /CURRENTUSER';
  Result := False;
  { Inno forbids relaunching its own source before installation starts.
    Use a separate temporary copy and retain it until the child exits. }
  HandoffExe := ExpandConstant('{tmp}\MBCompresorVideoIA-ScopeSetup.exe');
  if not CopyInstallerFile(ExpandConstant('{srcexe}'), HandoffExe, False) then
  begin
    MsgBox('No se pudo preparar el instalador temporal. Comprueba el espacio libre y vuelve a intentarlo.', mbError, MB_OK);
    Exit;
  end;
  if ScopePage.SelectedValueIndex = 0 then Verb := 'runas' else Verb := '';
  FadeInstallerOut;
  WizardForm.Hide;
  if ShellExec(Verb, HandoffExe, Params, '', SW_SHOWNORMAL,
    ewWaitUntilTerminated, ErrorCode) then
  begin
    Relaunching := True;
    WizardForm.Close;
  end
  else
  begin
    WizardForm.Show;
    if ErrorCode = 1223 then
      MsgBox('Se canceló el permiso de administrador. Pulsa Siguiente para volver a solicitarlo o elige Solo para mí.', mbInformation, MB_OK)
    else MsgBox('No se pudo abrir el instalador para el tipo seleccionado. Error ' + IntToStr(ErrorCode) + ': ' + SysErrorMessage(ErrorCode), mbError, MB_OK);
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if Relaunching then begin Cancel := True; Confirm := False; Exit; end;
  if Confirm and not WizardSilent then
  begin
    Cancel := SuppressibleMsgBox(SetupMessage(msgExitSetupMessage), mbConfirmation, MB_YESNO, IDNO) = IDYES;
    Confirm := False;
  end;
  if Cancel then FadeInstallerOut;
end;
