#!/usr/bin/env python3
"""ai-limit 菜单栏 App（rumps 版）

独立 macOS App，不依赖 SwiftBar，有自己的图标和进程。
py2app 打包：cd menubar && python3 setup.py py2app
"""
import datetime
import json
import pathlib
import sys
import threading
import webbrowser

import rumps
import AppKit

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from usage import (
    __version__,
    live_claude_plan,
    live_claude_usage,
    live_codex_web_usage,
    ClaudeWebError,
    CodexWebError,
    CodexAuthError,
    TZ_LOCAL,
    epoch_to_local,
)

# ── 常量 ─────────────────────────────────────────────────────────────────────

_STATE_PATH   = pathlib.Path.home() / ".ai-limit-menubar.json"
_CACHE_PATH   = pathlib.Path.home() / ".ai-limit-menubar-cache.json"
_CACHE_TTL    = 55
_REFRESH_SEC  = 60
_DISPLAY_MODES = ("5h", "7d")
_LANGS         = ("zh", "en")
_SERVICES      = ("claude", "codex")
_ZH_WEEKDAYS   = "一二三四五六日"
_EN_WEEKDAYS   = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_RESET_PAD  = 8
# 同步到开源版时改为 "https://github.com/zhuchenxi113/ai-limit"
_PROJECT_URL   = "https://gitee.com/zhuchenxi113/ai-limit-private"
_AUTHOR_URL_ZH = "https://gitee.com/zhuchenxi113"
_AUTHOR_URL_EN = "https://github.com/zhuchenxi113"

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _tr(lang, zh, en):
    return en if lang == "en" else zh

def _native_bar(pct, width=4):
    filled = round(max(0, min(100, pct)) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)

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
        return f"{wd.ljust(_EN_RESET_PAD)} {dt:%H:%M}"
    if days == 0:    wd = "今天"
    elif days == 1:  wd = "明天"
    elif days == 2:  wd = "后天"
    elif next_week:  wd = f"下周{_ZH_WEEKDAYS[dt.weekday()]}"
    else:            wd = f"周{_ZH_WEEKDAYS[dt.weekday()]}"
    if len(wd) < 3:
        wd += "　" * (3 - len(wd))
    return f"{wd} {dt:%H:%M}"

def _fmt_reset_epoch(epoch, lang="zh"):
    try:
        return _fmt_reset_dt(epoch_to_local(int(epoch)), lang)
    except Exception:
        return "?"

def _fmt_reset_iso(iso, lang="zh"):
    try:
        return _fmt_reset_dt(datetime.datetime.fromisoformat(iso).astimezone(TZ_LOCAL), lang)
    except Exception:
        return "?"

# ── 状态 / 缓存 ──────────────────────────────────────────────────────────────

def _load_state():
    state = {"global": "5h", "lang": "zh", "services": list(_SERVICES)}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
            if raw.get("lang") in _LANGS:
                state["lang"] = raw["lang"]
            if isinstance(raw.get("services"), list):
                svc = [s for s in raw["services"] if s in _SERVICES]
                if svc:
                    state["services"] = svc
    except Exception:
        pass
    return state

def _save_state(state):
    try:
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

def _load_cache():
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.datetime.now().timestamp() - float(raw.get("cached_at", 0))
        if age <= _CACHE_TTL:
            return raw.get("claude"), raw.get("codex")
    except Exception:
        pass
    return None, None

def _save_cache(claude, codex):
    try:
        _CACHE_PATH.write_text(
            json.dumps({
                "cached_at": datetime.datetime.now().timestamp(),
                "claude": claude,
                "codex": codex,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

# ── 数据获取 ─────────────────────────────────────────────────────────────────

def _fetch_claude(lang):
    import socket, urllib.error
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
        return {"error": str(e)}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _fetch_codex(lang):
    import socket, urllib.error
    try:
        _ts, rl = live_codex_web_usage()
        primary   = rl.get("primary") or {}
        secondary = rl.get("secondary") or {}
        return {
            "5h_left":  int(round(100 - primary.get("used_percent", 0))),
            "7d_left":  int(round(100 - secondary.get("used_percent", 0))),
            "5h_reset": primary.get("resets_at"),
            "7d_reset": secondary.get("resets_at"),
            "plan":     rl.get("plan_type") or "?",
        }
    except CodexAuthError:
        return {"error": _tr(lang,
            "无 Codex 权限（可能未订阅或需重新登录）",
            "No Codex access (subscription required or re-login needed)")}
    except CodexWebError as e:
        return {"error": str(e)}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

# ── AppKit 辅助 ───────────────────────────────────────────────────────────────

def _status_button(app):
    """返回 NSStatusItem.button()；rumps 在不同版本里把它存在不同属性下。"""
    # 已知 rumps 0.4 在 _nsapp.nsstatusitem，但版本间不一致；做一次探测
    candidates = ("_status_item", "_status_bar_item", "_nsstatusitem")
    for attr in candidates:
        item = getattr(app, attr, None)
        if item and hasattr(item, "button"):
            return item.button()
    # rumps 0.4.x 路径：app._nsapp.nsstatusitem
    nsapp = getattr(app, "_nsapp", None)
    if nsapp is not None:
        item = getattr(nsapp, "nsstatusitem", None)
        if item and hasattr(item, "button"):
            return item.button()
    # 兜底：扫一遍 app 所有属性，找一个 .button() 看起来对的
    for name in dir(app):
        if name.startswith("__"):
            continue
        try:
            item = getattr(app, name)
        except Exception:
            continue
        if item is not None and hasattr(item, "button") and callable(getattr(item, "button", None)):
            try:
                btn = item.button()
                if hasattr(btn, "setTitle_") and hasattr(btn, "setImage_"):
                    return btn
            except Exception:
                continue
    return None


def _set_bar_title(app, text):
    """纯文字标题（用作 SF Symbol 不可用时的兜底）。"""
    btn = _status_button(app)
    if btn is not None:
        btn.setImage_(None)
        btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_(""))
        btn.setTitle_(text)
        btn.setImagePosition_(0)  # NSNoImage
        return
    app.title = text


def _sf_battery_image(pct, point_size=14):
    """返回对应百分比的 SF Symbol 电池 NSImage（5 档量化）。

    粒度：0(<13) / 25 / 50 / 75 / 100(≥88)。
    不在这里上色——会作为 template 一起整合进 composite，由 AppKit 在状态
    栏上下文里和系统 Wi-Fi、电池等一起决定实际颜色（vibrancy/明暗自适应）。
    """
    if pct >= 88:
        name = "battery.100"
    elif pct >= 63:
        name = "battery.75"
    elif pct >= 38:
        name = "battery.50"
    elif pct >= 13:
        name = "battery.25"
    else:
        name = "battery.0"
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size, AppKit.NSFontWeightMedium
    )
    return img.imageWithSymbolConfiguration_(cfg)


def _battery_attachment(pct, font):
    """SF Symbol 电池包成 NSTextAttachment，可塞进 NSAttributedString 里跟文字一行排。

    image 设 template，菜单栏会把它当系统图标处理（vibrancy + 亮暗自适应），
    跟 Wi-Fi / 系统电池图标在同一渲染通道。
    """
    bat = _sf_battery_image(pct)
    if bat is None:
        return None
    bat.setTemplate_(True)
    attach = AppKit.NSTextAttachment.alloc().init()
    attach.setImage_(bat)
    sz = bat.size()
    # 垂直微调：让电池中线大致对齐文字中线
    y_offset = (font.capHeight() - sz.height) / 2
    attach.setBounds_(AppKit.NSMakeRect(0, y_offset, sz.width, sz.height))
    return AppKit.NSAttributedString.attributedStringWithAttachment_(attach)


def _render_attributed_title(items):
    """构建状态栏 attributed title：文字交给 NSStatusBarButton 原生渲染（拿到
    系统 vibrancy 和亮暗自适应），电池作为内联 template image 附件。

    旧方案是把整条画成位图（NSImage.lockFocus + labelColor），但 bitmap 里
    的文字是一次性栅格化的灰度，拿不到状态栏文字的 vibrancy，视觉上比系统
    时钟、菜单文字偏暗。
    """
    font = AppKit.NSFont.menuBarFontOfSize_(0)
    text_attrs = {AppKit.NSFontAttributeName: font}
    mas = AppKit.NSMutableAttributedString.alloc().init()

    def append_text(s):
        mas.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(s, text_attrs)
        )

    for i, (label, pct, err) in enumerate(items):
        prefix = "  " if i > 0 else ""
        if err:
            append_text(f"{prefix}{label} ⚠️")
            continue
        append_text(f"{prefix}{label} {pct}% ")
        bat_attach = _battery_attachment(pct, font)
        if bat_attach is not None:
            mas.appendAttributedString_(bat_attach)

    if mas.length() == 0:
        append_text("ai-limit ⚠️")
    return mas


def _set_bar_with_batteries(app, items):
    """把 attributed title（文字 + 电池附件）安到状态栏按钮上。"""
    btn = _status_button(app)
    if btn is None:
        raise RuntimeError("no status button")
    btn.setImage_(None)
    btn.setTitle_("")
    btn.setAttributedTitle_(_render_attributed_title(items))

def _noop(_):
    """无副作用 callback，仅用于让 macOS 把无动作菜单项也按常规文字色渲染。
    AppKit 会把 NSMenuItem.target=nil 的项自动灰化，setEnabled_(True) 也救不了；
    挂一个真实 callback（哪怕什么都不做）才会让 macOS 视为正常项。"""
    pass


def _disable(menu_item):
    """让菜单项显式灰色（仅用于'上次刷新'这种刻意的次要信息）。"""
    menu_item._menuitem.setEnabled_(False)
    return menu_item


def _inert(menu_item):
    """挂 no-op callback，让 macOS 按常规文字色渲染（不灰），点击无效果。"""
    menu_item.set_callback(_noop)
    return menu_item

def _detail_text(mode, pct, reset, lang):
    if lang == "en":
        return f"  {mode}\t{pct:>3}% left\t↻ {reset}"
    return f"  {mode}\t{pct:>3}% 剩余\t↻ {reset}"

# ── 主 App ────────────────────────────────────────────────────────────────────

class AiLimitApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self._state = _load_state()
        self._claude = None
        self._codex  = None
        # 后台线程把抓取结果放这里，由主线程的 _apply_pending 定时器接力
        self._pending = None
        self._pending_lock = threading.Lock()
        self._build_menu()

    # ── 菜单构建 ──────────────────────────────────────────────────────────────

    def _build_menu(self):
        lang = self._state["lang"]

        # Claude 区块（段头 + 详情都挂 no-op callback 避免 macOS 自动灰化）
        self._claude_header = _inert(rumps.MenuItem("Claude Code"))
        self._claude_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._claude_7d     = _inert(rumps.MenuItem("  7d  …"))

        # CodeX 区块
        self._codex_header = _inert(rumps.MenuItem("CodeX"))
        self._codex_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._codex_7d     = _inert(rumps.MenuItem("  7d  …"))

        # 上次刷新（次要信息，刻意灰色）
        self._last_refresh = _disable(rumps.MenuItem("…"))

        # 菜单栏显示子菜单
        self._mode_5h = rumps.MenuItem("5 小时" if lang == "zh" else "5 hours",
                                       callback=self._set_mode_5h)
        self._mode_7d = rumps.MenuItem("7 天" if lang == "zh" else "7 days",
                                       callback=self._set_mode_7d)
        mode_label = "菜单栏显示" if lang == "zh" else "Menu bar display"
        self._mode_menu = rumps.MenuItem(mode_label)
        self._mode_menu.add(self._mode_5h)
        self._mode_menu.add(self._mode_7d)

        # 语言子菜单
        self._lang_zh = rumps.MenuItem("中文", callback=self._set_lang_zh)
        self._lang_en = rumps.MenuItem("English", callback=self._set_lang_en)
        lang_label = "语言" if lang == "zh" else "Language"
        self._lang_menu = rumps.MenuItem(lang_label)
        self._lang_menu.add(self._lang_zh)
        self._lang_menu.add(self._lang_en)

        # 监控服务子菜单
        self._svc_claude = rumps.MenuItem("Claude Code", callback=self._toggle_claude)
        self._svc_codex  = rumps.MenuItem("CodeX",       callback=self._toggle_codex)
        svc_label = "监控服务" if lang == "zh" else "Services"
        self._svc_menu = rumps.MenuItem(svc_label)
        self._svc_menu.add(self._svc_claude)
        self._svc_menu.add(self._svc_codex)

        # 操作项
        self._refresh_item = rumps.MenuItem(
            "立即刷新" if lang == "zh" else "Refresh now",
            callback=self._force_refresh,
        )
        self._codex_dash = rumps.MenuItem(
            "打开 CodeX 分析页" if lang == "zh" else "Open CodeX analytics",
            callback=lambda _: webbrowser.open("https://chatgpt.com/codex/cloud/settings/analytics"),
        )
        self._claude_dash = rumps.MenuItem(
            "打开 Claude 用量页" if lang == "zh" else "Open Claude usage",
            callback=lambda _: webbrowser.open("https://claude.ai/settings/usage"),
        )

        # 关于子菜单
        about_label = f"关于（ai-limit {__version__}）" if lang == "zh" else f"About (ai-limit {__version__})"
        self._about_menu   = rumps.MenuItem(about_label)
        self._about_ver    = rumps.MenuItem(f"ai-limit {__version__}",
                                            callback=lambda _: webbrowser.open(_PROJECT_URL))
        self._about_author = rumps.MenuItem(
            "作者：zhuchenxi" if lang == "zh" else "Author: zhuchenxi",
            callback=lambda _: webbrowser.open(_AUTHOR_URL_ZH if self._state["lang"] == "zh" else _AUTHOR_URL_EN),
        )
        self._about_desc   = _disable(rumps.MenuItem(
            "Claude Code / CodeX 额度监控" if lang == "zh" else "Claude Code / CodeX quota monitor"
        ))
        self._about_src    = _disable(rumps.MenuItem(
            "数据来源：本地日志 + 官方网页接口" if lang == "zh" else "Source: local logs + official web endpoints"
        ))
        self._about_menu.add(self._about_ver)
        self._about_menu.add(self._about_author)
        self._about_menu.add(self._about_desc)
        self._about_menu.add(self._about_src)

        # 退出
        self._quit_item = rumps.MenuItem(
            "退出" if lang == "zh" else "Quit",
            callback=rumps.quit_application,
        )

        self.menu = [
            self._claude_header,
            self._claude_5h,
            self._claude_7d,
            None,
            self._codex_header,
            self._codex_5h,
            self._codex_7d,
            None,
            self._last_refresh,
            None,
            self._mode_menu,
            self._lang_menu,
            self._svc_menu,
            None,
            self._refresh_item,
            self._codex_dash,
            self._claude_dash,
            None,
            self._about_menu,
            None,
            self._quit_item,
        ]
        self._update_mode_checks()
        self._update_lang_checks()
        self._update_service_checks()

    # ── 数据更新 ──────────────────────────────────────────────────────────────
    #
    # 原则：网络抓取一律在后台线程跑，绝对不阻塞主 UI 线程，否则切换菜单时
    # macOS 会显示转圈光标。
    # 流程：
    #   主线程触发    → 立即用 _load_cache() 重画一次（瞬时响应）
    #                → 启动后台线程 _async_refresh()
    #   后台线程     → 调 _fetch_claude / _fetch_codex（耗时几秒）
    #                → 把结果塞进 self._pending（加锁）
    #   主线程定时器 → _apply_pending 每 0.4s 检查 _pending，有就 apply + 重画

    @rumps.timer(0.3)
    def _init_render(self, sender):
        """启动后立即用缓存重画 + 后台拉一次最新数据。"""
        self._refresh_from_cache()
        self._kick_background_fetch()
        sender.stop()

    @rumps.timer(_REFRESH_SEC)
    def _auto_refresh(self, _):
        """每 60s 后台拉一次。"""
        self._kick_background_fetch()

    @rumps.timer(0.4)
    def _apply_pending(self, _):
        """主线程接力点：把后台线程取到的数据 apply 到 UI。

        重点：服务被禁用时不要清空内存里的旧数据。后台线程对禁用服务返回
        None 表示"没拉新的"，不是"清空"——保留上次的值，重新启用时菜单栏
        瞬间显示该服务的最近一次缓存，避免 1-2s 网络抓取的等待感。
        """
        with self._pending_lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        claude, codex = pending
        if claude is not None:
            self._claude = claude
        if codex is not None:
            self._codex = codex
        _save_cache(self._claude, self._codex)
        self._render()

    def _refresh_from_cache(self):
        """主线程瞬时操作：读短缓存重画，不碰网络。"""
        claude, codex = _load_cache()
        # 不按 services 过滤——内存里保留两份数据，UI 显示由 _render 控
        if claude is not None:
            self._claude = claude
        if codex is not None:
            self._codex = codex
        self._render()

    def _kick_background_fetch(self):
        """启动后台线程抓数据；线程内不要碰任何 UI 对象。"""
        t = threading.Thread(target=self._async_refresh, daemon=True)
        t.start()

    def _async_refresh(self):
        """后台线程：抓数据 → 写共享变量。不能调任何 rumps/AppKit UI。"""
        lang = self._state["lang"]
        services = self._state.get("services") or list(_SERVICES)
        claude = _fetch_claude(lang) if "claude" in services else None
        codex  = _fetch_codex(lang)  if "codex"  in services else None
        with self._pending_lock:
            self._pending = (claude, codex)

    def _render(self):
        lang     = self._state["lang"]
        mode     = self._state["global"]
        services = self._state.get("services") or list(_SERVICES)
        show_claude = "claude" in services
        show_codex  = "codex"  in services
        claude = self._claude or {}
        codex  = self._codex  or {}

        # 菜单栏标题：[Claude 68% ⌬]  [CodeX 99% ⌬]
        # 电池是原生 SF Symbol，Apple 亲手画的 iPhone 风格，向量永不糊
        bar_items = []
        if show_claude:
            if "error" in claude:
                bar_items.append(("Claude", 0, True))
            elif claude:
                pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
                bar_items.append(("Claude", pct, False))
        if show_codex:
            if "error" in codex:
                bar_items.append(("CodeX", 0, True))
            elif codex:
                pct = codex["5h_left"] if mode == "5h" else codex["7d_left"]
                bar_items.append(("CodeX", pct, False))
        try:
            _set_bar_with_batteries(self, bar_items)
        except Exception:
            # SF Symbol 不可用时（很老的 macOS）回退到 ▰▱ 文字版
            parts = [
                f"{lbl} ⚠️" if err else f"{lbl} {pct}% {_native_bar(pct)}"
                for lbl, pct, err in bar_items
            ]
            _set_bar_title(self, "  ".join(parts) if parts else "ai-limit ⚠️")

        # Claude 区块 —— 服务被关时整段隐藏
        self._claude_header._menuitem.setHidden_(not show_claude)
        self._claude_5h._menuitem.setHidden_(not show_claude)
        self._claude_7d._menuitem.setHidden_(not show_claude)
        if show_claude:
            if "error" in claude:
                self._claude_header.title = "Claude Code ⚠️"
                self._claude_5h.title = f"  {claude['error'][:60]}"
                self._claude_7d._menuitem.setHidden_(True)
            elif claude:
                plan = _fmt_plan(claude.get("plan"), lang)
                self._claude_header.title = f"Claude Code{plan}"
                c5_reset = _fmt_reset_iso(claude["5h_reset"], lang)
                c7_reset = _fmt_reset_iso(claude["7d_reset"], lang)
                self._claude_5h.title = _detail_text("5h", claude["5h_left"], c5_reset, lang)
                self._claude_7d.title = _detail_text("7d", claude["7d_left"], c7_reset, lang)

        # CodeX 区块
        self._codex_header._menuitem.setHidden_(not show_codex)
        self._codex_5h._menuitem.setHidden_(not show_codex)
        self._codex_7d._menuitem.setHidden_(not show_codex)
        if show_codex:
            if "error" in codex:
                self._codex_header.title = "CodeX ⚠️"
                self._codex_5h.title = f"  {codex['error'][:60]}"
                self._codex_7d._menuitem.setHidden_(True)
            elif codex:
                plan = _fmt_plan(codex.get("plan"), lang)
                self._codex_header.title = f"CodeX{plan}"
                x5_reset = _fmt_reset_epoch(codex["5h_reset"], lang)
                x7_reset = _fmt_reset_epoch(codex["7d_reset"], lang)
                self._codex_5h.title = _detail_text("5h", codex["5h_left"], x5_reset, lang)
                self._codex_7d.title = _detail_text("7d", codex["7d_left"], x7_reset, lang)

        # 刷新时间
        now = datetime.datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
        self._last_refresh.title = _tr(lang, f"上次刷新: {now}", f"Last refresh: {now}")

    # ── 模式 / 语言切换 ──────────────────────────────────────────────────────

    def _set_mode_5h(self, _):
        self._state["global"] = "5h"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()  # 只换显示窗口，数据没变，直接重画

    def _set_mode_7d(self, _):
        self._state["global"] = "7d"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()

    def _update_mode_checks(self):
        lang = self._state["lang"]
        mode = self._state["global"]
        self._mode_5h.title = ("✓ " if mode == "5h" else "  ") + _tr(lang, "5 小时", "5 hours")
        self._mode_7d.title = ("✓ " if mode == "7d" else "  ") + _tr(lang, "7 天", "7 days")
        self._mode_menu.title = _tr(lang,
            f"菜单栏显示（{_tr(lang, '5 小时', '5 hours') if mode == '5h' else _tr(lang, '7 天', '7 days')}）",
            f"Menu bar display ({_tr(lang, '5 hours', '5 hours') if mode == '5h' else '7 days'})",
        )

    def _set_lang_zh(self, _):
        self._state["lang"] = "zh"
        _save_state(self._state)
        self._update_lang_checks()
        # 重画所有 i18n 文本（详情行 / 段头 / "上次刷新" 等）
        self._update_mode_checks()
        self._update_service_checks()
        self._refresh_static_labels()
        self._render()

    def _set_lang_en(self, _):
        self._state["lang"] = "en"
        _save_state(self._state)
        self._update_lang_checks()
        self._update_mode_checks()
        self._update_service_checks()
        self._refresh_static_labels()
        self._render()

    def _refresh_static_labels(self):
        """语言切换后，更新所有不依赖数据的菜单文字。"""
        lang = self._state["lang"]
        self._refresh_item.title = _tr(lang, "立即刷新", "Refresh now")
        self._codex_dash.title  = _tr(lang, "打开 CodeX 分析页", "Open CodeX analytics")
        self._claude_dash.title = _tr(lang, "打开 Claude 用量页", "Open Claude usage")
        self._about_menu.title  = _tr(lang,
            f"关于（ai-limit {__version__}）",
            f"About (ai-limit {__version__})",
        )
        self._about_author.title = _tr(lang, "作者：zhuchenxi", "Author: zhuchenxi")
        self._about_desc.title   = _tr(lang,
            "Claude Code / CodeX 额度监控",
            "Claude Code / CodeX quota monitor",
        )
        self._about_src.title    = _tr(lang,
            "数据来源：本地日志 + 官方网页接口",
            "Source: local logs + official web endpoints",
        )
        self._quit_item.title    = _tr(lang, "退出", "Quit")

    def _update_lang_checks(self):
        lang = self._state["lang"]
        self._lang_zh.title = ("✓ " if lang == "zh" else "  ") + "中文"
        self._lang_en.title = ("✓ " if lang == "en" else "  ") + "English"
        self._lang_menu.title = _tr(lang,
            f"语言（{'中文' if lang == 'zh' else 'English'}）",
            f"Language ({'中文' if lang == 'zh' else 'English'})",
        )

    # ── 监控服务切换 ────────────────────────────────────────────────────────

    def _toggle_claude(self, _):
        self._toggle_service("claude")

    def _toggle_codex(self, _):
        self._toggle_service("codex")

    def _toggle_service(self, service):
        svc = list(self._state.get("services") or list(_SERVICES))
        if service in svc:
            svc.remove(service)
        else:
            svc.append(service)
        if not svc:
            # 不允许两个都关掉，回退保留刚才被关的
            svc = [service]
        self._state["services"] = svc
        _save_state(self._state)
        self._update_service_checks()
        # 立即用现有数据重画（隐藏/显示对应区块），不卡 UI；
        # 新启用的服务若有 ≤55s 的缓存就用，否则等下面后台拉
        self._render()
        # 后台异步刷新（如果新启用的服务无缓存，几秒后自动出现）
        self._kick_background_fetch()

    def _update_service_checks(self):
        lang = self._state["lang"]
        svc = self._state.get("services") or list(_SERVICES)
        self._svc_claude.title = ("✓ " if "claude" in svc else "  ") + "Claude Code"
        self._svc_codex.title  = ("✓ " if "codex"  in svc else "  ") + "CodeX"
        summary = _tr(lang, "全部", "All") if len(svc) == 2 else (
            "Claude Code" if "claude" in svc else "CodeX"
        )
        self._svc_menu.title = _tr(lang, f"监控服务（{summary}）", f"Services ({summary})")

    # ── 立即刷新 ──────────────────────────────────────────────────────────────

    def _force_refresh(self, _):
        try:
            _CACHE_PATH.unlink()
        except Exception:
            pass
        # 后台拉，不卡 UI；新数据 ≤几秒内通过 _apply_pending 落到菜单上
        self._kick_background_fetch()


if __name__ == "__main__":
    AiLimitApp().run()
