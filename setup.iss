; ArtCore Client installer — Inno Setup 6.3+
; Build: run build.bat (PyInstaller first, then this script), or open in Inno Setup and press Compile.
; Expects the PyInstaller output in dist\ArtCoreClient\.

#define AppName      "ArtCore Client"
#define AppVersion   "1.0.1"
#define AppPublisher "ArtCore Client"
#define AppURL       "https://github.com/Ruf790/artcore-torrent-client"
#define AppExe       "ArtCoreClient.exe"
#define ProgIdT      "ArtCoreClient.torrent"
#define ProgIdM      "ArtCoreClient.magnet"

[Setup]
; Never change AppId: Windows uses it to find the installed copy for updates/uninstall
AppId={{EDD638BA-DFA8-4290-AE9C-56E7089265B2}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Per-user install by default (no admin); the user may choose "for all users"
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=installer
OutputBaseFilename=ArtCoreClient-Setup-{#AppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; The client holds this mutex — setup/uninstall waits for it to be closed
AppMutex=ArtCoreClientMutex
CloseApplications=yes
RestartApplications=no
ChangesAssociations=yes
ShowLanguageDialog=auto
; HKCU entries (language, autostart) are intended even for "all users" installs
UsedUserAreasWarning=no

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ru.GroupIntegration=Интеграция с Windows:
en.GroupIntegration=Windows integration:
ru.AssocTorrent=Открывать .torrent-файлы в ArtCore Client
en.AssocTorrent=Open .torrent files with ArtCore Client
ru.AssocMagnet=Открывать magnet-ссылки в ArtCore Client
en.AssocMagnet=Open magnet links with ArtCore Client
ru.Autostart=Запускать вместе с Windows (свёрнутым в трей)
en.Autostart=Start with Windows (minimized to tray)
ru.Firewall=Разрешить входящие соединения в брандмауэре Windows
en.Firewall=Allow incoming connections in Windows Firewall
ru.TorrentFile=Торрент-файл
en.TorrentFile=Torrent file

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoctorrent"; Description: "{cm:AssocTorrent}";      GroupDescription: "{cm:GroupIntegration}"
Name: "assocmagnet";  Description: "{cm:AssocMagnet}";       GroupDescription: "{cm:GroupIntegration}"
Name: "autostart";    Description: "{cm:Autostart}";         GroupDescription: "{cm:GroupIntegration}"; Flags: unchecked
; netsh needs admin rights, so this is offered only for "install for all users"
Name: "firewall";     Description: "{cm:Firewall}";          GroupDescription: "{cm:GroupIntegration}"; Check: IsAdminInstallMode

[Files]
Source: "dist\ArtCoreClient\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"; AppUserModelID: "ArtCore.Client"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; HKA = HKLM for "all users", HKCU for "just me"

; Remember the installer language — the client starts in it on first run
Root: HKCU; Subkey: "Software\ArtCore\ArtCore Client"; ValueType: string; ValueName: "Language"; ValueData: "{language}"; Flags: uninsdeletekey

; ── .torrent ────────────────────────────────────────────────
Root: HKA; Subkey: "Software\Classes\.torrent"; ValueType: string; ValueName: ""; ValueData: "{#ProgIdT}"; Flags: uninsdeletevalue; Tasks: assoctorrent
Root: HKA; Subkey: "Software\Classes\.torrent"; ValueType: string; ValueName: "Content Type"; ValueData: "application/x-bittorrent"; Tasks: assoctorrent
Root: HKA; Subkey: "Software\Classes\.torrent\OpenWithProgids"; ValueType: string; ValueName: "{#ProgIdT}"; ValueData: ""; Flags: uninsdeletevalue; Tasks: assoctorrent
Root: HKA; Subkey: "Software\Classes\{#ProgIdT}"; ValueType: string; ValueName: ""; ValueData: "{cm:TorrentFile}"; Flags: uninsdeletekey; Tasks: assoctorrent
Root: HKA; Subkey: "Software\Classes\{#ProgIdT}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"; Tasks: assoctorrent
Root: HKA; Subkey: "Software\Classes\{#ProgIdT}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assoctorrent

; ── magnet: ─────────────────────────────────────────────────
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: ""; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\magnet"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\magnet\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"",0"; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\magnet\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\{#ProgIdM}"; ValueType: string; ValueName: ""; ValueData: "URL:Magnet link"; Flags: uninsdeletekey; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\{#ProgIdM}"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Tasks: assocmagnet
Root: HKA; Subkey: "Software\Classes\{#ProgIdM}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: assocmagnet

; ── "Default apps" in Windows Settings ──────────────────────
Root: HKA; Subkey: "Software\ArtCore\ArtCore Client\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#AppName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\ArtCore\ArtCore Client\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "BitTorrent client"
Root: HKA; Subkey: "Software\ArtCore\ArtCore Client\Capabilities\FileAssociations"; ValueType: string; ValueName: ".torrent"; ValueData: "{#ProgIdT}"
Root: HKA; Subkey: "Software\ArtCore\ArtCore Client\Capabilities\URLAssociations"; ValueType: string; ValueName: "magnet"; ValueData: "{#ProgIdM}"
Root: HKA; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "ArtCore Client"; ValueData: "Software\ArtCore\ArtCore Client\Capabilities"; Flags: uninsdeletevalue

; ── autostart ───────────────────────────────────────────────
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ArtCore Client"; ValueData: """{app}\{#AppExe}"" --minimized"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""{#AppName}"" dir=in action=allow program=""{app}\{#AppExe}"" enable=yes"; Flags: runhidden; Tasks: firewall
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; silent update from the client itself: start it again afterwards
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""{#AppName}"""; Flags: runhidden; RunOnceId: "DelFirewallRule"; Tasks: firewall

[UninstallDelete]
; Torrent list and settings in %APPDATA%\ArtCoreClient are kept on purpose (reinstall keeps the session).
Type: filesandordirs; Name: "{app}"
