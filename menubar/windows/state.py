"""偏好设置 / 缓存 / 历史持久化。

纯 JSON 文件读写，跟 mac 版（ai-limit-app.py）用同一套文件路径和格式，
两个平台共用一份偏好数据（不是各自独立的状态）。
"""
import datetime
import json
import pathlib

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

CLAUDE_STATUS_ALL = (
    "Claude Code",
    "claude.ai",
    "Claude API (api.anthropic.com)",
    "Claude Console (platform.claude.com)",
)
CLAUDE_STATUS_DEFAULT = ("Claude Code",)
CODEX_STATUS_ALL = (
    "Codex in ChatGPT Desktop",
    "CLI",
    "Codex API",
    "VS Code extension",
    "Codex Web",
)
CODEX_STATUS_DEFAULT = ("Codex in ChatGPT Desktop", "CLI", "Codex API")


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
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
            windows = raw.get("display_windows")
            if isinstance(windows, list):
                selected = [mode for mode in _DISPLAY_MODES if mode in windows]
                if selected:
                    state["display_windows"] = selected
            else:
                # Existing installations had a single radio selection.
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
            if isinstance(raw.get("claude_status_components"), list):
                state["claude_status_components"] = [
                    name for name in CLAUDE_STATUS_ALL
                    if name in raw["claude_status_components"]
                ]
            if isinstance(raw.get("codex_status_components"), list):
                state["codex_status_components"] = [
                    name for name in CODEX_STATUS_ALL
                    if name in raw["codex_status_components"]
                ]
            retry_until = raw.get("oauth_retry_until")
            if isinstance(retry_until, dict):
                state["oauth_retry_until"] = {
                    service: float(retry_until[service])
                    for service in _SERVICES
                    if isinstance(retry_until.get(service), (int, float))
                }
    except Exception:
        pass
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
