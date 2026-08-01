#!/usr/bin/env python3
"""为 Windows Release 安装包生成相邻的 Ed25519 `.sig` 文件。"""
import argparse
import json
import os
import pathlib
import sys

import win32crypt

import update_signing


DEFAULT_KEY_PATH = (
    pathlib.Path.home() / ".ai-limit-release-signing" / "ed25519-private.dpapi"
)


def sign_installer(installer_path, key_path=DEFAULT_KEY_PATH):
    installer_path = pathlib.Path(installer_path).resolve()
    key_path = pathlib.Path(key_path).resolve()
    if not installer_path.is_file():
        raise FileNotFoundError(f"安装包不存在：{installer_path}")
    if not key_path.is_file():
        raise FileNotFoundError(f"发布私钥不存在：{key_path}")

    protected_key = key_path.read_bytes()
    private_key_der = win32crypt.CryptUnprotectData(
        protected_key, None, None, None, 0
    )[1]
    document = update_signing.build_signature_document(
        installer_path, private_key_der
    )

    signature_path = installer_path.with_name(installer_path.name + ".sig")
    temp_path = signature_path.with_suffix(signature_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, signature_path)
    update_signing.verify_detached_signature(
        installer_path, signature_path, installer_path.name
    )
    return signature_path, document


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", help="ai-limit-<version>-setup.exe")
    parser.add_argument("--key", default=str(DEFAULT_KEY_PATH))
    args = parser.parse_args()
    signature_path, document = sign_installer(args.installer, args.key)
    print(f"签名完成：{signature_path}")
    print(f"公钥 ID：{document['key_id']}")
    print(f"SHA-256：{document['sha256']}")


if __name__ == "__main__":
    main()
