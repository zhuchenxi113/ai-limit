#!/usr/bin/env python3
"""生成正式 Windows 安装资产及供 v0.3.25 更新器使用的旧名兼容资产。"""
import argparse
import os
import pathlib
import re
import shutil
import sys

from sign_windows_update import DEFAULT_KEY_PATH, sign_installer


_CANONICAL_NAME_RE = re.compile(
    r"^ai-limit-windows-(?P<version>\d+\.\d+\.\d+)-setup\.exe$"
)


def prepare_release_assets(installer_path, key_path=DEFAULT_KEY_PATH,
                           signer=sign_installer):
    """签署正式资产，并生成内容相同、文件名独立签署的旧名兼容副本。"""
    installer = pathlib.Path(installer_path).resolve()
    match = _CANONICAL_NAME_RE.fullmatch(installer.name)
    if not installer.is_file():
        raise FileNotFoundError(f"安装包不存在：{installer}")
    if match is None:
        raise ValueError(
            "正式安装包必须命名为 "
            "ai-limit-windows-<version>-setup.exe"
        )

    canonical_signature, canonical_document = signer(installer, key_path)

    legacy_installer = installer.with_name(
        f"ai-limit-{match.group('version')}-setup.exe"
    )
    temp_installer = legacy_installer.with_name(legacy_installer.name + ".tmp")
    shutil.copy2(installer, temp_installer)
    os.replace(temp_installer, legacy_installer)
    legacy_signature, legacy_document = signer(legacy_installer, key_path)

    return (
        (installer, canonical_signature, canonical_document),
        (legacy_installer, legacy_signature, legacy_document),
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "installer", help="ai-limit-windows-<version>-setup.exe"
    )
    parser.add_argument("--key", default=str(DEFAULT_KEY_PATH))
    args = parser.parse_args()

    for installer, signature, document in prepare_release_assets(
        args.installer, args.key
    ):
        print(f"安装包：{installer}")
        print(f"签名：{signature}")
        print(f"公钥 ID：{document['key_id']}")
        print(f"SHA-256：{document['sha256']}")


if __name__ == "__main__":
    main()
