import datetime
from unittest.mock import patch

import usage


# ── 语言标签分类（简体 / 繁体 / 英文三态） ──────────────────────────────────

def test_classify_explicit_script_subtag():
    assert usage._classify_lang_tag("zh-Hant-TW") == "zh-Hant"
    assert usage._classify_lang_tag("zh-Hans-CN") == "zh-Hans"


def test_classify_traditional_regions():
    for tag in ("zh-TW", "zh-HK", "zh-MO", "zh_TW.UTF-8"):
        assert usage._classify_lang_tag(tag) == "zh-Hant", tag


def test_classify_simplified_regions():
    for tag in ("zh-CN", "zh-SG", "zh_CN.UTF-8"):
        assert usage._classify_lang_tag(tag) == "zh-Hans", tag


def test_classify_windows_localized_display_name():
    # locale.getlocale() 在 Windows 上可能返回这种本地化显示名，不是 POSIX 格式。
    assert usage._classify_lang_tag("chinese (traditional)_taiwan") == "zh-Hant"
    assert usage._classify_lang_tag("chinese (simplified)_china") == "zh-Hans"


def test_classify_unknown_region_defaults_to_simplified():
    assert usage._classify_lang_tag("zh") == "zh-Hans"


def test_classify_non_chinese_is_english():
    assert usage._classify_lang_tag("en-US") == "en"
    assert usage._classify_lang_tag("ja-JP") == "en"


# ── _detect_lang() 优先级：env var > Windows API > POSIX locale ────────────

def test_env_var_overrides_everything(monkeypatch):
    monkeypatch.setenv("AI_LIMIT_LANG", "zh-Hant")
    assert usage._detect_lang() == "zh-Hant"

    monkeypatch.setenv("AI_LIMIT_LANG", "zh-Hans")
    assert usage._detect_lang() == "zh-Hans"

    monkeypatch.setenv("AI_LIMIT_LANG", "en")
    assert usage._detect_lang() == "en"


def test_env_var_accepts_region_style_values(monkeypatch):
    monkeypatch.setenv("AI_LIMIT_LANG", "zh-TW")
    assert usage._detect_lang() == "zh-Hant"


def test_windows_branch_used_when_no_env_var(monkeypatch):
    monkeypatch.delenv("AI_LIMIT_LANG", raising=False)
    monkeypatch.setattr(usage, "IS_WINDOWS", True)
    with patch.object(usage, "_windows_ui_language_tag", return_value="zh-Hant-TW"):
        assert usage._detect_lang() == "zh-Hant"


def test_windows_branch_exception_falls_through_to_locale(monkeypatch):
    monkeypatch.delenv("AI_LIMIT_LANG", raising=False)
    monkeypatch.setattr(usage, "IS_WINDOWS", True)
    with patch.object(usage, "_windows_ui_language_tag", side_effect=OSError("boom")):
        # Windows 分支异常时不应该整体崩掉，应该继续走 POSIX locale 兜底
        # （这里不校验具体结果，只校验不抛异常）。
        usage._detect_lang()


# ── t() 三态选择 ─────────────────────────────────────────────────────────

def test_t_picks_traditional(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hant")
    assert usage.t("简体", "繁體", "English") == "繁體"


def test_t_picks_simplified(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hans")
    assert usage.t("简体", "繁體", "English") == "简体"


def test_t_picks_english(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "en")
    assert usage.t("简体", "繁體", "English") == "English"


def test_english_timezone_does_not_use_localized_windows_name(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "en")
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "UTC+08:00"
    assert usage.fmt_dt(dt) == "08-02 00:36 UTC+08:00"
    assert "中国标准时间" not in usage.fmt_reset_dt(dt)


def test_chinese_timezone_keeps_system_timezone_name(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hans")
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "中国标准时间"


def test_traditional_chinese_timezone_keeps_system_timezone_name_on_macos(monkeypatch):
    # macOS 的 strftime('%Z') 本身是语言无关的 ASCII 缩写（如 "CST"），不会跟
    # 系统区域设置的简繁字体混淆，所以非 Windows 平台继续沿用 OS 原生文本。
    monkeypatch.setattr(usage, "LANG", "zh-Hant")
    monkeypatch.setattr(usage, "IS_WINDOWS", False)
    monkeypatch.setattr(usage, "TZ_ABBR", "CST")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "CST"


def test_traditional_chinese_timezone_avoids_windows_os_text(monkeypatch):
    # Windows 的 strftime('%Z') 跟随系统区域设置返回完整本地化名称。系统区域
    # 设置是简体（多数中文 Windows 用户的默认状态）、AI_LIMIT_LANG 显式切到
    # zh-Hant 时，这段 OS 文本不会跟着变繁体，会在繁体输出里混入简体字
    # （2026-08-04 实测复现）。所以 Windows 上 zh-Hant 改走数字 UTC 偏移，
    # 不信任 OS 文本的字体一定跟 LANG 匹配。
    monkeypatch.setattr(usage, "LANG", "zh-Hant")
    monkeypatch.setattr(usage, "IS_WINDOWS", True)
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "UTC+08:00"


def test_simplified_chinese_timezone_keeps_windows_os_text(monkeypatch):
    # zh-Hans 是 Windows 中文用户的默认检测结果，OS 原生文本本身跟场景一致，
    # 不受这次 zh-Hant 专属修复影响，继续沿用。
    monkeypatch.setattr(usage, "LANG", "zh-Hans")
    monkeypatch.setattr(usage, "IS_WINDOWS", True)
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "中国标准时间"


def test_reset_dt_uses_traditional_weekday_characters(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hant")
    today = datetime.datetime.now(usage.TZ_LOCAL).date()
    day_after_tomorrow = datetime.datetime.combine(
        today + datetime.timedelta(days=2), datetime.time(9, 0), tzinfo=usage.TZ_LOCAL
    )
    formatted = usage.fmt_reset_dt(day_after_tomorrow)
    assert "後天" in formatted
    assert "后天" not in formatted


def test_reset_dt_uses_simplified_weekday_characters(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hans")
    today = datetime.datetime.now(usage.TZ_LOCAL).date()
    day_after_tomorrow = datetime.datetime.combine(
        today + datetime.timedelta(days=2), datetime.time(9, 0), tzinfo=usage.TZ_LOCAL
    )
    formatted = usage.fmt_reset_dt(day_after_tomorrow)
    assert "后天" in formatted
    assert "後天" not in formatted


def test_english_error_replaces_os_localized_text(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "en")
    error = "<urlopen error [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。>"

    formatted = usage.fmt_error(error)

    assert formatted == "<urlopen error [WinError 10013] localized system error>"
    assert not any("\u3400" <= char <= "\u9fff" for char in formatted)


def test_chinese_error_keeps_os_localized_text(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh-Hans")
    error = "[WinError 10013] 访问被拒绝"

    assert usage.fmt_error(error) == error
