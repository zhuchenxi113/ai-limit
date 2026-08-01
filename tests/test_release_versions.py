from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def _match(path: Path, pattern: str) -> str:
    match = re.search(pattern, path.read_text(encoding="utf-8"), re.MULTILINE)
    assert match is not None
    return match.group(1)


def test_release_versions_match_across_platforms() -> None:
    application_version = _match(
        ROOT / "usage.py", r'^__version__ = "([^"]+)"$'
    )
    windows_version = _match(
        ROOT / "menubar" / "windows" / "installer.iss",
        r'^#define AppVersion "([^"]+)"$',
    )
    macos_versions = re.findall(
        r'"CFBundle(?:ShortVersionString|Version)": "([^"]+)"',
        (ROOT / "menubar" / "setup.py").read_text(encoding="utf-8"),
    )

    assert application_version == "0.3.25"
    assert windows_version == application_version
    assert macos_versions == [application_version, application_version]
