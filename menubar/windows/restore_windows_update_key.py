#!/usr/bin/env python3
"""Verify or restore an encrypted AI Limit Windows release-key backup."""

import argparse
import base64
import getpass
import os
import pathlib
import sys

import win32crypt
import win32clipboard
from Cryptodome.PublicKey import ECC

import update_signing


DEFAULT_DESTINATION = (
    pathlib.Path.home() / ".ai-limit-release-signing" / "ed25519-private.dpapi"
)
PEM_BEGIN = "-----BEGIN ENCRYPTED PRIVATE KEY-----"
PEM_END = "-----END ENCRYPTED PRIVATE KEY-----"
COMPACT_PREFIX = "AI-LIMIT-ED25519-PKCS8-V1:"


def extract_encrypted_key(text: str):
    compact_at = text.find(COMPACT_PREFIX)
    if compact_at >= 0:
        encoded_at = compact_at + len(COMPACT_PREFIX)
        encoded = text[encoded_at:].split()[0]
        try:
            encrypted_der = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("单行备份的 Base64 内容无效") from exc
        if not encrypted_der:
            raise ValueError("单行备份内容为空")
        return encrypted_der

    begin = text.find(PEM_BEGIN)
    end = text.find(PEM_END, begin + len(PEM_BEGIN))
    if begin < 0 or end < 0:
        raise ValueError("找不到完整的加密 PEM 首尾标记")
    end += len(PEM_END)
    pem = text[begin:end]
    if text.find(PEM_BEGIN, begin + 1) >= 0 or text.find(PEM_END, end) >= 0:
        raise ValueError("备份中包含多个 PEM 私钥块")
    return pem + "\n"


def load_and_validate_text(text: str, passphrase: str):
    encrypted_key = extract_encrypted_key(text)
    try:
        key = ECC.import_key(encrypted_key, passphrase=passphrase)
    except (ValueError, IndexError, TypeError) as exc:
        raise ValueError("无法解密备份：密码错误或 PEM 内容已损坏") from exc
    if not key.has_private():
        raise ValueError("备份不包含 Ed25519 私钥")
    if key.public_key().export_key(format="DER") != update_signing.PUBLIC_KEY_DER:
        raise ValueError("备份私钥与 AI Limit 内置发布公钥不匹配")
    return key


def load_and_validate_backup(backup_path, passphrase: str):
    backup_path = pathlib.Path(backup_path).resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{backup_path}")
    return load_and_validate_text(
        backup_path.read_text(encoding="utf-8-sig"), passphrase
    )


def read_and_clear_clipboard() -> str:
    win32clipboard.OpenClipboard()
    try:
        if not win32clipboard.IsClipboardFormatAvailable(
            win32clipboard.CF_UNICODETEXT
        ):
            raise ValueError("剪贴板中没有文本")
        text = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("剪贴板文本为空")
        win32clipboard.EmptyClipboard()
        return text
    finally:
        win32clipboard.CloseClipboard()


def restore_backup(backup_path, destination, passphrase: str):
    destination = pathlib.Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(f"目标私钥已存在，拒绝覆盖：{destination}")
    key = load_and_validate_backup(backup_path, passphrase)
    private_der = key.export_key(format="DER", use_pkcs8=True)
    protected = win32crypt.CryptProtectData(
        private_der,
        "AI Limit Ed25519 release signing key",
        None,
        None,
        None,
        0,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    try:
        with temp_path.open("xb") as f:
            f.write(protected)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    # Ground-truth verification of the actual bytes written to disk.
    restored_der = win32crypt.CryptUnprotectData(
        destination.read_bytes(), None, None, None, 0
    )[1]
    restored = ECC.import_key(restored_der)
    if (
        not restored.has_private()
        or restored.public_key().export_key(format="DER")
        != update_signing.PUBLIC_KEY_DER
    ):
        raise ValueError("恢复后的 DPAPI 私钥回读校验失败")
    return destination


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="验证或恢复 AI Limit 的密码加密发布私钥备份"
    )
    parser.add_argument(
        "backup",
        nargs="?",
        help="从 Bitwarden 或离线介质取出的 PEM/文本文件",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="只验证密码、PEM 完整性和公钥 ID，不写入 DPAPI 私钥",
    )
    parser.add_argument("--destination", default=str(DEFAULT_DESTINATION))
    parser.add_argument(
        "--from-clipboard",
        action="store_true",
        help="暂停后读取并清空 Bitwarden 复制到剪贴板的隐藏字段",
    )
    args = parser.parse_args()

    if args.from_clipboard:
        if not args.verify_only:
            parser.error("--from-clipboard 只能与 --verify-only 一起使用")
        getpass.getpass(
            "现在去 Bitwarden 点击隐藏字段的复制按钮，回来后只按 Enter；"
            "此处输入始终隐藏："
        )
        clipboard_text = read_and_clear_clipboard()
        passphrase = getpass.getpass("输入备份密码（输入不会显示）：")
        load_and_validate_text(clipboard_text, passphrase)
        print("Bitwarden 隐藏字段备份验证通过。")
        print(f"公钥 ID：{update_signing.KEY_ID}")
        print("剪贴板已清空；未写入或覆盖任何 DPAPI 私钥。")
        return

    if not args.backup:
        parser.error("必须提供备份文件，或使用 --from-clipboard --verify-only")
    passphrase = getpass.getpass("输入备份密码（输入不会显示）：")
    if args.verify_only:
        load_and_validate_backup(args.backup, passphrase)
        print("备份验证通过。")
        print(f"公钥 ID：{update_signing.KEY_ID}")
        print("未写入或覆盖任何 DPAPI 私钥。")
    else:
        output = restore_backup(args.backup, args.destination, passphrase)
        print(f"恢复完成：{output}")
        print(f"公钥 ID：{update_signing.KEY_ID}")
        print("已完成 DPAPI 落盘回读和公钥匹配校验。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"恢复失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
