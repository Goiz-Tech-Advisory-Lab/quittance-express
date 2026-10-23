; Installateur Windows (Inno Setup 6) — installation par utilisateur, sans droits administrateur.
; iscc /DAppVersion=0.1.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E7C3F0A-6B1D-4C9B-9E0F-5A2C1D7B8E41}
AppName=Quittance Express
AppVersion={#AppVersion}
AppVerName=Quittance Express {#AppVersion}
AppPublisher=Goiz Tech Advisory
AppPublisherURL=https://goiztechadvisory.fr
AppSupportURL=https://github.com/Goiz-Tech-Advisory-Lab/quittance-express/issues
DefaultDirName={localappdata}\Programs\Quittance Express
DefaultGroupName=Quittance Express
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=QuittanceExpress-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\QuittanceExpress.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
CloseApplications=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
Source: "..\dist\QuittanceExpress\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Quittance Express"; Filename: "{app}\QuittanceExpress.exe"
Name: "{userdesktop}\Quittance Express"; Filename: "{app}\QuittanceExpress.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\QuittanceExpress.exe"; Description: "Lancer Quittance Express"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; supprime la tâche planifiée d'envoi automatique (les données et quittances archivées sont conservées)
Filename: "{app}\QuittanceExpress.exe"; Parameters: "--uninstall-task"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveTask"
