; Inno Setup 安装脚本。构建前提：先跑 pyinstaller.spec 产出 dist\ai-limit-tray\，
; icon\ai-limit.ico 是受版本控制的正式构建输入；仅在明确更新产品图标时，
; 才使用维护者工具从原始设计素材重新生成。
; 编译：ISCC installer.iss（在 menubar\windows\ 目录下）
;
; AppId 是固定 GUID，一旦发布过一个版本就不能再改——Inno Setup 靠它判断
; "是不是同一个应用的升级安装"，改了会导致老版本卸载残留、新版本被当成
; 全新应用重复安装。

#define AppVersion "0.3.28"

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
; 即使检测到同一 AppId 的旧版本，也显示安装目录页，允许用户确认或自定义位置。
; UsePreviousAppDir 保持 yes，让升级时默认沿用现有目录。
DisableDirPage=no
UsePreviousAppDir=yes
DefaultGroupName=AI Limit
DisableProgramGroupPage=yes
OutputDir=dist_installer
OutputBaseFilename=ai-limit-windows-{#AppVersion}-setup
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

[CustomMessages]
chinesesimplified.RelocationWarning=安装位置将从“%1”更改为“%2”。安装程序会先卸载旧目录中的程序文件，再安装到新位置；用户设置会保留。是否继续？
english.RelocationWarning=The install location will change from "%1" to "%2". Setup will first remove the program files from the old location, then install to the new location. User settings will be preserved. Continue?
chinesesimplified.PreviousUninstallerMissing=无法迁移安装位置：找不到旧版卸载程序。请先从 Windows“已安装的应用”卸载旧版，然后重新运行安装程序。
english.PreviousUninstallerMissing=Setup cannot move the installation because the previous uninstaller is missing. Uninstall the previous version from Windows Installed apps, then run Setup again.
chinesesimplified.PreviousUninstallFailed=无法迁移安装位置：旧版卸载失败（退出码 %1）。请关闭 AI Limit，手动卸载旧版，然后重新运行安装程序。
english.PreviousUninstallFailed=Setup cannot move the installation because uninstalling the previous version failed (exit code %1). Close AI Limit, uninstall the previous version manually, then run Setup again.
chinesesimplified.PreviousInstallStillPresent=无法迁移安装位置：旧目录中的程序仍在使用或未能删除。请关闭 AI Limit，必要时重启 Windows，再重新运行安装程序。
english.PreviousInstallStillPresent=Setup cannot move the installation because program files in the old location are still in use or could not be removed. Close AI Limit, restart Windows if necessary, then run Setup again.

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

[Code]
const
  UninstallSubkey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{FE4A5B6A-1833-45D6-80E5-0FADAC018795}_is1';

var
  PreviousInstallDir: String;
  PreviousUninstallExe: String;
  ConfirmedRelocationDir: String;

function NormalizedPath(Path: String): String;
begin
  Result := Lowercase(RemoveBackslashUnlessRoot(ExpandFileName(Path)));
end;

function IsRelocation: Boolean;
begin
  Result :=
    (PreviousInstallDir <> '') and
    (CompareText(NormalizedPath(PreviousInstallDir),
      NormalizedPath(WizardDirValue)) <> 0);
end;

procedure InitializeWizard;
var
  UninstallString: String;
begin
  PreviousInstallDir := '';
  PreviousUninstallExe := '';
  ConfirmedRelocationDir := '';

  RegQueryStringValue(
    HKCU, UninstallSubkey, 'InstallLocation', PreviousInstallDir);
  if RegQueryStringValue(
    HKCU, UninstallSubkey, 'UninstallString', UninstallString) then
    PreviousUninstallExe := RemoveQuotes(UninstallString);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  TargetDir: String;
begin
  Result := True;
  if (CurPageID <> wpSelectDir) or not IsRelocation then
    exit;

  TargetDir := NormalizedPath(WizardDirValue);
  if CompareText(ConfirmedRelocationDir, TargetDir) = 0 then
    exit;

  Result := MsgBox(
    FmtMessage(ExpandConstant('{cm:RelocationWarning}'), [PreviousInstallDir, WizardDirValue]),
    mbConfirmation, MB_YESNO) = IDYES;
  if Result then
    ConfirmedRelocationDir := TargetDir;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  OldExecutable: String;
begin
  Result := '';
  if not IsRelocation then
    exit;

  if (PreviousUninstallExe = '') or not FileExists(PreviousUninstallExe) then
  begin
    Result := ExpandConstant('{cm:PreviousUninstallerMissing}');
    exit;
  end;

  if not Exec(
    PreviousUninstallExe,
    '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART',
    ExtractFileDir(PreviousUninstallExe), SW_HIDE,
    ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
  begin
    Result := FmtMessage(
      ExpandConstant('{cm:PreviousUninstallFailed}'), [IntToStr(ResultCode)]);
    exit;
  end;

  OldExecutable := AddBackslash(PreviousInstallDir) + 'ai-limit-tray.exe';
  if FileExists(OldExecutable) then
  begin
    Result := ExpandConstant('{cm:PreviousInstallStillPresent}');
    exit;
  end;

  PreviousInstallDir := '';
  PreviousUninstallExe := '';
end;
