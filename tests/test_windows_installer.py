from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "menubar" / "windows" / "installer.iss"
USAGE = ROOT / "usage.py"


def _script() -> str:
    return INSTALLER.read_text(encoding="utf-8")


def test_installer_uses_product_identity_and_chinese_wizard() -> None:
    script = _script()

    assert "AppVerName=AI Limit {#AppVersion}" in script
    assert "SetupIconFile=icon\\ai-limit.ico" in script
    assert "WizardStyle=modern" in script
    assert "ShowLanguageDialog=no" in script
    assert "LanguageDetectionMethod=locale" in script
    assert "UsePreviousLanguage=no" in script
    assert 'Name: "chinesesimplified"; MessagesFile: "languages\\ChineseSimplified.isl"' in script
    assert script.index('Name: "chinesesimplified"') < script.index('Name: "english"')


def test_installer_always_offers_install_location() -> None:
    script = _script()

    assert "DisableDirPage=no" in script
    assert "UsePreviousAppDir=yes" in script


def test_installer_uninstalls_previous_copy_before_relocation() -> None:
    script = _script()

    assert "function IsRelocation: Boolean;" in script
    assert "function PrepareToInstall(var NeedsRestart: Boolean): String;" in script
    assert "PreviousUninstallExe" in script
    assert "ewWaitUntilTerminated" in script
    assert "OldExecutable := AddBackslash(PreviousInstallDir) + 'ai-limit-tray.exe';" in script
    assert "PreviousInstallStillPresent" in script
    assert "DelTree(" not in script


def test_installer_offers_desktop_icon_and_autostart_tasks() -> None:
    script = _script()

    assert 'Name: "desktopicon";' in script
    assert 'Name: "autostart";' in script
    assert 'Name: "{autodesktop}\\AI Limit";' in script
    assert 'Tasks: desktopicon' in script
    assert 'Subkey: "Software\\Microsoft\\Windows\\CurrentVersion\\Run";' in script
    assert 'ValueName: "AI Limit";' in script
    assert 'Flags: uninsdeletevalue; Tasks: autostart' in script


def test_installer_version_matches_application_version() -> None:
    installer_match = re.search(r'^#define AppVersion "([^"]+)"$', _script(), re.MULTILINE)
    usage_match = re.search(
        r'^__version__ = "([^"]+)"$',
        USAGE.read_text(encoding="utf-8"),
        re.MULTILINE,
    )

    assert installer_match is not None
    assert usage_match is not None
    assert installer_match.group(1) == usage_match.group(1)
