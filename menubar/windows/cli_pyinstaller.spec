# -*- mode: python ; coding: utf-8 -*-
"""独立 CLI 可执行文件打包配置（onefile，console=True）。

跟 pyinstaller.spec（托盘 GUI，onedir + console=False）是两份独立产物：
GUI 子系统（console=False）编译出的 exe 从终端调用时不会向调用者的控制台
输出任何内容——这是 Windows 的进程/控制台附加机制决定的，不是打包配置能
绕开的——所以装完就能在终端用的 `ai-limit` 命令必须是单独一个 console=True
的可执行文件，不能复用 ai-limit-tray.exe。

onefile 理由：这个 exe 只打包 usage.py（纯 CLI 逻辑），不含 PySide6，体积远
小于托盘 GUI 的 onedir 产物，onefile 的启动解压开销可以接受。

用法（在 menubar/windows/ 目录下）：
    pyinstaller cli_pyinstaller.spec
"""
import pathlib

_HERE = pathlib.Path(SPECPATH).resolve()
_REPO = _HERE.parent.parent

block_cipher = None

a = Analysis(
    [str(_REPO / "usage.py")],
    pathex=[str(_REPO)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "win32api",
        "win32timezone",
        "browser_cookie3",
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    # 名字特意跟 GUI 安装包（ai-limit-windows-<version>-setup.exe）区分开、
    # 带上 "cli" 关键词——这份资产是给 install.ps1 自动下载用的，不是给人在
    # Release 页面手动点的；叫 "ai-limit.exe" 太容易被当成正式安装包点错
    # （双击后只会弹一个终端窗口跑默认参数、瞬间关闭，体验很怪）。
    name="ai-limit-windows-cli",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    icon=str(_HERE / "icon" / "ai-limit.ico"),
)
