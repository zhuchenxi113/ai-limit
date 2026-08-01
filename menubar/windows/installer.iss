; Inno Setup 安装脚本。构建前提：先跑 pyinstaller.spec 产出 dist\ai-limit-tray\，
; icon\ai-limit.ico 是受版本控制的正式构建输入；仅在明确更新产品图标时，
; 才使用维护者工具从原始设计素材重新生成。
; 编译：ISCC installer.iss（在 menubar\windows\ 目录下）
;
; AppId 是固定 GUID，一旦发布过一个版本就不能再改——Inno Setup 靠它判断
; "是不是同一个应用的升级安装"，改了会导致老版本卸载残留、新版本被当成
; 全新应用重复安装。

#define AppVersion "0.3.24"

[Setup]
AppId={{FE4A5B6A-1833-45D6-80E5-0FADAC018795}
AppName=AI Limit
AppVersion={#AppVersion}
AppVerName=AI Limit {#AppVersion}
; AppVersion 只控制安装界面/卸载信息；更新器读取的是 setup.exe 的 PE 文件版本。
; 未显式设置时 Inno Setup 默认为 0.0.0.0，会让版本交叉校验永远失败。
VersionInfoVersion={#AppVersion}
AppPublisher=zhuchenxi113
AppPublisherURL=https://github.com/zhuchenxi113/ai-limit
DefaultDirName={autopf}\AI Limit
DefaultGroupName=AI Limit
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=ai-limit-{#AppVersion}-setup
SetupIconFile=icon\ai-limit.ico
UninstallDisplayIcon={app}\ai-limit-tray.exe
WizardStyle=modern
ShowLanguageDialog=no
LanguageDetectionMethod=locale
UsePreviousLanguage=no
Compression=lzma2
SolidCompression=yes
; 装到用户目录，不需要管理员权限，减少 UAC 打扰，呼应 mac 版
; "复制到 /Applications 不需要 sudo" 的用户体验。
PrivilegesRequired=lowest
CloseApplications=yes
CloseApplicationsFilter=ai-limit-tray.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "chinesesimplified"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart"; Description: "{cm:AutoStartProgram,AI Limit}"; GroupDescription: "{cm:AutoStartProgramGroupDescription}"; Flags: unchecked

[Files]
Source: "dist\ai-limit-tray\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AI Limit"; Filename: "{app}\ai-limit-tray.exe"
Name: "{group}\{cm:UninstallProgram,AI Limit}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AI Limit"; Filename: "{app}\ai-limit-tray.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "AI Limit"; ValueData: """{app}\ai-limit-tray.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\ai-limit-tray.exe"; Description: "{cm:LaunchProgram,AI Limit}"; Flags: postinstall nowait skipifsilent
