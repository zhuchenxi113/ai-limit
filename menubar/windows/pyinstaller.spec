# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir，不用 onefile）。

onedir 理由：onefile 每次启动要解压到 %TEMP%，PySide6 体积大，冷启动
延迟明显；onedir 目录形态也更适合后续"整目录替换"的更新流程，语义
对应 mac 版整个 .app bundle 替换。

用法（在 menubar/windows/ 目录下）：
    pyinstaller pyinstaller.spec
"""
import pathlib

_HERE = pathlib.Path(SPECPATH).resolve()
_REPO = _HERE.parent.parent

block_cipher = None

a = Analysis(
    [str(_HERE / "ai-limit-tray.py")],
    pathex=[str(_REPO), str(_HERE)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "win32crypt",
        "win32cred",
        "win32api",
        "win32timezone",
        "Cryptodome.Cipher.AES",
        "Cryptodome.Protocol.KDF",
        "usage",
        "state",
        "lang_win",
        "icon_render",
        "autostart_win",
        "dialogs",
        "fetchers",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-limit-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(_HERE / "icon" / "ai-limit.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="ai-limit-tray",
)
