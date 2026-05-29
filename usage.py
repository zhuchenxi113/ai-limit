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
TZ_LOCAL = datetime.datetime.now().astimezone().tzinfo
TZ_ABBR  = datetime.datetime.now().astimezone().strftime('%Z')

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

def _colored_bar(remaining: float, width: int = 20) -> str:
    filled = round(remaining / 100 * width)
    return f"{_bc(remaining)}{'█'*filled}{_DIM}{'░'*(width-filled)}{_RST}"

def _bold_bar(pct: float, width: int = 20) -> str:
    filled = round(pct / 100 * width)
    return f"{_BOLD}{'█'*filled}{_RST}{_DIM}{'░'*(width-filled)}{_RST}"


REMOTE_TIMEOUT_SEC = 15
CLAUDE_WEB_TIMEOUT_SEC = 10


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


def fmt_dt(dt: datetime.datetime) -> str:
    return f"{dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def fmt_reset_dt(dt: datetime.datetime) -> str:
    _bare_zh = ["一", "二", "三", "四", "五", "六", "日"]
    _bare_en = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    today = datetime.datetime.now(TZ_LOCAL).date()
    next_week = dt.date().isocalendar()[:2] > today.isocalendar()[:2]
    if LANG == "zh":
        wd = f"下周{_bare_zh[dt.weekday()]}" if next_week else f"周{_bare_zh[dt.weekday()]}  "
    else:
        wd = f"next {_bare_en[dt.weekday()]}" if next_week else f"{_bare_en[dt.weekday()]:<8}"
    return f"{wd} {dt.strftime('%m-%d %H:%M')} {TZ_ABBR}"


def find_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Claude Web 额度 (--claude-web) ────────────────────────────────────────────

class ClaudeWebError(Exception):
    pass


def live_claude_usage(timeout: int = CLAUDE_WEB_TIMEOUT_SEC) -> dict:
    """
    通过浏览器 session cookie 调用 claude.ai/api/organizations/{org}/usage。
    返回形如 {"five_hour": {...}, "seven_day": {...}} 的 dict。
    """
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

    import urllib.request
    import urllib.error

    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": cookie_header,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://claude.ai",
            "Referer": "https://claude.ai/settings/usage",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise ClaudeWebError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
    except Exception as e:
        raise ClaudeWebError(str(e))

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ClaudeWebError(f"非 JSON 响应: {body[:300].decode(errors='replace')}")


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


_CHATGPT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _chatgpt_headers(cookie_header: str, *, referer: str = "https://chatgpt.com/codex/cloud/settings/analytics", bearer: str = None) -> dict:
    # 包含 Sec-Fetch-* / Accept-Language / Referer，避免 Cloudflare 反爬 403
    h = {
        "Cookie": cookie_header,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _CHATGPT_UA,
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


def current_codex_rate_limits(offline: bool = False):
    """返回 (timestamp, rate_limits_dict, source_label, fallback_reason)

    数据源优先级：
      1. chatgpt.com web 接口（只读，不触发窗口；覆盖 Cloud + CLI 合并用量）
      2. app-server live（注意：会触发新的 Codex 5 小时冷却窗口，见 README）
      3. 本地快照（~/.codex/sessions/）
    """
    if offline:
        ts, rl = latest_codex_rate_limits()
        return ts, rl, "snapshot", "--offline"

    reasons = []

    # 1. web 优先：不触发窗口，安全
    try:
        ts, rl = live_codex_web_usage()
        resets_at = (rl.get("primary") or {}).get("resets_at")
        if resets_at:
            _save_window_cache(float(resets_at))
        return ts, rl, "web", None
    except CodexWebError as e:
        reasons.append(f"web: {e}")
    except Exception as e:
        reasons.append(f"web: {e.__class__.__name__}: {e}")

    # 2. app-server 兜底（会触发 5h 窗口，详见 README 副作用说明）
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

    # 3. 本地快照
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


def render_codex(since: datetime.datetime, offline: bool = False):
    title = "CodeX (OpenAI GPT-5)"
    print(f"\n{_DIM}{SEP}{_RST}")
    print(f"{_BOLD}{title.center(52)}{_RST}")
    print()

    ts, rl, source, fallback_reason = current_codex_rate_limits(offline=offline)
    if not rl:
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
    print(f"  {t('套餐', 'Plan')}: {_BOLD}{plan.upper()}{_RST}")
    print()

    secondary = rl.get("secondary") or {}
    primary = rl.get("primary") or {}

    data_age_min = (now_local - ts_local).total_seconds() / 60

    # 5-hour window
    p_pct = primary.get("used_percent", 0)
    p_remaining = remaining_percent(p_pct)
    p_reset = epoch_to_local(primary["resets_at"]) if primary.get("resets_at") else None
    p_min = primary.get("window_minutes", 300)
    p_stale = source == "snapshot" and data_age_min > p_min
    p_label = t("5小时滚动窗", "5-hour window")

    if p_stale:
        if p_reset and now_local >= p_reset:
            # 快照过期且窗口重置时间已过；web/live 都失败 → 保守推断已重置
            full_str = f"{_OK}{_BOLD}100%{_RST}"
            print(f"  {p_label}  {_colored_bar(100)}  {t(f'剩余 {full_str}  {_DIM}(推断：CLI 无新记录，可能漏检 Cloud){_RST}', f'left {full_str}  {_DIM}(inferred: no new CLI usage; Cloud may be missed){_RST}')}")
            print(f"  {_DIM}{t('重置时间', 'Reset at')}: {fmt_reset_dt(p_reset)}{_RST}")
        elif p_reset:
            print(f"  {p_label}  {_DIM}{t(f'快照已过期，预计 {fmt_reset_dt(p_reset)} 后恢复', f'snapshot expired, expected reset at {fmt_reset_dt(p_reset)}')}{_RST}")
        else:
            age_h = data_age_min / 60
            print(f"  {p_label}  {_DIM}{t(f'快照已过期 ({age_h:.0f}h 前)', f'snapshot expired ({age_h:.0f}h ago)')}{_RST}  →  {CODEX_USAGE_URL}")
    else:
        p_r_str = f"{_bc(p_remaining)}{_BOLD}{p_remaining:.0f}%{_RST}"
        print(f"  {p_label}  {_colored_bar(p_remaining)}  {t(f'剩余 {p_r_str}  {_DIM}(已用 {p_pct:.0f}%){_RST}', f'left {p_r_str}  {_DIM}(used {p_pct:.0f}%){_RST}')}")
        if p_reset:
            print(f"  {_DIM}{t('重置时间', 'Resets at')}: {fmt_reset_dt(p_reset)}{_RST}")
    print()

    # 7-day window
    w_pct = secondary.get("used_percent", 0)
    w_remaining = remaining_percent(w_pct)
    w_reset = epoch_to_local(secondary["resets_at"]) if secondary.get("resets_at") else None
    w_min = secondary.get("window_minutes", 10080)
    if w_min:
        days = w_min // 60 // 24
        w_label = t(f"{days}天滚动窗  ", f"{days}-day window ")
    else:
        w_label = t("周额度    ", "Weekly quota")
    w_stale = bool(source == "snapshot" and w_min and data_age_min > w_min)
    w_r_str = f"{_bc(w_remaining)}{_BOLD}{w_remaining:.0f}%{_RST}"
    if w_stale and w_reset and now_local >= w_reset:
        full_str = f"{_OK}{_BOLD}100%{_RST}"
        print(f"  {w_label}  {_colored_bar(100)}  {t(f'剩余 {full_str}  {_DIM}(推断：已重置){_RST}', f'left {full_str}  {_DIM}(inferred: reset){_RST}')}")
        print(f"  {_DIM}{t('重置时间', 'Reset at')}: {fmt_reset_dt(w_reset)}{_RST}")
    else:
        age_note = f"  {_DIM}{t(f'{data_age_min/60:.0f}h 前快照', f'snapshot {data_age_min/60:.0f}h ago')}{_RST}" if p_stale else ""
        print(f"  {w_label}  {_colored_bar(w_remaining)}  {t(f'剩余 {w_r_str}  {_DIM}(已用 {w_pct:.0f}%){_RST}', f'left {w_r_str}  {_DIM}(used {w_pct:.0f}%){_RST}')}{age_note}")
        if w_reset:
            print(f"  {_DIM}{t('重置时间', 'Resets at')}: {fmt_reset_dt(w_reset)}{_RST}")

    # remaining quota estimate
    if w_pct and w_reset:
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


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description=t("查看 Claude / CodeX 本周消耗", "Show Claude / CodeX token usage and quota"),
    )
    parser.add_argument("--days", type=int, default=7,
                        help=t("统计最近 N 天（默认 7）", "show last N days (default: 7)"))
    parser.add_argument("--all", action="store_true",
                        help=t("统计全部历史（忽略 --days）", "show all history (overrides --days)"))
    parser.add_argument("--offline", action="store_true",
                        help=t("不调用 Codex app-server，只读取本地快照", "skip Codex app-server, use local snapshot only"))
    parser.add_argument("--detail", action="store_true",
                        help=t("展示每个模型的详细 token 统计", "show per-model token breakdown"))
    args = parser.parse_args()

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
    render_codex(since, offline=args.offline)
    render_summary()


if __name__ == "__main__":
    main()
