#ifndef AppVersion
  #error AppVersion must be supplied by build_release.py
#endif
#ifndef AppIdValue
  #define AppIdValue "C36C795A-6471-43A2-9813-A2412DA7DEED"
#endif
#ifndef AppNameValue
  #define AppNameValue "Goggles HDMI"
#endif
#ifndef SetupName
  #define SetupName "Goggles-HDMI-Setup-" + AppVersion
#endif
#ifndef AppRunArguments
  #define AppRunArguments ""
#endif

[Setup]
AppId={#AppIdValue}
AppName={#AppNameValue}
AppVersion={#AppVersion}
AppPublisher=Contentrium
DefaultDirName={localappdata}\Programs\{#AppNameValue}
DefaultGroupName={#AppNameValue}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename={#SetupName}
SetupIconFile=..\assets\fpv-line.ico
UninstallDisplayIcon={app}\Goggles HDMI.exe
UninstallDisplayName={#AppNameValue}
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppNameValue}
VersionInfoCompany=Contentrium
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
SetupMutex=GogglesHDMI.Contentrium.Setup

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppNameValue}"; Filename: "{app}\Goggles HDMI.exe"; WorkingDir: "{app}"; AppUserModelID: "GogglesHDMI.Next.FPV"
Name: "{userdesktop}\{#AppNameValue}"; Filename: "{app}\Goggles HDMI.exe"; WorkingDir: "{app}"; Tasks: desktopicon; AppUserModelID: "GogglesHDMI.Next.FPV"

[Run]
Filename: "{app}\Goggles HDMI.exe"; WorkingDir: "{app}"; Parameters: "{#AppRunArguments}"; Description: "{cm:LaunchProgram,{#AppNameValue}}"; Flags: nowait postinstall skipifsilent; Check: not IsAppUpdate
Filename: "{app}\Goggles HDMI.exe"; WorkingDir: "{app}"; Parameters: "{#AppRunArguments}"; Flags: nowait; Check: IsAppUpdate

[Code]
function IsAppUpdate: Boolean;
begin
  Result := ExpandConstant('{param:UPDATEFROMAPP|0}') = '1';
end;
