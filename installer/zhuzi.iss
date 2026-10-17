; Inno Setup 6.3+ script. Built in CI (see .github/workflows/release.yml):
;   set ZHUZI_VERSION=1.0.0  ZHUZI_ARCH=arm64|x64  ZHUZI_SOURCE=<dist\Zhuzi-arm64>
;   ISCC installer\zhuzi.iss
; The installed app keeps its data in %LOCALAPPDATA%\Zhuzi (no `portable` marker).

#define AppVersion GetEnv("ZHUZI_VERSION")
#define Arch GetEnv("ZHUZI_ARCH")
#define Source GetEnv("ZHUZI_SOURCE")

[Setup]
AppId={{6F0C2B9E-4D7A-4E21-9C2B-5A1E7D3F8B40}
AppName=逐字
AppVersion={#AppVersion}
AppVerName=逐字 {#AppVersion}
AppPublisher=Zhuzi contributors
AppPublisherURL=https://github.com/EllenZhang708/zhuzi
AppSupportURL=https://github.com/EllenZhang708/zhuzi/issues
DefaultDirName={autopf}\Zhuzi
DefaultGroupName=逐字
DisableProgramGroupPage=yes
UninstallDisplayName=逐字
UninstallDisplayIcon={app}\Zhuzi.exe
OutputDir=..\dist
; No version in the name, so releases/latest/download/Zhuzi-<arch>-setup.exe always works.
OutputBaseFilename=Zhuzi-{#Arch}-setup
SetupIconFile=..\assets\icon.ico
LicenseFile=..\LICENSE
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Per-user by default, so no administrator prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
#if Arch == "arm64"
ArchitecturesAllowed=arm64
ArchitecturesInstallIn64BitMode=arm64
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif

[Languages]
; Kept in the repo (from jrsoftware/issrc, Inno Setup 6.5+) so builds don't depend on the installed language packs.
Name: "zh"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; Flags: unchecked

[Files]
Source: "{#Source}\*"; Excludes: "portable,data\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\逐字"; Filename: "{app}\Zhuzi.exe"
Name: "{autodesktop}\逐字"; Filename: "{app}\Zhuzi.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Zhuzi.exe"; Description: "打开逐字"; Flags: nowait postinstall skipifsilent
