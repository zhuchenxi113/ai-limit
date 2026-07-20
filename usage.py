#!/usr/bin/env python3
"""
usage.py — 查看 Claude Code + CodeX 本周 token 消耗与额度状态

用法：
    python tools/usage.py
    python tools/usage.py --days 3     # 只看最近 3 天
    python tools/usage.py --all        # 看全部历史（跨周汇总）
"""
import argparse
import base64
import functools
import datetime
import locale as _locale
import os
import json
import pathlib
import select
import shutil
import socket
import struct
import subprocess
import sys
import time

CLAUDE_BASE = pathlib.Path.home() / ".claude" / "projects"
CODEX_BASE = pathlib.Path.home() / ".codex" / "sessions"
_CODEX_WINDOW_CACHE = pathlib.Path.home() / ".codex_window_cache"
_MENUBAR_HISTORY_PATH = pathlib.Path.home() / ".ai-limit-menubar-history.jsonl"
TZ_LOCAL = datetime.datetime.now().astimezone().tzinfo
TZ_ABBR  = datetime.datetime.now().astimezone().strftime('%Z')
__version__ = "0.3.23"

# ── 外观配置（可直接修改） ────────────────────────────────────────────────────
WARN_THRESHOLD = 20    # 剩余低于此值（%）显示黄色
CRIT_THRESHOLD = 10    # 剩余低于此值（%）显示红色
COLOR_OK   = "\033[32m"   # 绿：正常（ANSI 色码，32=绿 33=黄 36=青 34=蓝）
COLOR_WARN = "\033[33m"   # 黄：偏低
COLOR_CRIT = "\033[31m"   # 红：告警
# ─────────────────────────────────────────────────────────────────────────────

_C   = sys.stdout.isatty()
_DIM = "\033[2m" if _C else ""
_BOLD= "\033[1m" if _C else ""
_RST = "\033[0m" if _C else ""
_OK  = COLOR_OK   if _C else ""
_WRN = COLOR_WARN if _C else ""
_CRT = COLOR_CRIT if _C else ""

def _bc(r: float) -> str:
    return _OK if r >= WARN_THRESHOLD else (_WRN if r >= CRIT_THRESHOLD else _CRT)


@functools.lru_cache(maxsize=1)
def _chrome_ua() -> str:
    ver = "124.0.0.0"
    candidates = (
        ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        if sys.platform == "darwin"
        else ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]
    )
    for cmd in candidates:
        try:
            raw = subprocess.check_output(
                [cmd, "--version"], timeout=3, stderr=subprocess.DEVNULL,
            ).decode().strip()
            parts = raw.split()
            if parts:
                ver = parts[-1]
                break
        except Exception:
            continue
    return (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{ver} Safari/537.36"
    )

def _colored_bar(remaining: float, width: int = 20) -> str:
    filled = round(remaining / 100 * width)
    return f"{_bc(remaining)}{'█'*filled}{_DIM}{'░'*(width-filled)}{_RST}"

def _bold_bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return f"{_BOLD}{'█'*filled}{_RST}{_DIM}{'░'*(width-filled)}{_RST}"


REMOTE_TIMEOUT_SEC = 15
CLAUDE_WEB_TIMEOUT_SEC = 15


def _detect_lang() -> str:
    env = os.environ.get("AI_LIMIT_LANG", "")
    if env:
        return "zh" if env.lower().startswith("zh") else "en"
    try:
        loc = _locale.getlocale()[0] or os.environ.get("LANG", "")
        return "zh" if loc.startswith("zh") else "en"
    except Exception:
        return "en"


LANG = _detect_lang()


def t(zh: str, en: str) -> str:
    return zh if LANG == "zh" else en


# ── 工具函数 ─────────────────────────────────────────────────────────────────

CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
CODEX_USAGE_URL = "https://chatgpt.com/codex/cloud/settings/analytics"

# Statuspage.io 官方公开只读 API，无需鉴权；components.json 是全量组件列表
# （summary.json 只含 showcase=true 的组件，OpenAI 的 CLI/App 这些不在其中）
CLAUDE_STATUS_COMPONENTS_URL = "https://status.claude.com/api/v2/components.json"
CODEX_STATUS_COMPONENTS_URL = "https://status.openai.com/api/v2/components.json"
CLAUDE_STATUS_PAGE_URL = "https://status.claude.com/"
CODEX_STATUS_PAGE_URL = "https://status.openai.com/"

# Statuspage 组件 status 取值的严重度排序，用于多选取最差
STATUS_SEVERITY = {
    "operational": 0,
    "under_maintenance": 1,
    "degraded_performance": 2,
    "partial_outage": 3,
    "major_outage": 4,
    "critical": 4,
}



def ts_to_local(iso: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(TZ_LOCAL)


def epoch_to_local(epoch: int) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(epoch, tz=TZ_LOCAL)


def bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def remaining_percent(used_pct: float) -> float:
    return max(0, min(100, 100 - used_pct))


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_plan(plan: str) -> str:
    if not plan or plan == "?":
        return "?"
    return str(plan).replace("_", " ").title()


def fmt_dt(dt: datetime.datetime) -> str:
    return f"{dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def fmt_reset_dt(dt: datetime.datetime) -> str:
    _bare_zh = ["一", "二", "三", "四", "五", "六", "日"]
    _bare_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    today = datetime.datetime.now(TZ_LOCAL).date()
    target = dt.date()
    days = (target - today).days
    next_week = target.isocalendar()[:2] > today.isocalendar()[:2]
    if LANG == "zh":
        if days == 0:
            wd = "今天  "
        elif days == 1:
            wd = "明天  "
        elif days == 2:
            wd = "后天  "
        elif next_week:
            wd = f"下周{_bare_zh[dt.weekday()]}"
        else:
            wd = f"周{_bare_zh[dt.weekday()]}  "
    else:
        if days == 0:
            wd = "today   "
        elif days == 1:
            wd = "tomorrow"
        elif days == 2:
            wd = "2 days  "
        elif next_week:
            wd = f"next {_bare_en[dt.weekday()]}"
        else:
            wd = f"{_bare_en[dt.weekday()]:<8}"
    return f"{wd} {dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Claude Web 额度 (--claude-web) ────────────────────────────────────────────

_DIAG_LOG = pathlib.Path.home() / ".ai-limit-error.log"

def _diag_log(tag: str, info: dict, _max_lines: int = 200) -> None:
    """把出错现场写到 ~/.ai-limit-error.log，用于事后定位误判（如把瞬时 403
    误报成"需人机验证/重新登录"）。只在异常路径调用，频率低；保留最近
    _max_lines 条；任何写失败都静默吞掉，绝不影响主流程。"""
    try:
        line = json.dumps(
            {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
             "tag": tag, **info},
            ensure_ascii=False,
        )
        old = []
        try:
            old = _DIAG_LOG.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            pass
        lines = (old + [line])[-_max_lines:]
        _DIAG_LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def _cookie_summary(cookie_header: str) -> dict:
    """从 Cookie 请求头解析出关键 cookie 是否存在（不记录值，避免泄露 session）。
    用来区分'cookie 读取竞争读到残缺态'与'服务端真 403'。"""
    try:
        names = {p.split("=", 1)[0].strip() for p in cookie_header.split(";") if "=" in p}
    except Exception:
        names = set()
    return {
        "cookie_count": len(names),
        "has_sessionKey": "sessionKey" in names,
        "has_lastActiveOrg": "lastActiveOrg" in names,
        "has_cf_clearance": "cf_clearance" in names,
    }


class ClaudeWebError(Exception):
    """kind: 'generic' | 'cloudflare'（需人机验证）| 'auth'（登录失效）| 'timeout'"""
    def __init__(self, message, kind="generic"):
        super().__init__(message)
        self.kind = kind


def _claude_web_context(referer: str) -> tuple[str, dict]:
    try:
        import browser_cookie3
    except ImportError:
        raise ClaudeWebError(t(
            "未安装 browser_cookie3，请先运行: pip install browser-cookie3",
            "browser_cookie3 not installed, run: pip install browser-cookie3",
        ))

    cookies = []
    errs = []
    for name, loader in [("Chrome", browser_cookie3.chrome), ("Firefox", browser_cookie3.firefox)]:
        try:
            jar = loader(domain_name=".claude.ai")
            cookies = [(c.name, c.value) for c in jar]
            if cookies:
                break
        except Exception as e:
            errs.append(f"{name}: {e}")

    if not cookies:
        detail = f" ({'; '.join(errs)})" if errs else ""
        raise ClaudeWebError(t(
            f"无法读取浏览器 cookie{detail}，请先在浏览器登录 claude.ai",
            f"cannot read browser cookies{detail}, please log in to claude.ai first",
        ))

    cookie_dict = dict(cookies)
    org_id = cookie_dict.get("lastActiveOrg", "")
    if not org_id:
        raise ClaudeWebError(t(
            "未能从 cookie 读取 org ID，请先在浏览器打开 claude.ai",
            "could not read org ID from cookie, please open claude.ai in your browser",
        ))

    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    headers = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://claude.ai",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": _chrome_ua(),
    }
    return org_id, headers


def fetch_status_components(url: str, timeout: int = 5) -> list[dict] | None:
    """拉 Statuspage components.json，返回 [{'id','name','status'}] 全量列表。
    超时/网络错误重试一次；两次都失败返回 None——调用方不能拿 None 当"维持上次的值",
    必须显式展示"未知"，呼应额度数据不能用旧值兜底的同一条原则。"""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "User-Agent": _chrome_ua(),
    })
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
            return [
                {"id": c["id"], "name": c["name"], "status": c["status"]}
                for c in data.get("components", [])
            ]
        except Exception:
            if attempt == 0:
                continue
            return None


def worst_status(components: list[dict], selected_names: list[str]) -> tuple[str, str] | None:
    """从 components 里按 selected_names 过滤，取严重度最差的一项。
    并列时按 selected_names 里的顺序取排在前面的那个。
    没有任何组件命中 selected_names 时返回 None（勾选项在接口里消失了，或列表为空）。"""
    order = {name: i for i, name in enumerate(selected_names)}
    matched = [c for c in components if c["name"] in order]
    if not matched:
        return None
    matched.sort(key=lambda c: (-STATUS_SEVERITY.get(c["status"], 0), order[c["name"]]))
    worst = matched[0]
    return worst["status"], worst["name"]


def _claude_web_get(path: str, headers: dict, timeout: int) -> dict:
    import urllib.request
    import urllib.error

    url = f"https://claude.ai{path}"
    req = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read()[:600].decode(errors="replace")
        # Cf-Mitigated: challenge 是 Cloudflare 给拦截/挑战响应打的唯一标记，
        # 不随页面语言或 HTML 改版变化，优先以此判别；body 匹配仅作兜底。
        is_cf = bool(e.headers.get("cf-mitigated"))
        if not is_cf:
            low = raw.lower()
            is_cf = any(m in low for m in (
                "just a moment", "challenge-platform", "/cdn-cgi/", "请验证您是真人"))
        kind = "cloudflare" if is_cf else ("auth" if e.code in (401, 403) else "generic")
        # 记录现场，事后区分根因：瞬时 403 / 真 Cloudflare 挑战 / cookie 残缺
        _diag_log("claude_web_http_error", {
            "path": path,
            "code": e.code,
            "decided_kind": kind,
            "cf_mitigated": e.headers.get("cf-mitigated"),
            "cf_ray": e.headers.get("cf-ray"),
            "server": e.headers.get("server"),
            "body_head": raw[:200],
            **_cookie_summary(headers.get("Cookie", "")),
        })
        if is_cf:
            raise ClaudeWebError(t(
                "claude.ai 触发了 Cloudflare 人机验证，请在浏览器打开 claude.ai 通过验证后重试",
                "claude.ai is showing a Cloudflare human-verification challenge; "
                "open claude.ai in your browser, pass it, then retry",
            ), kind="cloudflare")
        if e.code in (401, 403):
            raise ClaudeWebError(t(
                "claude.ai 登录态已失效，请在浏览器重新登录",
                "claude.ai session expired, please re-login in your browser",
            ), kind="auth")
        raise ClaudeWebError(f"HTTP {e.code}: {raw[:300]}")
    except Exception as e:
        raise ClaudeWebError(str(e))

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ClaudeWebError(f"非 JSON 响应: {body[:300].decode(errors='replace')}")


def live_claude_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> dict:
    """
    通过浏览器 session cookie 调用 claude.ai/api/organizations/{org}/usage。
    返回形如 {"five_hour": {...}, "seven_day": {...}} 的 dict。
    """
    org_id, headers = _claude_web_context("https://claude.ai/settings/usage")
    return _claude_web_get(
        f"/api/organizations/{org_id}/usage",
        headers,
        timeout,
    )


def live_claude_plan(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> str | None:
    """
    读取 Claude 活跃组织能力，映射为用户可见套餐名；没有可靠字段时返回 None。
    """
    org_id, headers = _claude_web_context("https://claude.ai/settings/billing")
    data = _claude_web_get(
        f"/api/organizations/{org_id}",
        headers,
        timeout,
    )
    capabilities = set(data.get("capabilities") or [])
    raven_type = data.get("raven_type")
    if raven_type == "enterprise":
        return "Enterprise"
    if raven_type == "team":
        return "Team"
    if "claude_max" in capabilities:
        return "Max"
    if "claude_pro" in capabilities:
        return "Pro"
    if "raven" in capabilities:
        return "Enterprise"
    if "chat" in capabilities:
        return "Free"
    return None


# ── Claude 解析 ───────────────────────────────────────────────────────────────

def collect_claude(since: datetime.datetime):
    """
    返回 {model: {input, cache_create, cache_read, output, calls, days: set}}
    since 必须是 aware datetime (UTC)
    """
    totals: dict[str, dict] = {}
    since_ts = since.timestamp()
    for jf in sorted(CLAUDE_BASE.rglob("*.jsonl")):
        try:
            if jf.stat().st_mtime < since_ts:
                continue
            _parse_claude_file(jf, since, totals)
        except Exception:
            pass
    return totals


def _parse_claude_file(jf: pathlib.Path, since: datetime.datetime, totals: dict):
    with open(jf, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "assistant":
                continue
            ts_raw = rec.get("timestamp", "")
            if not ts_raw:
                continue
            t = datetime.datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if t < since:
                continue
            msg = rec.get("message") or {}
            usage = msg.get("usage") or {}
            model = msg.get("model", "unknown")
            totals.setdefault(model, {
                "input": 0, "cache_create": 0, "cache_read": 0,
                "output": 0, "calls": 0, "days": set(),
            })
            d = totals[model]
            d["input"] += usage.get("input_tokens", 0)
            d["cache_create"] += usage.get("cache_creation_input_tokens", 0)
            d["cache_read"] += usage.get("cache_read_input_tokens", 0)
            d["output"] += usage.get("output_tokens", 0)
            d["calls"] += 1
            d["days"].add(t.astimezone(TZ_LOCAL).date())


# ── CodeX 解析 ────────────────────────────────────────────────────────────────

class CodexRemoteError(Exception):
    pass


def live_codex_rate_limits(timeout: int = REMOTE_TIMEOUT_SEC):
    """
    通过 Codex CLI 自带 app-server 实时读取账户额度。

    返回 (timestamp, normalized_rate_limits_dict)。
    失败时抛 CodexRemoteError，由调用方回退到本地 jsonl 快照。
    """
    if not shutil.which("codex"):
        raise CodexRemoteError("codex command not found")

    try:
        port = find_free_local_port()
        proc = subprocess.Popen(
            ["codex", "app-server", "--listen", f"ws://127.0.0.1:{port}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as e:
        raise CodexRemoteError(str(e)) from e
    try:
        _wait_codex_app_server(proc, port, timeout)
        result = _read_codex_rate_limits_ws(port, timeout)
        rl = result.get("rateLimits") or {}
        if not rl:
            raise CodexRemoteError("empty rate limits response")
        return datetime.datetime.now(datetime.timezone.utc), _normalize_remote_rate_limits(rl)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_codex_app_server(proc: subprocess.Popen, port: int, timeout: int):
    deadline = time.monotonic() + timeout
    lines: list[str] = []
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise CodexRemoteError("app-server exited: " + "".join(lines[-3:]).strip())
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            pass
        if proc.stdout:
            ready, _, _ = select.select([proc.stdout], [], [], 0)
            if ready:
                lines.append(proc.stdout.readline())
        time.sleep(0.1)
    raise CodexRemoteError("app-server start timed out")


def _normalize_remote_rate_limits(rl: dict) -> dict:
    def win(w):
        if not w:
            return None
        return {
            "used_percent": w.get("usedPercent", 0),
            "window_minutes": w.get("windowDurationMins"),
            "resets_at": w.get("resetsAt"),
        }

    return {
        "limit_id": rl.get("limitId"),
        "limit_name": rl.get("limitName"),
        "primary": win(rl.get("primary")),
        "secondary": win(rl.get("secondary")),
        "credits": rl.get("credits"),
        "plan_type": rl.get("planType"),
        "rate_limit_reached_type": rl.get("rateLimitReachedType"),
    }


def _read_codex_rate_limits_ws(port: int, timeout: int) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as s:
        _ws_handshake(s, port)
        _ws_send_json(s, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "ai-limit", "title": "ai-limit", "version": "0"},
                "capabilities": {"experimentalApi": True, "requestAttestation": False},
            },
        })
        _ws_send_json(s, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "account/rateLimits/read",
            "params": None,
        })

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            s.settimeout(max(0.1, deadline - time.monotonic()))
            msg = _ws_recv_json(s)
            if msg.get("id") == 2:
                if "error" in msg:
                    raise CodexRemoteError(str(msg["error"]))
                return msg.get("result") or {}
        raise CodexRemoteError("rate limit response timed out")


def _ws_handshake(s: socket.socket, port: int):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET / HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    if b" 101 " not in resp.split(b"\r\n", 1)[0]:
        raise CodexRemoteError("websocket handshake failed")


def _ws_send_json(s: socket.socket, obj: dict):
    payload = json.dumps(obj, separators=(",", ":")).encode()
    key = os.urandom(4)
    n = len(payload)
    if n < 126:
        hdr = bytes([0x81, 0x80 | n])
    elif n < 65536:
        hdr = bytes([0x81, 0x80 | 126]) + struct.pack("!H", n)
    else:
        hdr = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", n)
    masked = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    s.sendall(hdr + key + masked)


def _ws_recv_json(s: socket.socket) -> dict:
    opcode, payload = _ws_recv_frame(s)
    if opcode == 8:
        raise CodexRemoteError("websocket closed")
    if opcode != 1:
        return {}
    return json.loads(payload.decode("utf-8"))


def _ws_recv_frame(s: socket.socket):
    h = _recv_exact(s, 2)
    b1, b2 = h
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack("!H", _recv_exact(s, 2))[0]
    elif n == 127:
        n = struct.unpack("!Q", _recv_exact(s, 8))[0]
    key = _recv_exact(s, 4) if (b2 & 0x80) else b""
    payload = _recv_exact(s, n) if n else b""
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return b1 & 0x0F, payload


def _recv_exact(s: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise CodexRemoteError("unexpected EOF")
        data += chunk
    return data


class CodexWebError(Exception):
    pass


class CodexAuthError(CodexWebError):
    """401 / 403：未登录 ChatGPT 或无 Codex 权限（可能未订阅）。
    捕获后应直接跳过所有 fallback，app-server 也会因同样原因失败。"""
    pass


def _load_chatgpt_cookies():
    try:
        import browser_cookie3
    except ImportError:
        raise CodexWebError(t(
            "未安装 browser_cookie3，请先运行: pip install browser-cookie3",
            "browser_cookie3 not installed, run: pip install browser-cookie3",
        ))
    errs = []
    for name, loader in [("Chrome", browser_cookie3.chrome), ("Firefox", browser_cookie3.firefox)]:
        try:
            jar = loader(domain_name=".chatgpt.com")
            cookies = [(c.name, c.value) for c in jar]
            if cookies:
                return cookies
        except Exception as e:
            errs.append(f"{name}: {e}")
    detail = f" ({'; '.join(errs)})" if errs else ""
    raise CodexWebError(t(
        f"无法读取 chatgpt.com cookie{detail}，请先在浏览器登录 chatgpt.com",
        f"cannot read chatgpt.com cookies{detail}, please log in to chatgpt.com in your browser",
    ))


def _chatgpt_headers(cookie_header: str, *, referer: str = "https://chatgpt.com/codex/cloud/settings/analytics", bearer: str = None) -> dict:
    # 包含 Sec-Fetch-* / Accept-Language / Referer，避免 Cloudflare 反爬 403
    h = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _chrome_ua(),
        "Referer": referer,
        "Origin": "https://chatgpt.com",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    if bearer:
        h["Authorization"] = f"Bearer {bearer}"
    return h


def _get_chatgpt_access_token(cookie_header: str, timeout: int) -> str:
    import urllib.request
    import urllib.error
    req = urllib.request.Request(
        "https://chatgpt.com/api/auth/session",
        headers=_chatgpt_headers(cookie_header),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise CodexWebError(f"session HTTP {e.code}")
    except Exception as e:
        raise CodexWebError(f"session: {e}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise CodexWebError("session: non-JSON response")
    token = data.get("accessToken")
    if not token:
        raise CodexWebError(t(
            "请先在浏览器登录 chatgpt.com",
            "please log in to chatgpt.com in your browser",
        ))
    return token


def _normalize_web_rate_limits(data: dict) -> dict:
    rl = data.get("rate_limit") or {}

    def win(w):
        if not w:
            return None
        wsec = w.get("limit_window_seconds")
        return {
            "used_percent": w.get("used_percent", 0),
            "window_minutes": wsec // 60 if wsec else None,
            "resets_at": w.get("reset_at"),
        }

    plan = data.get("plan_type")
    return {
        "limit_id": None,
        "limit_name": None,
        "primary": win(rl.get("primary_window")),
        "secondary": win(rl.get("secondary_window")),
        "credits": data.get("credits"),
        "plan_type": plan,
        "rate_limit_reached_type": (rl or {}).get("rate_limit_reached_type"),
    }


def live_codex_web_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC):
    """
    通过浏览器 cookie 读取 chatgpt.com 的 Codex usage 接口。

    返回 (timestamp, normalized_rate_limits_dict)。

    与 app-server 不同，此端点为只读分析接口，不会触发新的 5 小时窗口；
    且数据覆盖 Cloud + CLI 真实合并用量。失败抛 CodexWebError。
    """
    import urllib.request
    import urllib.error
    cookies = _load_chatgpt_cookies()
    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies)
    token = _get_chatgpt_access_token(cookie_header, timeout)
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/codex/usage",
        headers=_chatgpt_headers(cookie_header, bearer=token),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CodexAuthError(
                t(
                    f"HTTP {e.code}：未登录 ChatGPT 或无 Codex 权限（可能未订阅，或需重新登录）",
                    f"HTTP {e.code}: not signed in to ChatGPT or no Codex access (subscription may be required)",
                )
            )
        raise CodexWebError(f"HTTP {e.code}")
    except Exception as e:
        raise CodexWebError(str(e))
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise CodexWebError("non-JSON response")
    return datetime.datetime.now(datetime.timezone.utc), _normalize_web_rate_limits(data)


def _load_window_cache():
    """读取上次 live 查询缓存的窗口到期时间（Unix 秒），失败返回 None"""
    try:
        return float(_CODEX_WINDOW_CACHE.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _save_window_cache(resets_at_unix):
    """缓存最新的窗口到期时间"""
    try:
        _CODEX_WINDOW_CACHE.write_text(str(resets_at_unix))
    except OSError:
        pass


def _prompt_app_server_confirm() -> bool:
    """交互式询问是否允许 app-server 查询触发新的 5h 窗口。

    仅在 TTY 环境调用；返回 True 表示用户同意继续。
    """
    msg = t(
        "Web 查询失败，且当前窗口未激活。\n"
        "继续调用 app-server 会触发新的 Codex 5 小时冷却窗口。\n"
        "确认继续？[y/N]: ",
        "Web fetch failed and no active window cached.\n"
        "Calling app-server will trigger a new Codex 5-hour cooldown.\n"
        "Continue? [y/N]: ",
    )
    try:
        ans = input(msg).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return ans in ("y", "yes")


def current_codex_rate_limits():
    """返回 (timestamp, rate_limits_dict, source_label, fallback_reason)

    数据源优先级：
      1. chatgpt.com web 接口（只读，不触发窗口；覆盖 Cloud + CLI 合并用量）
      2. app-server live（条件守卫：cached_expiry > now 时安全直调；
         否则在 TTY 下二次询问，非 TTY 直接跳过，避免误触发新 5h 窗口）
      3. 本地快照（~/.codex/sessions/）
    """
    reasons = []

    # 1. web 优先：不触发窗口，安全
    try:
        ts, rl = live_codex_web_usage()
        resets_at = (rl.get("primary") or {}).get("resets_at")
        if resets_at:
            _save_window_cache(float(resets_at))
        return ts, rl, "web", None
    except CodexAuthError as e:
        # 认证/权限错误：app-server 也会因同样原因失败，直接跳过所有 fallback
        return None, None, "no_access", str(e)
    except CodexWebError as e:
        reasons.append(f"web: {e}")
    except Exception as e:
        reasons.append(f"web: {e.__class__.__name__}: {e}")

    # 2. app-server：仅在安全条件下调用
    cached_expiry = _load_window_cache()
    now_unix = datetime.datetime.now(datetime.timezone.utc).timestamp()
    window_active = cached_expiry is not None and cached_expiry > now_unix

    if window_active:
        allow_app_server = True
    elif sys.stdin.isatty() and sys.stdout.isatty():
        allow_app_server = _prompt_app_server_confirm()
        if not allow_app_server:
            reasons.append("app-server: user_declined")
    else:
        allow_app_server = False
        reasons.append("app-server: non_tty_skip")

    if allow_app_server:
        try:
            ts, rl = live_codex_rate_limits()
            resets_at = (rl.get("primary") or {}).get("resets_at")
            if resets_at:
                _save_window_cache(float(resets_at))
            return ts, rl, "live", None
        except (CodexRemoteError, OSError, subprocess.SubprocessError) as e:
            reasons.append(f"app-server: {e or e.__class__.__name__}")
        except Exception as e:
            reasons.append(f"app-server: {e.__class__.__name__}: {e}")

    # 3. 本地快照兜底
    ts, rl = latest_codex_rate_limits()
    return ts, rl, "snapshot", " → ".join(reasons) if reasons else None


def latest_codex_rate_limits():
    """返回 (timestamp, rate_limits_dict) 或 (None, None)"""
    latest_ts = None
    latest_rl = None
    for jf in sorted(CODEX_BASE.rglob("*.jsonl")):
        try:
            ts, rl = _scan_codex_file(jf)
        except Exception:
            continue
        if rl and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
            latest_rl = rl
    return latest_ts, latest_rl


def _scan_codex_file(jf: pathlib.Path):
    best_ts = None
    best_rl = None
    with open(jf, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "event_msg":
                continue
            payload = rec.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            rl = payload.get("rate_limits")
            if not rl:
                continue
            ts = datetime.datetime.fromisoformat(
                rec["timestamp"].replace("Z", "+00:00")
            )
            if best_ts is None or ts > best_ts:
                best_ts = ts
                best_rl = rl
    return best_ts, best_rl


def collect_codex_tokens(since: datetime.datetime):
    """返回 {date: {input, output, calls}} 按日汇总"""
    by_day: dict = {}
    for jf in sorted(CODEX_BASE.rglob("*.jsonl")):
        try:
            _parse_codex_file(jf, since, by_day)
        except Exception:
            pass
    return by_day


def _parse_codex_file(jf: pathlib.Path, since: datetime.datetime, by_day: dict):
    session_last: dict[str, dict] = {}  # turn_id → last token_count
    with open(jf, errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") != "event_msg":
                continue
            payload = rec.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            ts = datetime.datetime.fromisoformat(
                rec["timestamp"].replace("Z", "+00:00")
            )
            if ts < since:
                continue
            info = payload.get("info") or {}
            last_usage = info.get("last_token_usage") or {}
            day = ts.astimezone(TZ_LOCAL).date()
            by_day.setdefault(day, {"input": 0, "output": 0, "calls": 0})
            by_day[day]["input"] += last_usage.get("input_tokens", 0)
            by_day[day]["output"] += last_usage.get("output_tokens", 0)
            by_day[day]["calls"] += 1


# ── 渲染 ─────────────────────────────────────────────────────────────────────

SEP = "─" * 52


def render_claude(totals: dict, since: datetime.datetime, days_count: int,
                  web_data: dict = None, web_error: str = None, detail: bool = False):
    title = "Claude Code"
    print(f"\n{_DIM}{SEP}{_RST}")
    print(f"{_BOLD}{title.center(52)}{_RST}")
    print()
    since_local = since.astimezone(TZ_LOCAL)
    print(f"  {_DIM}{t('统计自', 'Since')}: {fmt_dt(since_local)}  ({t(f'近 {days_count} 天', f'last {days_count} days')}){_RST}")

    if not totals:
        print(t("  （该时间段无记录）", "  (no records in this period)"))
        return

    active = {m: d for m, d in totals.items() if m != "<synthetic>"}
    grand_out = sum(d["output"] for d in active.values())
    grand_in_net = sum(d["input"] + d["cache_create"] for d in active.values())
    show_ratio = len(active) > 1 and grand_out > 0

    if detail:
        for model in sorted(active.keys()):
            d = active[model]
            total_in = d["input"] + d["cache_create"] + d["cache_read"]
            cache_pct = d["cache_read"] / total_in * 100 if total_in else 0
            if show_ratio:
                pct = d["output"] / grand_out * 100
                pct_s = '<1%' if pct < 1 else f'{pct:.0f}%'
                ratio_str = t(f"  (占总输出 {pct_s})", f"  ({pct_s} of total output)")
            else:
                ratio_str = ""
            print(f"  {model}")
            print(f"    {t('调用次数', 'Calls')}: {d['calls']:,}")
            print(f"    {t('输入合计', 'Input')}: {fmt_tokens(total_in):>8}  ({t(f'缓存命中 {cache_pct:.0f}%', f'cache hit {cache_pct:.0f}%')})")
            print(f"    {t('输出合计', 'Output')}: {fmt_tokens(d['output']):>8}{ratio_str}")
            actual_days = len(d["days"])
            if actual_days > 0:
                rate = d["output"] / actual_days
                print(f"    {t('日均输出', 'Daily avg')}: {fmt_tokens(int(rate)):>8}  ({t(f'共 {actual_days} 天有记录', f'{actual_days} days recorded')})")
            print()

    print(f"  {t('总输出', 'Total output')}: {_BOLD}{fmt_tokens(grand_out)}{_RST}  |  {t('净输入(非缓存)', 'Net input (non-cache)')}: {_BOLD}{fmt_tokens(grand_in_net)}{_RST}")
    if show_ratio:
        print(f"\n  {_BOLD}{t('输出占比', 'Output share')}{_RST}")
        name_w = max(len(m.replace("claude-", "")) for m in active)
        for m in sorted(active.keys(), key=lambda x: active[x]["output"], reverse=True):
            pct = active[m]["output"] / grand_out * 100
            pct_str = "<1%" if pct < 1 else f"{pct:.0f}%"
            short = m.replace("claude-", "")
            print(f"  {short:<{name_w}}  {_bold_bar(pct)}  {pct_str}")
    if web_data is not None:
        five_h = web_data.get("five_hour") or {}
        seven_d = web_data.get("seven_day") or {}
        if five_h or seven_d:
            print(f"\n  {_BOLD}{t('实时额度', 'Live quota')}{_RST}  {_DIM}{t('(与 --days 统计范围无关)', '(independent of --days range)')}{_RST}")
            print(f"  {_DIM}{t('数据来源', 'Source')}: claude.ai usage API  ({t('浏览器登录态', 'browser session')}){_RST}")
            print()
            for win_key, label, win in [
                ("5h", t("5小时滚动窗", "5-hour window"), five_h),
                ("7d", t("7天滚动窗  ", "7-day window "), seven_d),
            ]:
                if not win:
                    continue
                used = float(win.get("utilization", 0))
                remaining = remaining_percent(used)
                r_str = f"{_bc(remaining)}{_BOLD}{remaining:.0f}%{_RST}"
                print(f"  {label}  {_colored_bar(remaining)}  {t(f'剩余 {r_str}  {_DIM}(已用 {used:.0f}%){_RST}', f'left {r_str}  {_DIM}(used {used:.0f}%){_RST}')}")
                resets_at = win.get("resets_at")
                reset_dt = None
                if resets_at:
                    try:
                        reset_dt = datetime.datetime.fromisoformat(resets_at).astimezone(TZ_LOCAL)
                        print(f"  {_DIM}{t('重置时间', 'Resets at')}: {fmt_reset_dt(reset_dt)}{_RST}")
                    except Exception:
                        pass
                printed_estimate = False
                if win_key == "7d" and used and reset_dt:
                    window_min = 7 * 24 * 60
                    elapsed = (datetime.timedelta(minutes=window_min)
                               - (reset_dt - datetime.datetime.now(TZ_LOCAL)))
                    if elapsed.total_seconds() > 0:
                        rate = used / (elapsed.total_seconds() / 3600)
                        if rate > 0:
                            hours_left = remaining / rate
                            print(f"\n  📊 {_DIM}{t(f'按当前速率 ({rate:.1f}%/小时)，剩余 {remaining:.0f}% 约可用', f'At current rate ({rate:.1f}%/hr), {remaining:.0f}% left ≈')}{_RST} {_BOLD}{hours_left:.0f} {t('小时', 'hrs')}{_RST}")
                            printed_estimate = True
                if not printed_estimate:
                    print()
        else:
            print(f"\n  {t('claude.ai usage 原始响应', 'claude.ai usage raw response')}: {json.dumps(web_data, ensure_ascii=False)[:400]}")
            print(f"  →  {CLAUDE_USAGE_URL}  ({t('Cmd+双击打开', 'Cmd+double-click to open')})")
    elif web_error:
        print(f"\n  {t('实时额度  (与 --days 统计范围无关)', 'Live quota  (independent of --days range)')}")
        print(f"  ⚠️  {t('读取失败', 'Failed to fetch')}: {web_error}")
        print(f"  →  {CLAUDE_USAGE_URL}  ({t('Cmd+双击打开', 'Cmd+double-click to open')})")
    else:
        print(f"\n  ⚠️  {t('Claude 周额度百分比本地不可得', 'Claude quota unavailable locally')}  →  {CLAUDE_USAGE_URL}  ({t('Cmd+双击打开', 'Cmd+double-click to open')})")


def _rolling_window_label(window_minutes):
    """按窗口实际分钟数生成"N 小时滚动窗"/"N 天滚动窗"文案。

    不能假设 Codex 固定是 5h+7d 两档：2026-07-13 起 OpenAI 后端把 5 小时窗口
    并入了周窗口，primary_window 的 limit_window_seconds 变成了 604800（7 天），
    但字段位置仍叫 primary——必须按实际时长算标签，不能按字段名硬编码。
    """
    if not window_minutes:
        return t("额度窗口  ", "quota window ")
    hours = window_minutes / 60
    if hours < 24:
        n = round(hours) or 1
        return t(f"{n}小时滚动窗", f"{n}-hour window")
    days = round(hours / 24)
    return t(f"{days}天滚动窗  ", f"{days}-day window ")


def _classify_codex_windows(rl):
    """把 primary/secondary 两个 JSON 字段位置的窗口，按实际时长分成
    "短窗口档"（约 5 小时一档）和"长窗口档"（约 7 天/更长一档），返回 (short, long)。

    不能假设字段位置固定对应哪个时长：2026-07-13 OpenAI 临时移除 Codex 的 5 小时
    限额后，唯一剩下的窗口出现在 primary_window 字段，但实际时长是 7 天——必须按
    window_minutes 分类，不能按 JSON 字段名。官方说明是"temporarily"，5 小时档
    随时可能恢复，所以两档都要固定展示（缺数据显示 "?"），不能因为这次没数据就把
    整行隐藏掉，否则每次 OpenAI 开关这个限额，菜单栏布局就跟着忽隐忽现。
    """
    short = long = None
    for w in (rl.get("primary"), rl.get("secondary")):
        if not w:
            continue
        mins = w.get("window_minutes")
        if not mins:
            continue
        if mins <= 360:   # ≤6 小时归"短窗口"档
            short = w
        else:
            long = w
    return short, long


def _print_codex_window_row(label, window, *, source, data_age_min, now_local, missing_note):
    """打印一档 Codex 额度窗口；window=None 时打印占位行（标签 + "?"）而不是
    直接跳过——见 _classify_codex_windows 的 docstring，缺数据可能只是临时的。"""
    if window is None:
        print(f"  {label}  {_colored_bar(0)}  {t(f'剩余 ?  {_DIM}({missing_note}){_RST}', f'left ?  {_DIM}({missing_note}){_RST}')}")
        return False
    pct = window.get("used_percent", 0)
    remaining = remaining_percent(pct)
    reset = epoch_to_local(window["resets_at"]) if window.get("resets_at") else None
    win_min = window.get("window_minutes") or 300
    stale = source == "snapshot" and data_age_min > win_min
    if stale:
        if reset and now_local >= reset:
            full_str = f"{_OK}{_BOLD}100%{_RST}"
            print(f"  {label}  {_colored_bar(100)}  {t(f'剩余 {full_str}  {_DIM}(推断：CLI 无新记录，可能漏检 Cloud){_RST}', f'left {full_str}  {_DIM}(inferred: no new CLI usage; Cloud may be missed){_RST}')}")
            print(f"  {_DIM}{t('重置时间', 'Reset at')}: {fmt_reset_dt(reset)}{_RST}")
        elif reset:
            print(f"  {label}  {_DIM}{t(f'快照已过期，预计 {fmt_reset_dt(reset)} 后恢复', f'snapshot expired, expected reset at {fmt_reset_dt(reset)}')}{_RST}")
        else:
            age_h = data_age_min / 60
            print(f"  {label}  {_DIM}{t(f'快照已过期 ({age_h:.0f}h 前)', f'snapshot expired ({age_h:.0f}h ago)')}{_RST}  →  {CODEX_USAGE_URL}")
    else:
        r_str = f"{_bc(remaining)}{_BOLD}{remaining:.0f}%{_RST}"
        print(f"  {label}  {_colored_bar(remaining)}  {t(f'剩余 {r_str}  {_DIM}(已用 {pct:.0f}%){_RST}', f'left {r_str}  {_DIM}(used {pct:.0f}%){_RST}')}")
        if reset:
            print(f"  {_DIM}{t('重置时间', 'Resets at')}: {fmt_reset_dt(reset)}{_RST}")
    return stale


def render_codex(since: datetime.datetime):
    title = "CodeX (OpenAI GPT-5)"
    print(f"\n{_DIM}{SEP}{_RST}")
    print(f"{_BOLD}{title.center(52)}{_RST}")
    print()

    ts, rl, source, fallback_reason = current_codex_rate_limits()
    if not rl:
        if source == "no_access":
            print(f"  {_WARN}{t('未检测到 Codex 权限', 'No Codex access detected')}{_RST}")
            print(f"  {_DIM}{fallback_reason}{_RST}")
        else:
            if fallback_reason:
                print(f"  {t('实时读取失败', 'Live fetch failed')}: {fallback_reason}")
            print(t("  （未找到 CodeX 数据）", "  (no CodeX data found)"))
        return

    now_local = datetime.datetime.now(TZ_LOCAL)
    ts_local = ts.astimezone(TZ_LOCAL)

    source_labels = {
        "live": t("实时", "live"),
        "web": t("实时(网页)", "live (web)"),
        "snapshot": t("本地快照", "snapshot"),
    }
    source_details = {
        "live": "codex app-server WebSocket",
        "web": t("chatgpt.com usage API  (浏览器登录态)", "chatgpt.com usage API  (browser session)"),
        "snapshot": t("本地快照", "local snapshot") + " (~/.codex/sessions/)",
    }
    print(f"  {_DIM}{t('数据时间', 'Data time')}: {fmt_dt(ts_local)}  ({source_labels[source]}){_RST}")
    print(f"  {_DIM}{t('数据来源', 'Source')}: {source_details[source]}{_RST}")
    if fallback_reason and source == "snapshot":
        print(f"  {t('实时读取失败', 'Live fetch failed')}: {fallback_reason}")
    plan = rl.get("plan_type") or "?"
    print(f"  {t('套餐', 'Plan')}: {_BOLD}{fmt_plan(plan)}{_RST}")
    print()

    data_age_min = (now_local - ts_local).total_seconds() / 60

    # 按实际 window_minutes 把窗口分到"短档"（约 5 小时）/"长档"（约 7 天）；
    # 两档都固定打印，缺数据显示 "?"（见 _classify_codex_windows 的 why）。
    short_win, long_win = _classify_codex_windows(rl)

    short_label = _rolling_window_label(short_win["window_minutes"]) if short_win else t("5小时滚动窗", "5-hour window")
    _print_codex_window_row(
        short_label, short_win,
        source=source, data_age_min=data_age_min, now_local=now_local,
        missing_note=t("OpenAI 当前临时移除该档限额，预计后续恢复", "OpenAI has temporarily removed this tier, expected to return"),
    )
    print()

    long_label = _rolling_window_label(long_win["window_minutes"]) if long_win else t("7天滚动窗  ", "7-day window ")
    _print_codex_window_row(
        long_label, long_win,
        source=source, data_age_min=data_age_min, now_local=now_local,
        missing_note=t("本次未返回该档数据", "not returned this time"),
    )
    w_pct = long_win.get("used_percent", 0) if long_win else None
    w_reset = epoch_to_local(long_win["resets_at"]) if long_win and long_win.get("resets_at") else None
    w_min = (long_win.get("window_minutes") if long_win else None) or 10080

    # remaining quota estimate
    if long_win is not None and w_pct and w_reset:
        remaining_pct = 100 - w_pct
        elapsed_since_reset = (
            datetime.timedelta(minutes=w_min)
            - (w_reset - datetime.datetime.now(TZ_LOCAL))
        )
        if elapsed_since_reset.total_seconds() > 0:
            rate_per_hour = w_pct / (elapsed_since_reset.total_seconds() / 3600)
            if rate_per_hour > 0:
                hours_left = remaining_pct / rate_per_hour
                print(f"\n  📊 {_DIM}{t(f'按当前速率 ({rate_per_hour:.1f}%/小时)，剩余 {remaining_pct:.0f}% 约可用', f'At current rate ({rate_per_hour:.1f}%/hr), {remaining_pct:.0f}% left ≈')}{_RST} {_BOLD}{hours_left:.0f} {t('小时', 'hrs')}{_RST}")


def render_summary():
    print(f"\n{_DIM}{SEP}{_RST}\n")


def _fmt_history_service(data: dict | None) -> str:
    if not data:
        return "-"
    if "error" in data:
        return f"⚠️ {data.get('error')}"
    h5 = data.get("5h_left")
    d7 = data.get("7d_left")
    h5_reset = data.get("5h_reset")
    d7_reset = data.get("7d_reset")
    parts = []
    if h5 is not None:
        reset = f" ↻ {_fmt_history_reset(h5_reset)}" if h5_reset else ""
        parts.append(f"5h {h5}%{reset}")
    if d7 is not None:
        reset = f" ↻ {_fmt_history_reset(d7_reset)}" if d7_reset else ""
        parts.append(f"7d {d7}%{reset}")
    return " | ".join(parts) if parts else "-"


def _fmt_history_reset(value) -> str:
    if value is None:
        return "?"
    try:
        if isinstance(value, (int, float)):
            dt = epoch_to_local(int(value))
        elif isinstance(value, str) and value.isdigit():
            dt = epoch_to_local(int(value))
        else:
            dt = datetime.datetime.fromisoformat(str(value)).astimezone(TZ_LOCAL)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return "?"


def render_menubar_history(minutes: int = 120):
    cutoff = datetime.datetime.now(TZ_LOCAL).timestamp() - minutes * 60
    rows = []
    try:
        for line in _MENUBAR_HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if float(rec.get("epoch", 0)) >= cutoff:
                rows.append(rec)
    except FileNotFoundError:
        print(t(
            f"未找到菜单栏历史：{_MENUBAR_HISTORY_PATH}",
            f"Menubar history not found: {_MENUBAR_HISTORY_PATH}",
        ))
        return

    if not rows:
        print(t("最近没有菜单栏历史采样。", "No recent menubar history samples."))
        return

    print(f"\n{_BOLD}{t('菜单栏历史采样', 'Menubar history')}{_RST}")
    print(f"{_DIM}{_MENUBAR_HISTORY_PATH}{_RST}")
    print()
    for rec in rows:
        ts = rec.get("ts", "?")
        try:
            ts = datetime.datetime.fromisoformat(str(ts)).astimezone(TZ_LOCAL).strftime("%m-%d %H:%M:%S")
        except Exception:
            pass
        claude = _fmt_history_service(rec.get("claude"))
        codex = _fmt_history_service(rec.get("codex"))
        print(f"{ts}  Claude: {claude}  |  CodeX: {codex}")
    print()


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=t("查看 Claude / CodeX 本周消耗", "Show Claude / CodeX token usage and quota"),
    )
    parser.add_argument("--days", type=int, default=7,
                        help=t("统计最近 N 天（默认 7）", "show last N days (default: 7)"))
    parser.add_argument("--all", action="store_true",
                        help=t("统计全部历史（忽略 --days）", "show all history (overrides --days)"))
    parser.add_argument("--detail", action="store_true",
                        help=t("展示每个模型的详细 token 统计", "show per-model token breakdown"))
    parser.add_argument("--history", action="store_true",
                        help=t("展示菜单栏最近 2 小时额度采样", "show menubar quota samples from the last 2 hours"))
    args = parser.parse_args()

    if args.history:
        render_menubar_history()
        return

    now_utc = datetime.datetime.now(datetime.timezone.utc)

    if args.all:
        since = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        days_count = (now_utc - since).days
    else:
        since = now_utc - datetime.timedelta(days=args.days)
        days_count = args.days

    now_local = datetime.datetime.now(TZ_LOCAL)
    _wd_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    _wd_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    wd_now = _wd_zh[now_local.weekday()] if LANG == "zh" else _wd_en[now_local.weekday()]
    print(f"\n{_DIM}{t('查询时间', 'Queried at')}: {wd_now} {now_local.strftime('%m-%d %H:%M')} {TZ_ABBR}{_RST}")

    claude_totals = collect_claude(since)

    web_data, web_error = None, None
    try:
        web_data = live_claude_usage()
    except ClaudeWebError as e:
        web_error = str(e)

    render_claude(claude_totals, since, days_count, web_data=web_data, web_error=web_error, detail=args.detail)
    render_codex(since)
    render_summary()


if __name__ == "__main__":
    main()
