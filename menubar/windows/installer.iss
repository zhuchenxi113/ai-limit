; Inno Setup 安装脚本。构建前提：先跑 pyinstaller.spec 产出 dist\ai-limit-tray\，
; 再跑 menubar\windows\make_ico.py 产出 icon\ai-limit.ico。
; 编译：ISCC installer.iss（在 menubar\windows\ 目录下）
;
; AppId 是固定 GUID，一旦发布过一个版本就不能再改——Inno Setup 靠它判断
; "是不是同一个应用的升级安装"，改了会导致老版本卸载残留、新版本被当成
; 全新应用重复安装。

#define AppVersion "0.3.23"

[Setup]
AppId={{FE4A5B6A-1833-45D6-80E5-0FADAC018795}
AppName=AI Limit
AppVersion={#AppVersion}
AppPublisher=zhuchenxi113
AppPublisherURL=https://github.com/zhuchenxi113/ai-limit
DefaultDirName={autopf}\AI Limit
DefaultGroupName=AI Limit
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=ai-limit-{#AppVersion}-setup
SetupIconFile=icon\ai-limit.ico
Compression=lzma2
SolidCompression=yes
; 装到用户目录，不需要管理员权限，减少 UAC 打扰，呼应 mac 版
; "复制到 /Applications 不需要 sudo" 的用户体验。
PrivilegesRequired=lowest
CloseApplications=yes
CloseApplicationsFilter=ai-limit-tray.exe
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Inno Setup 官方发行版不自带简体中文语言文件（社区翻译需要额外下载放进
; Languages 目录），v1 先只用英文安装向导；应用本身的界面已经是中英双语，
; 只有安装程序这几步向导页（欢迎/选目录/安装中/完成）是英文，后续想做
; 双语向导可以补装社区翻译的 ChineseSimplified.isl。
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\ai-limit-tray\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AI Limit"; Filename: "{app}\ai-limit-tray.exe"
Name: "{group}\{cm:UninstallProgram,AI Limit}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\ai-limit-tray.exe"; Description: "{cm:LaunchProgram,AI Limit}"; Flags: postinstall nowait skipifsilent
