"""数据抓取与格式化。从 mac 版 ai-limit-app.py 复制的纯逻辑函数
（不碰 rumps/AppKit，逻辑对两个平台完全一致）。修 bug 时两边要同步改。
"""
import datetime
import socket
import urllib.error

from usage import (
    live_claude_plan,
    live_claude_usage,
    live_codex_web_usage,
    latest_codex_rate_limits,
    _classify_codex_windows,
    ClaudeWebError,
    CodexWebError,
    CodexAuthError,
    TZ_LOCAL,
    epoch_to_local,
    fetch_status_components,
)

from lang_win import tr as _tr

_ZH_WEEKDAYS = "一二三四五六日"
_EN_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _fmt_plan(plan, lang="zh"):
    if not plan or plan == "?":
        return ""
    plan = str(plan).replace("_", " ").title()
    return f" Plan: {plan}" if lang == "en" else f" 方案：{plan}"


def _fmt_reset_dt(dt, lang):
    today = datetime.datetime.now(TZ_LOCAL).date()
    target = dt.date()
    days = (target - today).days
    next_week = target.isocalendar()[:2] > today.isocalendar()[:2]
    if lang == "en":
        if days == 0:    wd = "today"
        elif days == 1:  wd = "tomorrow"
        elif days == 2:  wd = "2 days"
        elif next_week:  wd = f"next {_EN_WEEKDAYS[dt.weekday()]}"
        else:            wd = _EN_WEEKDAYS[dt.weekday()]
        return f"{dt:%H:%M}  {wd}"
    if days == 0:    wd = "今天"
    elif days == 1:  wd = "明天"
    elif days == 2:  wd = "后天"
    elif next_week:  wd = f"下周{_ZH_WEEKDAYS[dt.weekday()]}"
    else:            wd = f"周{_ZH_WEEKDAYS[dt.weekday()]}"
    if len(wd) < 3:
        wd += "　" * (3 - len(wd))
    return f"{wd} {dt:%H:%M}"


def fmt_reset_epoch(epoch, lang="zh"):
    try:
        return _fmt_reset_dt(epoch_to_local(int(epoch)), lang)
    except Exception:
        return "?"


def fmt_reset_iso(iso, lang="zh"):
    try:
        return _fmt_reset_dt(datetime.datetime.fromisoformat(iso).astimezone(TZ_LOCAL), lang)
    except Exception:
        return "?"


def window_shorthand(window_minutes):
    if not window_minutes:
        return None
    hours = window_minutes / 60
    if hours < 24:
        return f"{round(hours) or 1}h"
    return f"{round(hours / 24)}d"


def fetch_claude(lang):
    try:
        data = live_claude_usage()
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        try:
            plan = live_claude_plan()
        except Exception:
            plan = None
        return {
            "5h_left":  int(round(100 - float(five_h.get("utilization", 0)))),
            "7d_left":  int(round(100 - float(seven_d.get("utilization", 0)))),
            "5h_reset": five_h.get("resets_at"),
            "7d_reset": seven_d.get("resets_at"),
            "plan":     plan,
        }
    except ClaudeWebError as e:
        kind = getattr(e, "kind", "generic")
        raw_msg = str(e)
        if kind == "cloudflare":
            msg = _tr(lang, "被拦截，打开用量页勿关", "Blocked, open Claude usage, keep open")
        elif kind == "auth":
            msg = _tr(lang, "需在浏览器重新登录 claude.ai", "Re-login at claude.ai in browser")
        elif "cannot read browser cookies" in raw_msg or "App-Bound Encryption" in raw_msg:
            msg = _tr(lang,
                "未读取到登录信息，请用 Firefox 登录 claude.ai 后重试",
                "No browser login found; sign in to claude.ai with Firefox and retry")
        else:
            msg = raw_msg
            if "JSON" in msg or "DOCTYPE" in msg or "html" in msg.lower():
                msg = _tr(lang, "网络不可用或需重新登录 claude.ai", "Network error or re-login at claude.ai required")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fetch_codex(lang):
    try:
        _ts, rl = live_codex_web_usage()
        short_win, long_win = _classify_codex_windows(rl)
        return {
            "5h_left":  int(round(100 - short_win.get("used_percent", 0))) if short_win else None,
            "7d_left":  int(round(100 - long_win.get("used_percent", 0))) if long_win else None,
            "5h_reset": short_win.get("resets_at") if short_win else None,
            "7d_reset": long_win.get("resets_at") if long_win else None,
            "5h_label": window_shorthand(short_win.get("window_minutes")) if short_win else "5h",
            "7d_label": window_shorthand(long_win.get("window_minutes")) if long_win else "7d",
            "plan":     rl.get("plan_type") or "?",
            "source":   "web",
        }
    except CodexAuthError:
        return {"error": _tr(lang,
            "无 Codex 权限（可能未订阅或需重新登录）",
            "No Codex access (subscription required or re-login needed)")}
    except CodexWebError as e:
        # Windows 的 Chrome/Edge Cookie 采用 App-Bound Encryption，网页实时读取
        # 失败时只使用足够新的本机会话快照；绝不自动调用 app-server，因为它在
        # 当前窗口未激活时可能触发一个新的 5 小时计时窗口。
        ts, rl = latest_codex_rate_limits()
        if ts is not None and rl:
            age = datetime.datetime.now(datetime.timezone.utc) - ts.astimezone(datetime.timezone.utc)
            if age.total_seconds() <= 10 * 60:
                short_win, long_win = _classify_codex_windows(rl)
                return {
                    "5h_left":  int(round(100 - short_win.get("used_percent", 0))) if short_win else None,
                    "7d_left":  int(round(100 - long_win.get("used_percent", 0))) if long_win else None,
                    "5h_reset": short_win.get("resets_at") if short_win else None,
                    "7d_reset": long_win.get("resets_at") if long_win else None,
                    "5h_label": window_shorthand(short_win.get("window_minutes")) if short_win else "5h",
                    "7d_label": window_shorthand(long_win.get("window_minutes")) if long_win else "7d",
                    "plan":     rl.get("plan_type") or "?",
                    "source":   "snapshot",
                    "data_at":  ts.isoformat(),
                }
        msg = str(e)
        if "cannot read chatgpt.com cookies" in msg or "App-Bound Encryption" in msg:
            msg = _tr(lang,
                "未读取到登录信息，请用 Firefox 登录 chatgpt.com 后重试",
                "No browser login found; sign in to chatgpt.com with Firefox and retry")
        elif "timed out" in msg or "urlopen" in msg:
            msg = _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
