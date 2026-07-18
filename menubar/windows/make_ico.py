#!/usr/bin/env python3
"""从现有 macOS iconset 素材转出 Windows .ico，供 PyInstaller / Inno Setup /
QSystemTrayIcon 静态占位图标复用。构建期一次性脚本，不进最终产物。

用法：python menubar/windows/make_ico.py
"""
import pathlib

from PIL import Image

_HERE = pathlib.Path(__file__).resolve().parent
_ICONSET = _HERE.parent / "build-icon" / "ai-limit.iconset"
_OUT = _HERE / "icon" / "ai-limit.ico"

_SIZES = [16, 32, 48, 256]
# Pillow 的 ICO writer 是从"调用 .save() 的那个图像对象"按 sizes 逐档缩小生成
# 各分辨率帧的，不是从 append_images 传入的多张不同分辨率原图里各取一张
# （踩坑记录：最初以 16x16 素材作为 base 调用，Pillow 因为"不放大超过原图"
# 静默把所有比 16x16 大的 size 都丢了，只写出一帧，产物只有 371 字节）。
# 改成用最大的 512x512 素材做 base，交给 Pillow 内部逐档缩小。
_BASE_SRC = _ICONSET / "icon_512x512.png"


def main():
    if not _BASE_SRC.exists():
        raise FileNotFoundError(f"missing source PNG: {_BASE_SRC}")
    base = Image.open(_BASE_SRC).convert("RGBA")
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    base.save(_OUT, format="ICO", sizes=[(s, s) for s in _SIZES])
    print(f"written: {_OUT} ({_OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
