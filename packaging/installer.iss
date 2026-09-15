; Inno Setup script for the INVL Hub Windows installer.
;
; Built by release.bat after PyInstaller produces dist\INVLHub. The installer
; shows EULA.txt as a click-through license (the user must accept it before
; anything is installed), installs per-user (no admin prompt, which matters
; for an unsigned installer), adds a Start Menu entry and an uninstaller, and
; offers to launch the app at the end.
;
; GitHub's windows runners ship Inno Setup 6; locally, install it from
; jrsoftware.org and run: ISCC.exe packaging\installer.iss
;
; INVL_VERSION must be set (release.bat exports it from the VERSION file).

#define AppVersion GetEnv("INVL_VERSION")

[Setup]
; Never change AppId: it is how upgrades and the uninstaller find existing
; installs.
AppId={{E53C43EE-5FE2-44AA-B045-11A62B178866}
AppName=INVL Hub
AppVersion={#AppVersion}
AppPublisher=INVL LLC
AppPublisherURL=https://getinvl.com
AppSupportURL=https://getinvl.com/download
LicenseFile=..\EULA.txt
; Per-user install: no UAC admin prompt, and {autopf} resolves to the user's
; local programs folder.
PrivilegesRequired=lowest
DefaultDirName={autopf}\INVL Hub
DisableProgramGroupPage=yes
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\INVLHub.exe
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=INVLHub-{#AppVersion}-windows-x64-setup

[Files]
Source: "..\dist\INVLHub\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Icons]
Name: "{autoprograms}\INVL Hub"; Filename: "{app}\INVLHub.exe"
Name: "{autodesktop}\INVL Hub"; Filename: "{app}\INVLHub.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\INVLHub.exe"; Description: "{cm:LaunchProgram,INVL Hub}"; Flags: nowait postinstall skipifsilent
