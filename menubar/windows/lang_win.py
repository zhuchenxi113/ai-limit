"""Windows 系统界面语言检测。

不用 locale 标准库：Windows"区域格式"与"显示语言"是两个独立设置，
locale.getlocale() 反映的是前者，不一定代表用户在
"设置 → 时间和语言 → 语言"里选择的界面显示语言。
GetUserDefaultUILanguage 直接取后者，更准确。
"""
import ctypes

_LANG_CHINESE = 0x04  # LANG_CHINESE primary language ID


def detect_system_lang() -> str:
    try:
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary_lang = lcid & 0x3FF
        return "zh" if primary_lang == _LANG_CHINESE else "en"
    except Exception:
        return "en"


def tr(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh
