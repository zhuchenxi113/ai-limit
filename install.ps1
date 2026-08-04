# ai-limit CLI —— 一键安装脚本（PowerShell）
#
# 用法：
#   irm https://raw.githubusercontent.com/zhuchenxi113/ai-limit/main/install.ps1 | iex
#
# 流程：查询最新 GitHub Release → 下载独立 CLI 可执行文件 ai-limit.exe →
# 放进 %LOCALAPPDATA%\Programs\ai-limit-cli\ → 把这个目录加进当前用户 PATH
#
# 跟托盘 GUI App 的安装包（ai-limit-windows-<version>-setup.exe）是两条独立
# 路径：这个脚本只装 CLI，不装托盘图标；托盘 App 仍从官网/Release 页面手动
# 下载安装包。CLI 的 console=True 可执行文件由 cli_pyinstaller.spec 单独
# 编译产出，不能复用 GUI 的 ai-limit-tray.exe（console=False，从终端调用
# 不会输出任何内容，是 Windows 子系统机制决定的）。
#
# 只写当前用户的注册表（HKCU），不需要管理员权限。

$ErrorActionPreference = "Stop"

$Repo = "zhuchenxi113/ai-limit"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\ai-limit-cli"
$ExeName = "ai-limit.exe"

function Write-Info($msg) { Write-Host "  • $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  V $msg" -ForegroundColor Green }
function Write-Die($msg) {
    Write-Host ""
    Write-Host "error: $msg" -ForegroundColor Red
    exit 1
}

# ── 查询最新 Release ─────────────────────────────────────────────────────────
Write-Info "查询最新版本…"
$ApiUrl = "https://api.github.com/repos/$Repo/releases/latest"

try {
    $Release = Invoke-RestMethod -Uri $ApiUrl -UseBasicParsing
} catch {
    Write-Die "无法查询 Release 信息：$($_.Exception.Message)"
}

$Version = $Release.tag_name
if (-not $Version) { Write-Die "无法获取版本号，请检查网络或稍后重试" }

$Asset = $Release.assets | Where-Object { $_.name -eq $ExeName } | Select-Object -First 1
if (-not $Asset) {
    Write-Die "Release $Version 中未找到 $ExeName（该版本可能还没发布独立 CLI 资产，见开源仓 README）"
}

Write-Ok "最新版本：$Version"

# ── 下载 ──────────────────────────────────────────────────────────────────────
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
$ExePath = Join-Path $InstallDir $ExeName

Write-Info "下载 $ExeName…"
try {
    Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile "$ExePath.tmp" -UseBasicParsing
} catch {
    Write-Die "下载失败：$($_.Exception.Message)"
}
Move-Item -Force "$ExePath.tmp" $ExePath
Write-Ok "下载完成：$ExePath"

# ── 注册进当前用户 PATH ──────────────────────────────────────────────────────
# 只操作分号分隔的一段，不整体覆盖，避免破坏用户 PATH 里其他工具的条目；
# 已经在里面就跳过（重复运行这个脚本是幂等的，用于升级场景）。
$CurrentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $CurrentPath) { $CurrentPath = "" }
$PathEntries = $CurrentPath -split ";" | Where-Object { $_ -ne "" }

if ($PathEntries -contains $InstallDir) {
    Write-Info "PATH 中已存在该目录，跳过注册"
} else {
    $NewPath = if ($CurrentPath.TrimEnd(";") -eq "") { $InstallDir } else { "$CurrentPath;$InstallDir" }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Ok "已把 $InstallDir 加入当前用户 PATH"

    # 只改注册表，资源管理器（Explorer）不会自动感知——它自己的环境变量是
    # 进程启动时的快照，不会因为注册表变了就自动刷新。广播 WM_SETTINGCHANGE
    # 让 Explorer 刷新这份缓存，之后由它派生的新终端（开始菜单/双击打开的）
    # 才能立即看到新 PATH，不用等注销重新登录。
    try {
        Add-Type -Namespace AiLimitInstall -Name NativeMethods -MemberDefinition @"
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(
    IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam,
    uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
"@ -ErrorAction Stop
        $HWND_BROADCAST = [IntPtr]0xffff
        $WM_SETTINGCHANGE = 0x001A
        $SMTO_ABORTIFHUNG = 0x0002
        $result = [UIntPtr]::Zero
        [AiLimitInstall.NativeMethods]::SendMessageTimeout(
            $HWND_BROADCAST, $WM_SETTINGCHANGE, [UIntPtr]::Zero, "Environment",
            $SMTO_ABORTIFHUNG, 5000, [ref]$result) | Out-Null
    } catch {
        Write-Info "刷新系统环境变量广播失败（不影响安装结果，只是可能需要注销重新登录才能在新终端里用 ai-limit）"
    }
}

# 同步更新当前会话的 PATH，脚本跑完这个终端窗口立即可用；
# 新开的终端窗口会自动从注册表读到新 PATH，不需要这一步。
$env:Path = "$env:Path;$InstallDir"

Write-Host ""
Write-Ok "ai-limit $Version 已安装"
Write-Host ""
Write-Info "试一下："
Write-Host "    ai-limit --days 1"
Write-Host ""
Write-Info "已打开的终端窗口需要重新打开一个新窗口才能识别到 ai-limit 命令，"
Write-Info "当前这个窗口因为脚本已经临时更新过 PATH，可以直接用。"
Write-Host ""
