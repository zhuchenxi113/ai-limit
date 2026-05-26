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
TZ_LOCAL = datetime.timezone(datetime.timedelta(hours=8))  # CST
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


def current_codex_rate_limits(offline: bool = False):
    """返回 (timestamp, rate_limits_dict, source_label, fallback_reason)"""
    if not offline:
        try:
            ts, rl = live_codex_rate_limits()
            return ts, rl, "live", None
        except (CodexRemoteError, OSError, subprocess.SubprocessError) as e:
            fallback_reason = str(e) or e.__class__.__name__
        except Exception as e:
            fallback_reason = f"{e.__class__.__name__}: {e}"
    else:
        fallback_reason = "--offline"

    ts, rl = latest_codex_rate_limits()
    return ts, rl, "snapshot", fallback_reason


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
    print(f"\n{SEP}")
    print(f"{title.center(52)}")
    print()
    since_local = since.astimezone(TZ_LOCAL)
    print(f"  {t('统计范围', 'Stats from')}: {since_local.strftime('%m-%d %H:%M')} CST  ({t(f'{days_count} 天内', f'last {days_count} days')})")

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

    print(f"  {t('总输出', 'Total output')}: {fmt_tokens(grand_out)}  |  {t('净输入(非缓存)', 'Net input (non-cache)')}: {fmt_tokens(grand_in_net)}")
    if show_ratio:
        print(f"\n  {t('输出占比', 'Output share')}")
        name_w = max(len(m.replace("claude-", "")) for m in active)
        for m in sorted(active.keys(), key=lambda x: active[x]["output"], reverse=True):
            pct = active[m]["output"] / grand_out * 100
            pct_str = "<1%" if pct < 1 else f"{pct:.0f}%"
            short = m.replace("claude-", "")
            print(f"  {short:<{name_w}}  {bar(pct)}  {pct_str}")
    if web_data is not None:
        five_h = web_data.get("five_hour") or {}
        seven_d = web_data.get("seven_day") or {}
        if five_h or seven_d:
            print(f"\n  {t('实时额度  (与 --days 统计范围无关)', 'Live quota  (independent of --days range)')}")
            print(f"  {t('数据来源', 'Source')}: claude.ai usage API  ({t('浏览器登录态', 'browser session')})")
            print()
            for win_key, label, win in [
                ("5h", t("5小时滚动窗", "5-hour window"), five_h),
                ("7d", t("7天滚动窗  ", "7-day window "), seven_d),
            ]:
                if not win:
                    continue
                used = float(win.get("utilization", 0))
                remaining = remaining_percent(used)
                print(f"  {label}  {bar(remaining)}  {t(f'剩余 {remaining:.0f}%  (已用 {used:.0f}%)', f'left {remaining:.0f}%  (used {used:.0f}%)')}")
                resets_at = win.get("resets_at")
                reset_dt = None
                if resets_at:
                    try:
                        reset_dt = datetime.datetime.fromisoformat(resets_at).astimezone(TZ_LOCAL)
                        print(f"  {t('重置时间', 'Resets at')}: {reset_dt.strftime('%m-%d %H:%M')} CST")
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
                            print(f"\n  📊 {t(f'按当前速率 ({rate:.1f}%/小时)，剩余 {remaining:.0f}% 约可用 {hours_left:.0f} 小时', f'At current rate ({rate:.1f}%/hr), {remaining:.0f}% left ≈ {hours_left:.0f} hrs')}")
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
    print(f"\n{SEP}")
    print(f"{title.center(52)}")
    print()

    ts, rl, source, fallback_reason = current_codex_rate_limits(offline=offline)
    if not rl:
        print(t("  （未找到 CodeX 会话数据）", "  (no CodeX session data found)"))
        return

    ts_local = ts.astimezone(TZ_LOCAL)
    source_label = t("实时", "live") if source == "live" else t("本地快照", "snapshot")
    print(f"  {t('数据时间', 'Data time')}: {ts_local.strftime('%m-%d %H:%M')} CST  ({source_label})")
    if source == "live":
        print(f"  {t('数据来源', 'Source')}: codex app-server WebSocket")
    else:
        print(f"  {t('数据来源', 'Source')}: {t('本地快照', 'local snapshot')} (~/.codex/sessions/)")
    if fallback_reason and source == "snapshot":
        print(f"  {t('实时读取失败', 'Live fetch failed')}: {fallback_reason}")
    print(f"  {t('套餐', 'Plan')}: {rl.get('plan_type', '?').upper()}")
    print()

    secondary = rl.get("secondary") or {}
    primary = rl.get("primary") or {}

    # 5-hour window
    p_pct = primary.get("used_percent", 0)
    p_remaining = remaining_percent(p_pct)
    p_reset = epoch_to_local(primary["resets_at"]) if primary.get("resets_at") else None
    p_min = primary.get("window_minutes", 300)
    now_local = datetime.datetime.now(TZ_LOCAL)
    data_age_min = (now_local - ts_local).total_seconds() / 60
    p_stale = data_age_min > p_min
    age_h = data_age_min / 60
    if p_stale:
        stale_note = f"  ⚠️ {t(f'{age_h:.0f}h 前的数据', f'data {age_h:.0f}h old')}  →  {CODEX_USAGE_URL}  ({t('Cmd+双击打开', 'Cmd+double-click to open')})"
    else:
        stale_note = ""
    p_label = t("5小时滚动窗", "5-hour window")
    print(f"  {p_label}  {bar(p_remaining)}  {t(f'剩余 {p_remaining:.0f}%  (已用 {p_pct:.0f}%)', f'left {p_remaining:.0f}%  (used {p_pct:.0f}%)')}{stale_note}")
    if p_reset and not p_stale:
        print(f"  {t('重置时间', 'Resets at')}: {p_reset.strftime('%m-%d %H:%M')} CST")
    print()

    # 7-day window
    w_pct = secondary.get("used_percent", 0)
    w_remaining = remaining_percent(w_pct)
    w_reset = epoch_to_local(secondary["resets_at"]) if secondary.get("resets_at") else None
    w_min = secondary.get("window_minutes", 10080)
    if w_min:
        days = w_min // 60 // 24
        w_label = t(f"{days}天滚动窗  ", f"{days}-day window")
    else:
        w_label = t("周额度    ", "Weekly quota")
    print(f"  {w_label}  {bar(w_remaining)}  {t(f'剩余 {w_remaining:.0f}%  (已用 {w_pct:.0f}%)', f'left {w_remaining:.0f}%  (used {w_pct:.0f}%)')}")
    if w_reset:
        print(f"  {t('重置时间', 'Resets at')}: {w_reset.strftime('%m-%d %H:%M')} CST")

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
                print(f"\n  📊 {t(f'按当前速率 ({rate_per_hour:.1f}%/小时)，剩余 {remaining_pct:.0f}% 约可用 {hours_left:.0f} 小时', f'At current rate ({rate_per_hour:.1f}%/hr), {remaining_pct:.0f}% left ≈ {hours_left:.0f} hrs')}")


def render_summary():
    print(f"\n{SEP}\n")


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
