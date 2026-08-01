"""偏好设置 / 缓存 / 历史持久化。

纯 JSON 文件读写，跟 mac 版（ai-limit-app.py）用同一套文件路径和格式，
两个平台共用一份偏好数据（不是各自独立的状态）。
"""
import datetime
import json
import pathlib

import usage

_STATE_PATH   = pathlib.Path.home() / ".ai-limit-menubar.json"
_CACHE_PATH   = pathlib.Path.home() / ".ai-limit-menubar-cache.json"
_HISTORY_PATH = pathlib.Path.home() / ".ai-limit-menubar-history.jsonl"
_CACHE_TTL    = 55
_HISTORY_RETENTION_SEC = 2 * 60 * 60

_DISPLAY_MODES = ("5h", "7d")
_BAR_STYLES    = ("both", "number", "battery")
_LANGS         = ("zh", "en", "auto")
_SERVICES      = ("claude", "codex")
_REFRESH_MINS  = (1, 2, 3, 4, 5)

# 状态渠道的权威定义在 usage.py（跨平台单源），这里只保留内部 key 视图，
# 供菜单构建和默认勾选使用。旧版本曾在本文件按官方名称存勾选，现已改为内部 key，
# 迁移逻辑见 load_state。
CLAUDE_STATUS_DEFAULT = tuple(usage.status_default_selection("claude"))
CODEX_STATUS_DEFAULT = tuple(usage.status_default_selection("codex"))

# load_state 直接管理（会做校验/迁移）的字段；不在此集合里的字段视为
# "另一平台或未来版本写入的未知字段"，原样保留、不抹掉。
_MANAGED_KEYS = {
    "global", "display_windows", "lang", "bar_services", "panel_services",
    "bar_style", "refresh_min", "claude_status_components",
    "codex_status_components", "oauth_retry_until",
}


def load_state() -> dict:
    state = {
        "global": "5h", "lang": "auto",
        "display_windows": ["5h"],
        "oauth_retry_until": {},
        "bar_services": list(_SERVICES),
        "panel_services": list(_SERVICES),
        "bar_style": "both",
        "refresh_min": 1,
        "claude_status_components": list(CLAUDE_STATUS_DEFAULT),
        "codex_status_components": list(CODEX_STATUS_DEFAULT),
    }
    needs_writeback = False
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            # 先原样保留未知字段（另一平台 / 未来版本写入的），避免下次 save 抹掉。
            for k, v in raw.items():
                if k not in _MANAGED_KEYS:
                    state[k] = v
            stored_global = raw.get("global")
            if stored_global in _DISPLAY_MODES:
                state["global"] = stored_global
            windows = raw.get("display_windows")
            if isinstance(windows, list):
                selected = [mode for mode in _DISPLAY_MODES if mode in windows]
                if stored_global not in _DISPLAY_MODES and selected:
                    state["global"] = selected[0]
                # Windows originally allowed both periods to be checked even
                # though a tray icon can render only one. Normalize old state
                # to the single icon period and persist the migration once.
                state["display_windows"] = [state["global"]]
                if selected != state["display_windows"]:
                    needs_writeback = True
            else:
                state["display_windows"] = [state["global"]]
            if raw.get("lang") in _LANGS:
                state["lang"] = raw["lang"]
            if isinstance(raw.get("bar_services"), list):
                f = [s for s in raw["bar_services"] if s in _SERVICES]
                if f:
                    state["bar_services"] = f
            if isinstance(raw.get("panel_services"), list):
                state["panel_services"] = [s for s in raw["panel_services"] if s in _SERVICES]
            if raw.get("bar_style") in _BAR_STYLES:
                state["bar_style"] = raw["bar_style"]
            if raw.get("refresh_min") in _REFRESH_MINS:
                state["refresh_min"] = raw["refresh_min"]
            for service in _SERVICES:
                skey = f"{service}_status_components"
                stored = raw.get(skey)
                if isinstance(stored, list):
                    # 旧配置可能按官方名称 / 历史别名（如 "App"）存勾选，统一规范化为
                    # 内部 key。空列表规范化后仍是空（保留"不显示状态点"意图，不回退默认）。
                    normalized = usage.normalize_status_selection(service, stored)
                    state[skey] = normalized
                    # 存储值里出现任何"不是内部 key"的写法，说明是旧格式，需一次性写回。
                    valid_keys = {k for k, _c, _n in usage.status_channels(service)}
                    if any(item not in valid_keys for item in stored):
                        needs_writeback = True
            retry_until = raw.get("oauth_retry_until")
            if isinstance(retry_until, dict):
                state["oauth_retry_until"] = {
                    service: float(retry_until[service])
                    for service in _SERVICES
                    if isinstance(retry_until.get(service), (int, float))
                }
    except Exception:
        pass
    if needs_writeback:
        # 迁移是幂等的：把规范化后的内部 key 一次性写回磁盘，之后再 load 就不再触发。
        save_state(state)
    return state


def save_state(state: dict) -> None:
    try:
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def load_cache():
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.datetime.now().timestamp() - float(raw.get("cached_at", 0))
        if age <= _CACHE_TTL:
            return raw.get("claude"), raw.get("codex")
    except Exception:
        pass
    return None, None


def save_cache(claude, codex) -> None:
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


def _history_snapshot(data):
    if data is None:
        return None
    if isinstance(data, dict) and "error" in data:
        return {"error": str(data.get("error", ""))[:200]}
    if not isinstance(data, dict):
        return None
    return {
        "5h_left": data.get("5h_left"),
        "7d_left": data.get("7d_left"),
        "5h_reset": data.get("5h_reset"),
        "7d_reset": data.get("7d_reset"),
        "plan": data.get("plan"),
    }


def append_history(claude, codex) -> None:
    """跟 mac 版格式一致（ts/epoch 字段名、剪枝逻辑），两个平台写同一个文件。"""
    try:
        now = datetime.datetime.now().astimezone()
        entry = {
            "ts": now.isoformat(timespec="seconds"),
            "epoch": now.timestamp(),
        }
        if claude is not None:
            entry["claude"] = _history_snapshot(claude)
        if codex is not None:
            entry["codex"] = _history_snapshot(codex)
        if "claude" not in entry and "codex" not in entry:
            return

        cutoff = entry["epoch"] - _HISTORY_RETENTION_SEC
        kept = []
        try:
            for line in _HISTORY_PATH.read_text(encoding="utf-8").splitlines():
                try:
                    old = json.loads(line)
                except Exception:
                    continue
                if float(old.get("epoch", 0)) >= cutoff:
                    kept.append(old)
        except FileNotFoundError:
            pass
        kept.append(entry)
        _HISTORY_PATH.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in kept) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass
