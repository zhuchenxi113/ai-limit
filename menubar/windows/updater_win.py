"""Windows 半自动更新：检查 Release → 下载 setup.exe → 校验安装包 → 交给
Windows Shell 以可见方式启动 → 主进程退出 → 新版本核对 pending marker。

设计基线复用私仓 docs/adr/0004-in-app-auto-update.md 的决策（点击触发、
失败写 marker）。Windows 安装包必须先通过应用内置公钥的 Ed25519 独立签名；
随后再检查 Authenticode 和 PE 文件版本。未签名不等于签名损坏：前者可在
Ed25519 通过后继续，后者必须拒绝。安装包不静默执行；启动前仍写入 Mark of
the Web（互联网来源标记），但 SmartScreen 是否弹窗只由系统决定，不承担
应用的真实性校验。
"""
import base64
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.parse

import update_signing

_RELEASES_API_URL = "https://api.github.com/repos/zhuchenxi113/ai-limit/releases?per_page=10"
_GITEE_RELEASES_API_URL = "https://gitee.com/api/v5/repos/zhuchenxi113/ai-limit/releases?per_page=10&direction=desc"
_RELEASES_PAGE_URL = "https://github.com/zhuchenxi113/ai-limit/releases"
_GITEE_RELEASES_PAGE_URL = "https://gitee.com/zhuchenxi113/ai-limit/releases"

# 仅测试用：指向本地 file:// JSON，覆盖 GitHub/Gitee 两个真实源，跟 mac 版
# 同一个环境变量名/同一套用法，方便端到端联调不依赖真实公开 Release。
_RELEASE_FEED_OVERRIDE = os.environ.get("AI_LIMIT_RELEASE_FEED_OVERRIDE")

# v0.3.26 起正式 Windows 资产带平台名。旧名 fallback 原本是为了兼容
# v0.3.25 更新器（它精确查找 ai-limit-<version>-setup.exe）；v0.3.27 起
# 已不再发布旧名资产（见 AI_CONTEXT.md 约束 12），这里的 fallback 分支
# 对新发布的 Release 永远不会命中，只是无害保留，不代表旧名资产还在发。
_SETUP_ASSET_RE = re.compile(r"^ai-limit-windows-.*-setup\.exe$")
_LEGACY_SETUP_ASSET_RE = re.compile(r"^ai-limit-.*-setup\.exe$")

_UPDATE_PENDING_MARKER = pathlib.Path.home() / ".ai-limit-update-pending.json"
_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024
_MAX_SIGNATURE_BYTES = 16 * 1024


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


def _pick_setup_asset(assets, expected_version=None):
    if expected_version:
        expected_names = (
            f"ai-limit-windows-{expected_version}-setup.exe",
            f"ai-limit-{expected_version}-setup.exe",
        )
        for expected_name in expected_names:
            for asset in assets or []:
                if asset.get("name") == expected_name:
                    return asset.get("browser_download_url"), expected_name
        return None, None

    # 没有版本约束时也优先正式平台名，再回退旧名。
    for pattern in (_SETUP_ASSET_RE, _LEGACY_SETUP_ASSET_RE):
        for asset in assets or []:
            name = asset.get("name", "")
            if pattern.match(name):
                return asset.get("browser_download_url"), name
    return None, None


def _pick_signature_asset(assets, setup_name):
    expected_name = f"{setup_name}.sig" if setup_name else None
    for asset in assets or []:
        if expected_name and asset.get("name") == expected_name:
            return asset.get("browser_download_url"), expected_name
    return None, None


def _pick_release_with_setup_asset(releases):
    """从新到旧扫描 Release 列表，返回第一条带 Windows 安装资产的版本。

    单平台先发布期间，最新一条 Release 可能只有另一个平台（macOS）的
    资产；直接取最新 tag 会让 Windows 端错误地提示"有更新"却找不到能下载
    的安装包。跳过 draft/prerelease，保持跟旧版直接调用 releases/latest
    时的隐式过滤行为一致（详见 docs/reference/lessons.md 2026-08-04 条目）。
    """
    for release in releases or []:
        if release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name", "").lstrip("v")
        assets = release.get("assets")
        asset_url, asset_name = _pick_setup_asset(assets, tag)
        if asset_url:
            return tag, assets, asset_url, asset_name
    return None, None, None, None


def _validate_download_url(url: str) -> None:
    """只允许官方 Release 使用的 HTTPS 主机；测试 feed 可使用 file://。"""
    parsed = urllib.parse.urlparse(url)
    if _RELEASE_FEED_OVERRIDE and parsed.scheme == "file":
        return
    host = (parsed.hostname or "").lower()
    allowed_host = (
        host == "github.com"
        or host.endswith(".githubusercontent.com")
        or host == "gitee.com"
        or host.endswith(".gitee.com")
    )
    if parsed.scheme != "https" or not allowed_host:
        raise UpdateFailed("unsafe_download_url", f"不允许的安装包下载地址：{url}")


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

    def _result_from_releases(releases, source):
        tag, assets, asset_url, asset_name = _pick_release_with_setup_asset(releases)
        if tag is None:
            return {"error": True}
        out = {"latest": tag, "source": source, "asset_url": asset_url, "asset_name": asset_name}
        signature_url, signature_name = _pick_signature_asset(assets, asset_name)
        if signature_url:
            out["signature_url"] = signature_url
            out["signature_name"] = signature_name
        return out

    if _RELEASE_FEED_OVERRIDE:
        try:
            data = _get_json(_RELEASE_FEED_OVERRIDE)
            return _result_from_releases([data], "github")
        except Exception:
            return {"error": True}

    try:
        data = _get_json(_RELEASES_API_URL)
        result = _result_from_releases(data, "github")
        if not result.get("error"):
            return result
    except Exception:
        pass

    try:
        data = _get_json(_GITEE_RELEASES_API_URL)
        result = _result_from_releases(data, "gitee")
        if not result.get("error"):
            return result
    except Exception:
        pass

    return {"error": True}


def download_release_setup(url, dest_dir, timeout=30, total_timeout=600):
    """下载 setup.exe 到 dest_dir/update.exe。分块读取+磁盘空间检查+原子改名，
    跟 mac 版 _download_release_dmg 同一套逻辑，只是扩展名不同。"""
    import urllib.request
    import shutil

    dest_dir = pathlib.Path(dest_dir)
    part_path = dest_dir / "update.exe.part"
    final_path = dest_dir / "update.exe"

    _validate_download_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "ai-limit-tray"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        raise UpdateFailed("download_failed", f"无法连接下载地址：{e}") from e

    with resp:
        final_url = resp.geturl()
        _validate_download_url(final_url)
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


def download_release_signature(url, dest_dir, timeout=30):
    """下载很小的 Ed25519 签名 JSON；超限或重定向到非官方主机即失败。"""
    import urllib.request

    dest_dir = pathlib.Path(dest_dir)
    part_path = dest_dir / "update.exe.sig.part"
    final_path = dest_dir / "update.exe.sig"
    _validate_download_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "ai-limit-tray"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        raise UpdateFailed("signature_download_failed", f"无法下载更新签名：{e}") from e

    with resp:
        _validate_download_url(resp.geturl())
        data = resp.read(_MAX_SIGNATURE_BYTES + 1)
    if not data:
        raise UpdateFailed("signature_download_failed", "更新签名文件为空")
    if len(data) > _MAX_SIGNATURE_BYTES:
        raise UpdateFailed("signature_download_failed", "更新签名文件超出大小上限")
    try:
        part_path.write_bytes(data)
        os.replace(part_path, final_path)
    except Exception as e:
        raise UpdateFailed("signature_download_failed", str(e)) from e
    return final_path


def _authenticode_status(installer_path) -> str:
    installer_str = str(installer_path)
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "& { param([string]$Path) "
         "(Get-AuthenticodeSignature -LiteralPath $Path).Status }",
         installer_str],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    status = proc.stdout.strip()
    if proc.returncode != 0 or not status:
        raise UpdateFailed(
            "signature_check_failed",
            proc.stderr.strip()[:500] or "无法读取 Authenticode 状态",
        )
    return status


def verify_installer(installer_path, signature_path, expected_version: str,
                     expected_asset_name: str) -> str:
    """校验 Ed25519、PE 文件、Authenticode 状态和文件版本。

    返回 ``"signed"`` 或 ``"unsigned"``。只有明确的 ``NotSigned`` 才能按
    未签名包继续；HashMismatch、NotTrusted、UnknownError 等状态均失败关闭，
    不能把一个带有坏签名的文件降级成普通未签名文件运行。
    """
    installer_path = pathlib.Path(installer_path)
    try:
        with installer_path.open("rb") as f:
            if f.read(2) != b"MZ":
                raise UpdateFailed("invalid_installer", "安装包不是 Windows PE 可执行文件")
    except UpdateFailed:
        raise
    except Exception as e:
        raise UpdateFailed("invalid_installer", str(e)) from e

    installer_str = str(installer_path)
    try:
        update_signing.verify_detached_signature(
            installer_path, signature_path, expected_asset_name
        )
    except update_signing.SignatureError as e:
        raise UpdateFailed("update_signature_invalid", str(e)) from e

    status = _authenticode_status(installer_str)
    if status not in ("Valid", "NotSigned"):
        raise UpdateFailed("signature_invalid", status)

    try:
        import win32api
        info = win32api.GetFileVersionInfo(installer_str, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        actual = f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception as e:
        raise UpdateFailed("version_check_failed", str(e)) from e

    expected_parts = _version_tuple(expected_version)
    actual_parts = _version_tuple(actual)
    if actual_parts[:len(expected_parts)] != expected_parts:
        raise UpdateFailed(
            "version_mismatch",
            f"安装包版本号 {actual} 与 Release 声明版本 {expected_version} 不符",
        )
    return "signed" if status == "Valid" else "unsigned"


def _mark_installer_as_internet_file(installer_path, source_url: str) -> None:
    """写入 NTFS Zone.Identifier；失败时不允许执行下载得到的安装包。

    Python/urllib 不会像浏览器那样自动留下 Mark of the Web。ZoneId=3 表示
    Internet 区域，让 SmartScreen/附件策略得到来源证据；它不是更新包的
    真实性校验，Ed25519 验签已经在此步骤之前完成。
    """
    installer_path = pathlib.Path(installer_path)
    safe_url = (source_url or "").replace("\r", "").replace("\n", "")
    zone_path = pathlib.Path(f"{installer_path}:Zone.Identifier")
    content = "[ZoneTransfer]\nZoneId=3\n"
    if safe_url:
        content += f"HostUrl={safe_url}\n"
    try:
        zone_path.write_text(content, encoding="utf-8")
        saved = zone_path.read_text(encoding="utf-8")
    except Exception as e:
        raise UpdateFailed("motw_failed", f"无法写入互联网来源标记：{e}") from e
    if "ZoneId=3" not in saved:
        raise UpdateFailed("motw_failed", "互联网来源标记写入后无法核对")


def trigger_interactive_install(installer_path, expected_version: str,
                                source_url: str) -> None:
    """写来源标记和 pending marker，再交给独立 helper 打开安装向导。

    不传 ``/SILENT``/``/VERYSILENT``。helper 先等当前 App 退出，再通过
    Windows Shell 打开可见安装器；这样 SmartScreen 扫描或 ShellExecute 变慢
    不会把 Qt 主线程永久卡在“更新中”。用户取消系统安全提示（若系统显示）
    或安装向导时，下一次启动会根据 pending marker 明确提示更新未完成。
    """
    installer_path = pathlib.Path(installer_path)
    _mark_installer_as_internet_file(installer_path, source_url)

    marker_temp = _UPDATE_PENDING_MARKER.with_suffix(
        _UPDATE_PENDING_MARKER.suffix + ".tmp"
    )
    try:
        marker_temp.write_text(
            json.dumps({"target_version": expected_version, "triggered_at": time.time()}),
            encoding="utf-8",
        )
        os.replace(marker_temp, _UPDATE_PENDING_MARKER)
    except Exception as e:
        try:
            marker_temp.unlink()
        except FileNotFoundError:
            pass
        raise UpdateFailed("marker_failed", f"无法记录更新状态：{e}") from e

    installer_literal = "'" + str(installer_path).replace("'", "''") + "'"
    helper_script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$parentId = {os.getpid()}\n"
        f"$installer = {installer_literal}\n"
        "try { Wait-Process -Id $parentId -Timeout 30 -ErrorAction SilentlyContinue } catch {}\n"
        "Start-Process -FilePath $installer\n"
    )
    encoded_script = base64.b64encode(
        helper_script.encode("utf-16le")
    ).decode("ascii")
    powershell = (
        pathlib.Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    try:
        subprocess.Popen(
            [str(powershell), "-NoProfile", "-NonInteractive",
             "-WindowStyle", "Hidden", "-EncodedCommand", encoded_script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        try:
            _UPDATE_PENDING_MARKER.unlink()
        except FileNotFoundError:
            pass
        raise UpdateFailed("launch_failed", str(e)) from e


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
