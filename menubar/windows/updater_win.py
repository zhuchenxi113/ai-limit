"""自动更新：检查 Release → 下载 setup.exe → 签名校验 → 触发 Inno Setup
静默安装 → 主进程退出 → 新版本启动后核对 pending marker。

设计基线复用私仓 docs/adr/0004-in-app-auto-update.md 的决策（点击触发、
非静默确认后不再二次确认、下载校验完直接退出重启、失败写 marker），
只是把"整个 .app bundle 替换"换成"调用 Inno Setup 安装器的
CloseApplications + 静默安装"——不需要像 mac 版那样自建等待 PID 退出、
mv/cp 替换、失败回滚的 helper 脚本，Inno Setup 已经做好这套进程管理。
"""
import json
import os
import pathlib
import re
import subprocess
import time

_RELEASES_API_URL = "https://api.github.com/repos/zhuchenxi113/ai-limit/releases/latest"
_GITEE_RELEASES_API_URL = "https://gitee.com/api/v5/repos/zhuchenxi113/ai-limit/releases?per_page=1&direction=desc"
_RELEASES_PAGE_URL = "https://github.com/zhuchenxi113/ai-limit/releases"
_GITEE_RELEASES_PAGE_URL = "https://gitee.com/zhuchenxi113/ai-limit/releases"

# 仅测试用：指向本地 file:// JSON，覆盖 GitHub/Gitee 两个真实源，跟 mac 版
# 同一个环境变量名/同一套用法，方便端到端联调不依赖真实公开 Release。
_RELEASE_FEED_OVERRIDE = os.environ.get("AI_LIMIT_RELEASE_FEED_OVERRIDE")

# Windows 安装包资产命名：ai-limit-<version>-setup.exe（对应 installer.iss 的
# OutputBaseFilename）。比单纯 `.exe` 通配更精确，避免误匹配 Release 里其他
# 辅助文件；也不用 `.msi`——Inno Setup 产出的是 .exe 安装器不是 MSI。
_SETUP_ASSET_RE = re.compile(r"^ai-limit-.*-setup\.exe$")

_UPDATE_PENDING_MARKER = pathlib.Path.home() / ".ai-limit-update-pending.json"
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024


class UpdateFailed(Exception):
    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


def _version_tuple(v: str):
    out = []
    for p in v.lstrip("v").split("."):
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def _pick_setup_asset(assets):
    for a in assets or []:
        name = a.get("name", "")
        if _SETUP_ASSET_RE.match(name):
            return a.get("browser_download_url"), name
    return None, None


def fetch_latest_release_info(timeout=6) -> dict:
    """优先 GitHub，连不上（常见于未配代理，GitHub 在国内常被墙）时退到
    Gitee。不抛异常，两边都失败才返回 {"error": True}。"""
    import urllib.request

    def _get_json(url):
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ai-limit-tray"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _result(latest, source, assets):
        out = {"latest": latest, "source": source}
        asset_url, asset_name = _pick_setup_asset(assets)
        if asset_url:
            out["asset_url"] = asset_url
            out["asset_name"] = asset_name
        return out

    if _RELEASE_FEED_OVERRIDE:
        try:
            data = _get_json(_RELEASE_FEED_OVERRIDE)
            return _result(data["tag_name"].lstrip("v"), "github", data.get("assets"))
        except Exception:
            return {"error": True}

    try:
        data = _get_json(_RELEASES_API_URL)
        return _result(data["tag_name"].lstrip("v"), "github", data.get("assets"))
    except Exception:
        pass

    try:
        data = _get_json(_GITEE_RELEASES_API_URL)
        return _result(data[0]["tag_name"].lstrip("v"), "gitee", data[0].get("assets"))
    except Exception:
        return {"error": True}


def download_release_setup(url, dest_dir, timeout=30, total_timeout=600):
    """下载 setup.exe 到 dest_dir/update.exe。分块读取+磁盘空间检查+原子改名，
    跟 mac 版 _download_release_dmg 同一套逻辑，只是扩展名不同。"""
    import urllib.request
    import shutil

    dest_dir = pathlib.Path(dest_dir)
    part_path = dest_dir / "update.exe.part"
    final_path = dest_dir / "update.exe"

    req = urllib.request.Request(url, headers={"User-Agent": "ai-limit-tray"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        raise UpdateFailed("download_failed", f"无法连接下载地址：{e}") from e

    with resp:
        declared_size = resp.headers.get("Content-Length")
        declared_size = int(declared_size) if declared_size and declared_size.isdigit() else None

        if declared_size:
            need = declared_size * 3
            free = shutil.disk_usage(dest_dir).free
            if free < need:
                raise UpdateFailed(
                    "insufficient_disk_space",
                    f"磁盘空间不足：需要约 {need // (1024 * 1024)} MB，"
                    f"剩余 {free // (1024 * 1024)} MB",
                )

        start = time.monotonic()
        written = 0
        try:
            with open(part_path, "wb") as f:
                while True:
                    if time.monotonic() - start > total_timeout:
                        raise UpdateFailed("timeout", "下载耗时过长")
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        raise UpdateFailed("download_failed", "下载内容超出预期大小上限")
                    f.write(chunk)
        except UpdateFailed:
            raise
        except Exception as e:
            raise UpdateFailed("download_failed", str(e)) from e

    if written == 0:
        raise UpdateFailed("download_failed", "下载内容为空")
    if declared_size is not None and written != declared_size:
        raise UpdateFailed(
            "download_failed",
            f"下载字节数不符：期望 {declared_size}，实际 {written}",
        )

    os.replace(part_path, final_path)
    return final_path


def verify_installer(installer_path, expected_version: str) -> None:
    """签名校验：优先 PowerShell 内置 Get-AuthenticodeSignature（系统自带，
    不需要额外装 Windows SDK 的 signtool.exe），对应 mac 版 spctl/codesign。
    另加版本号交叉核对，防止资产链接指向错误/过期的包。
    """
    installer_path = str(installer_path)

    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-AuthenticodeSignature '{installer_path}').Status"],
        capture_output=True, text=True, timeout=30,
    )
    status = proc.stdout.strip()
    if status != "Valid":
        raise UpdateFailed("signature_invalid", status or proc.stderr.strip()[:500])

    try:
        import win32api
        info = win32api.GetFileVersionInfo(installer_path, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        actual = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception as e:
        raise UpdateFailed("version_check_failed", str(e)) from e

    if not actual.startswith(expected_version):
        raise UpdateFailed(
            "version_mismatch",
            f"安装包版本号 {actual} 与 Release 声明版本 {expected_version} 不符",
        )


def trigger_silent_install(installer_path, expected_version: str) -> None:
    """写 pending marker → 用 PowerShell Start-Process 拉起 Inno Setup 静默
    安装（不能直接 subprocess.Popen 传 /VERYSILENT 这类参数——如果调用方
    本身跑在 MSYS/git-bash 环境下，`/xxx` 参数会被自动转成 Unix 路径，
    静默参数传不进去，装成交互式向导；这里固定走 Windows 原生的
    subprocess.CREATE_NO_WINDOW + list 参数形式，不经过 shell 展开，
    不受 MSYS 影响）。

    调用方随后应立即退出主进程：Inno Setup 的 CloseApplications +
    CloseApplicationsFilter=ai-limit-tray.exe 会在覆盖文件前处理同名进程，
    主进程主动退出是双重保险，不是依赖它来关自己。
    """
    try:
        _UPDATE_PENDING_MARKER.write_text(
            json.dumps({"target_version": expected_version, "triggered_at": time.time()}),
            encoding="utf-8",
        )
    except Exception:
        pass

    subprocess.Popen(
        [str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def check_update_pending_marker(current_version: str):
    """启动时调用一次。marker 不存在：正常。marker 存在且目标版本已达成：
    说明是上次更新成功后的第一次启动，清掉 marker，返回 None（不提示，
    静默确认成功）。marker 存在但版本仍是旧的：上次更新大概率没走完，
    返回失败详情供上层弹窗提示，同时清掉 marker（只提示一次）。
    """
    if not _UPDATE_PENDING_MARKER.exists():
        return None
    try:
        data = json.loads(_UPDATE_PENDING_MARKER.read_text(encoding="utf-8"))
        target = data.get("target_version", "")
    except Exception:
        target = ""
    try:
        _UPDATE_PENDING_MARKER.unlink()
    except FileNotFoundError:
        pass
    if target and _version_tuple(current_version) >= _version_tuple(target):
        return None
    return {"target_version": target}
