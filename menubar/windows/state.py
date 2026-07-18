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


def load_state() -> dict:
    state = {
        "global": "5h", "lang": "auto",
        "bar_services": list(_SERVICES),
        "panel_services": list(_SERVICES),
        "bar_style": "both",
        "refresh_min": 1,
    }
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
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
