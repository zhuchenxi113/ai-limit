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
    _classify_codex_windows,
    ClaudeWebError,
    CodexWebError,
    CodexAuthError,
    TZ_LOCAL,
    epoch_to_local,
    fetch_status_components,
    worst_status_by_id,
    CLAUDE_STATUS_COMPONENTS_URL,
    CODEX_STATUS_COMPONENTS_URL,
)

from lang_win import tr as _tr
_ZH_WEEKDAYS = "一二三四五六日"
_EN_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

_STATUS_COLORS = {
    # Claude Status 官方色系：status.claude.com 的 pageColorData。必须与 mac 版
    # ai-limit-app.py 的 _STATUS_COLORS 逐项一致，否则同一状态在两平台颜色会
    # 明显偏色（如 degraded_performance 曾用 #c78d00，比官方 #FAA72A 偏暗偏棕）。
    "operational": "#76AD2A",
    "under_maintenance": "#2C84DB",
    "degraded_performance": "#FAA72A",
    "partial_outage": "#E86235",
    "major_outage": "#E04343",
    "critical": "#E04343",
    "unknown": "#B0AEA5",
    "loading": "#B0AEA5",
}


def fetch_service_status(service: str):
    """Fetch a full, current Statuspage component list for one provider."""
    url = (CLAUDE_STATUS_COMPONENTS_URL if service == "claude"
           else CODEX_STATUS_COMPONENTS_URL)
    components = fetch_status_components(url)
    return components if components is not None else "unknown"


def status_info(raw_status, selected_keys: list[str], service: str, lang: str):
    """Format the worst selected component for the Windows panel.

    selected_keys 是内部稳定 key 列表；按官方组件 ID 匹配实时状态，官方改名
    （name 变、id 不变）时仍能命中。展示名优先用本次 API 返回的官方名称。
    """
    if not selected_keys:
        return None
    if raw_status is None:
        return {
            "text": _tr(lang, "正在获取状态…", "Fetching status…"),
            "status": "loading",
            "color": _STATUS_COLORS["loading"],
        }
    if raw_status == "unknown":
        return {
            "text": _tr(lang, "状态未知", "Status unknown"),
            "status": "unknown",
            "color": _STATUS_COLORS["unknown"],
        }
    result = worst_status_by_id(raw_status, selected_keys, service)
    if result is None:
        return {
            "text": _tr(lang, "状态未知", "Status unknown"),
            "status": "unknown",
            "color": _STATUS_COLORS["unknown"],
        }
    status, _key, component = result
    labels = {
        "operational": _tr(lang, "状态正常", "Operational"),
        "under_maintenance": _tr(lang, "维护中", "Maintenance"),
        "degraded_performance": _tr(lang, "性能下降", "Degraded"),
        "partial_outage": _tr(lang, "部分中断", "Partial outage"),
        "major_outage": _tr(lang, "服务中断", "Outage"),
        "critical": _tr(lang, "服务中断", "Outage"),
    }
    return {
        "text": labels.get(status, _tr(lang, "状态未知", "Status unknown")),
        "status": status,
        "component": component,
        "color": _STATUS_COLORS.get(status, _STATUS_COLORS["unknown"]),
    }


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
        data = live_claude_usage(browser="firefox")
        source = "firefox"
        try:
            plan = live_claude_plan(browser="firefox")
        except Exception:
            plan = None
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        return {
            "5h_left":  int(round(100 - float(five_h.get("utilization", 0)))),
            "7d_left":  int(round(100 - float(seven_d.get("utilization", 0)))),
            "5h_reset": five_h.get("resets_at"),
            "7d_reset": seven_d.get("resets_at"),
            "plan":     plan,
            "source":   source,
        }
    except ClaudeWebError as e:
        kind = getattr(e, "kind", "generic")
        raw_msg = str(e)
        if kind == "cloudflare":
            msg = _tr(lang, "被拦截，打开用量页勿关", "Blocked, open Claude usage, keep open")
        elif kind == "auth":
            msg = _tr(lang, "需在浏览器重新登录 claude.ai", "Re-login at claude.ai in browser")
        elif kind == "browser_session" or "cannot read browser cookies" in raw_msg:
            msg = _tr(lang,
                "请在 Firefox 登录 claude.ai",
                "Sign in to claude.ai with Firefox")
        else:
            msg = raw_msg
            if "JSON" in msg or "DOCTYPE" in msg or "html" in msg.lower():
                msg = _tr(lang, "网络不可用或需重新登录 claude.ai", "Network error or re-login at claude.ai required")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {
            "error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later"),
            "icon_warning": False,
        }
    except urllib.error.URLError:
        return {
            "error": _tr(lang, "网络不可用", "Network unavailable"),
            "icon_warning": False,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fetch_codex(lang):
    try:
        _ts, rl = live_codex_web_usage(browser="firefox")
        short_win, long_win = _classify_codex_windows(rl)
        return {
            "5h_left":  int(round(100 - short_win.get("used_percent", 0))) if short_win else None,
            "7d_left":  int(round(100 - long_win.get("used_percent", 0))) if long_win else None,
            "5h_reset": short_win.get("resets_at") if short_win else None,
            "7d_reset": long_win.get("resets_at") if long_win else None,
            "5h_label": window_shorthand(short_win.get("window_minutes")) if short_win else "5h",
            "7d_label": window_shorthand(long_win.get("window_minutes")) if long_win else "7d",
            "plan":     rl.get("plan_type") or "?",
            "source":   "firefox",
        }
    except CodexAuthError:
        return {"error": _tr(lang,
            "无 Codex 权限（可能未订阅或需重新登录）",
            "No Codex access (subscription required or re-login needed)")}
    except CodexWebError as e:
        kind = getattr(e, "kind", "generic")
        msg = str(e)
        if kind in ("browser_session", "auth") or "cannot read chatgpt.com cookies" in msg:
            msg = _tr(lang,
                "请在 Firefox 登录 chatgpt.com",
                "Sign in to chatgpt.com with Firefox")
        elif kind in ("network", "timeout") or "timed out" in msg or "urlopen" in msg:
            msg = _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")
            return {"error": msg, "icon_warning": False}
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {
            "error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later"),
            "icon_warning": False,
        }
    except urllib.error.URLError:
        return {
            "error": _tr(lang, "网络不可用", "Network unavailable"),
            "icon_warning": False,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
