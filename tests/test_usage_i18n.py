import datetime

import usage


def test_english_timezone_does_not_use_localized_windows_name(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "en")
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "UTC+08:00"
    assert usage.fmt_dt(dt) == "08-02 00:36 UTC+08:00"
    assert "中国标准时间" not in usage.fmt_reset_dt(dt)


def test_chinese_timezone_keeps_system_timezone_name(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh")
    monkeypatch.setattr(usage, "TZ_ABBR", "中国标准时间")
    dt = datetime.datetime(2026, 8, 2, 0, 36,
                           tzinfo=datetime.timezone(datetime.timedelta(hours=8)))

    assert usage.fmt_timezone(dt) == "中国标准时间"


def test_english_error_replaces_os_localized_text(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "en")
    error = "<urlopen error [WinError 10013] 以一种访问权限不允许的方式做了一个访问套接字的尝试。>"

    formatted = usage.fmt_error(error)

    assert formatted == "<urlopen error [WinError 10013] localized system error>"
    assert not any("\u3400" <= char <= "\u9fff" for char in formatted)


def test_chinese_error_keeps_os_localized_text(monkeypatch):
    monkeypatch.setattr(usage, "LANG", "zh")
    error = "[WinError 10013] 访问被拒绝"

    assert usage.fmt_error(error) == error
