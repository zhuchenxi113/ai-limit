# ai-limit CLI —— 一键安装脚本（PowerShell）
#
# 用法：
#   irm https://raw.githubusercontent.com/zhuchenxi113/ai-limit/main/install.ps1 | iex
#
# 流程：查询最新 GitHub Release → 下载独立 CLI 可执行文件
# ai-limit-windows-cli.exe → 存成 ai-limit.exe，放进
# %LOCALAPPDATA%\Programs\ai-limit-cli\ → 把这个目录加进当前用户 PATH
#
# 跟托盘 GUI App 的安装包（ai-limit-windows-<version>-setup.exe）是两条独立
# 路径：这个脚本只装 CLI，不装托盘图标；托盘 App 仍从官网/Release 页面手动
# 下载安装包。CLI 的 console=True 可执行文件由 cli_pyinstaller.spec 单独
# 编译产出，不能复用 GUI 的 ai-limit-tray.exe（console=False，从终端调用
# 不会输出任何内容，是 Windows 子系统机制决定的）。
#
# Release 资产名（ai-limit-windows-cli.exe）跟本地安装后的文件名（ai-limit.exe）
# 故意不一样：Release 页面上的名字要跟 GUI 安装包明显区分开、避免被手动点错
# 下载（这份资产是给这个脚本自动下载用的，不是给人在网页上手动点的）；本地
# 装完之后则要越短越好，因为它就是用户以后敲的命令名。
#
# 只写当前用户的注册表（HKCU），不需要管理员权限。

$ErrorActionPreference = "Stop"

$Repo = "zhuchenxi113/ai-limit"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\ai-limit-cli"
$ReleaseAssetName = "ai-limit-windows-cli.exe"
$LocalExeName = "ai-limit.exe"

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

$Asset = $Release.assets | Where-Object { $_.name -eq $ReleaseAssetName } | Select-Object -First 1
if (-not $Asset) {
    Write-Die "Release $Version 中未找到 $ReleaseAssetName（该版本可能还没发布独立 CLI 资产，见开源仓 README）"
}

Write-Ok "最新版本：$Version"

# ── 下载 ──────────────────────────────────────────────────────────────────────
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
}
$ExePath = Join-Path $InstallDir $LocalExeName

Write-Info "下载 $ReleaseAssetName…"
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

# ── WSL2：顺手加一条别名，让裸敲 ai-limit 也能用 ────────────────────────────
# WSL2 默认把 Windows PATH 接进自己的 $PATH（interop），所以 ai-limit.exe
# 已经能在 WSL2 的 bash 里调用——但 bash 按精确文件名匹配 PATH 里的可执行
# 文件，不会像 Windows 的 PATHEXT 那样自动给裸命令名补 .exe，所以额外加一条
# alias。只在检测到 WSL2 已装好且能跑（`wsl.exe -e true` 成功）时才动手，
# 且是幂等追加（先 grep 判断是否已存在这一行），不会重复写、不会覆盖用户
# 已有的 ~/.bashrc 内容。没装 WSL2 的机器上 `wsl.exe` 调用本身就会失败，
# 直接跳过，不算错误。
$wslDetected = $false
try {
    wsl.exe -e true 2>$null | Out-Null
    $wslDetected = ($LASTEXITCODE -eq 0)
} catch {
    $wslDetected = $false
}

if ($wslDetected) {
    Write-Info "检测到 WSL2，顺手在 ~/.bashrc 里加一条 ai-limit 别名…"
    $aliasLine = 'alias ai-limit="ai-limit.exe"'
    $bashCmd = "grep -qxF '$aliasLine' ~/.bashrc 2>/dev/null || echo '$aliasLine' >> ~/.bashrc"
    try {
        wsl.exe -e bash -c $bashCmd 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "WSL2 里也能直接敲 ai-limit 了（需要开一个新的 WSL2 终端窗口）"
        } else {
            Write-Info "WSL2 别名写入失败（不影响 Windows 侧安装结果，WSL2 里仍可以用 ai-limit.exe）"
        }
    } catch {
        Write-Info "WSL2 别名写入失败（不影响 Windows 侧安装结果，WSL2 里仍可以用 ai-limit.exe）"
    }
}

Write-Host ""
Write-Ok "ai-limit $Version 已安装"
Write-Host ""
Write-Info "试一下："
Write-Host "    ai-limit --days 1"
Write-Host ""
Write-Info "已打开的终端窗口需要重新打开一个新窗口才能识别到 ai-limit 命令，"
Write-Info "当前这个窗口因为脚本已经临时更新过 PATH，可以直接用。"
Write-Host ""
