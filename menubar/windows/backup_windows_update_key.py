#!/usr/bin/env python3
"""Export the machine-bound Windows release key as encrypted portable PEM.

The source file is protected by Windows DPAPI and can normally be decrypted
only by the same Windows user on the same machine. The exported PKCS#8 PEM is
password-protected and can be restored on another machine.
"""

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


DEFAULT_KEY_PATH = (
    pathlib.Path.home() / ".ai-limit-release-signing" / "ed25519-private.dpapi"
)
PROTECTION = "scryptAndAES256-CBC"
PROTECTION_PARAMS = {
    "iteration_count": 131072,
    "block_size": 8,
    "parallelization": 1,
}
COMPACT_PREFIX = "AI-LIMIT-ED25519-PKCS8-V1:"


def _validated_private_key(private_key_der: bytes):
    key = ECC.import_key(private_key_der)
    if not key.has_private():
        raise ValueError("源文件不包含 Ed25519 私钥")
    public_der = key.public_key().export_key(format="DER")
    if public_der != update_signing.PUBLIC_KEY_DER:
        raise ValueError(
            "源私钥与 AI Limit 内置发布公钥不匹配；拒绝生成错误备份"
        )
    return key


def export_encrypted_pem(private_key_der: bytes, passphrase: str) -> str:
    if len(passphrase) < 16:
        raise ValueError("备份密码至少需要 16 个字符")
    key = _validated_private_key(private_key_der)
    pem = key.export_key(
        format="PEM",
        passphrase=passphrase,
        use_pkcs8=True,
        protection=PROTECTION,
        prot_params=PROTECTION_PARAMS,
    )

    # Verify the encrypted output before it can be written anywhere.
    restored = ECC.import_key(pem, passphrase=passphrase)
    if (
        not restored.has_private()
        or restored.public_key().export_key(format="DER")
        != update_signing.PUBLIC_KEY_DER
    ):
        raise ValueError("加密备份回读校验失败")
    return pem


def compact_backup_from_pem(pem: str) -> str:
    body = "".join(
        line for line in pem.splitlines() if line and not line.startswith("-----")
    )
    try:
        encrypted_der = base64.b64decode(body, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("加密 PEM 的 Base64 内容无效") from exc
    return COMPACT_PREFIX + base64.b64encode(encrypted_der).decode("ascii")


def copy_text_to_clipboard(text: str) -> None:
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def backup_key(source_path, output_path, passphrase: str):
    source_path = pathlib.Path(source_path).resolve()
    output_path = pathlib.Path(output_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"DPAPI 私钥不存在：{source_path}")
    if output_path.exists():
        raise FileExistsError(f"目标文件已存在，拒绝覆盖：{output_path}")

    protected_key = source_path.read_bytes()
    private_key_der = win32crypt.CryptUnprotectData(
        protected_key, None, None, None, 0
    )[1]
    pem = export_encrypted_pem(private_key_der, passphrase)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with temp_path.open("x", encoding="ascii", newline="\n") as f:
            f.write(pem)
            if not pem.endswith("\n"):
                f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    return output_path


def _read_passphrase() -> str:
    first = getpass.getpass("输入备份密码（输入不会显示）：")
    second = getpass.getpass("再次输入备份密码：")
    if first != second:
        raise ValueError("两次输入的密码不一致")
    return first


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="把 Windows DPAPI 发布私钥导出为密码加密的跨机器 PEM"
    )
    parser.add_argument("output", help="新建的 .pem 备份路径（不会覆盖已有文件）")
    parser.add_argument("--source", default=str(DEFAULT_KEY_PATH))
    parser.add_argument(
        "--copy-compact",
        action="store_true",
        help="成功后把 Bitwarden 隐藏字段用的单行密文复制到剪贴板",
    )
    args = parser.parse_args()

    output = backup_key(args.source, args.output, _read_passphrase())
    print(f"备份完成：{output}")
    print(f"公钥 ID：{update_signing.KEY_ID}")
    print("已完成密码解密回读和公钥匹配校验。")
    if args.copy_compact:
        pem = output.read_text(encoding="ascii")
        copy_text_to_clipboard(compact_backup_from_pem(pem))
        print("Bitwarden 单行加密备份已复制到剪贴板。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"备份失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
