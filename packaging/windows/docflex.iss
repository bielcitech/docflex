; Installeur Windows de DocFlex (Inno Setup 6 : https://jrsoftware.org/isinfo.php)
; Prérequis : dist\DocFlex\ produit par « pyinstaller docflex.spec ».
; Compilation : iscc /DAppVersion=0.1.0 packaging\windows\docflex.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F3B2C1E-8D4A-4E7B-9C21-5A0D3F8E1B47}
AppName=DocFlex
AppVersion={#AppVersion}
AppPublisher=XenoviaLabs
DefaultDirName={autopf}\DocFlex
DefaultGroupName=DocFlex
DisableProgramGroupPage=yes
; Installation possible sans droits administrateur (dans le profil utilisateur).
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist
OutputBaseFilename=DocFlex-{#AppVersion}-windows-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\DocFlex.exe
#if FileExists("DocFlex.ico")
SetupIconFile=DocFlex.ico
#endif

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\DocFlex\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DocFlex"; Filename: "{app}\DocFlex.exe"
Name: "{autodesktop}\DocFlex"; Filename: "{app}\DocFlex.exe"; Tasks: desktopicon

[Registry]
; « Ouvrir avec DocFlex » pour les PDF (sans remplacer le lecteur par défaut).
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; ValueType: string; ValueName: "DocFlex.PDF"; ValueData: ""; Flags: uninsdeletevalue
Root: HKA; Subkey: "Software\Classes\DocFlex.PDF"; ValueType: string; ValueName: ""; ValueData: "Document PDF"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\DocFlex.PDF\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\DocFlex.exe"" ""%1"""

[Run]
Filename: "{app}\DocFlex.exe"; Description: "{cm:LaunchProgram,DocFlex}"; Flags: nowait postinstall skipifsilent
