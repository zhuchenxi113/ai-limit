"""AI Limit Windows 更新包的 Ed25519 签名协议。

签名对象不是含糊的“某个哈希”，而是固定协议版本、安装包精确文件名和
SHA-256 内容摘要组成的规范消息。这样签名不能被挪给另一个文件名或版本复用。
"""
import base64
import hashlib
import json
import pathlib
import re

from Cryptodome.PublicKey import ECC
from Cryptodome.Signature import eddsa


SIGNATURE_SCHEMA = "ai-limit-windows-update-signature-v1"
ALGORITHM = "Ed25519"
PUBLIC_KEY_DER_BASE64 = "MCowBQYDK2VwAyEAl/7e/7yl25xRYILWiwnUmA2I3ZeVyyo0SjLrQOFVmfo="
PUBLIC_KEY_DER = base64.b64decode(PUBLIC_KEY_DER_BASE64)
KEY_ID = hashlib.sha256(PUBLIC_KEY_DER).hexdigest()[:16]


class SignatureError(Exception):
    pass


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def signature_message(asset_name: str, sha256_hex: str) -> bytes:
    return (
        f"{SIGNATURE_SCHEMA}\n"
        f"{asset_name}\n"
        f"{sha256_hex.lower()}\n"
    ).encode("utf-8")


def build_signature_document(installer_path, private_key_der: bytes) -> dict:
    installer_path = pathlib.Path(installer_path)
    private_key = ECC.import_key(private_key_der)
    public_der = private_key.public_key().export_key(format="DER")
    if public_der != PUBLIC_KEY_DER:
        raise SignatureError("私钥与应用内置公钥不匹配")
    sha256_hex = file_sha256(installer_path)
    signature = eddsa.new(private_key, mode="rfc8032").sign(
        signature_message(installer_path.name, sha256_hex)
    )
    return {
        "schema": SIGNATURE_SCHEMA,
        "algorithm": ALGORITHM,
        "key_id": KEY_ID,
        "file": installer_path.name,
        "sha256": sha256_hex,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def verify_signature_document(installer_path, document: dict,
                              expected_asset_name: str) -> None:
    if not isinstance(document, dict):
        raise SignatureError("签名文件不是 JSON 对象")
    expected_fields = {
        "schema", "algorithm", "key_id", "file", "sha256", "signature"
    }
    if set(document) != expected_fields:
        raise SignatureError("签名文件字段不完整或包含未知字段")
    if not all(isinstance(document[field], str) for field in expected_fields):
        raise SignatureError("签名文件字段类型无效")
    if document["schema"] != SIGNATURE_SCHEMA:
        raise SignatureError("签名协议版本不支持")
    if document["algorithm"] != ALGORITHM:
        raise SignatureError("签名算法不支持")
    if document["key_id"] != KEY_ID:
        raise SignatureError("签名公钥 ID 不匹配")
    if document["file"] != expected_asset_name:
        raise SignatureError("签名绑定的安装包文件名不匹配")
    if re.fullmatch(r"[0-9a-fA-F]{64}", document["sha256"]) is None:
        raise SignatureError("签名文件中的 SHA-256 格式无效")

    actual_sha256 = file_sha256(installer_path)
    if document["sha256"].lower() != actual_sha256:
        raise SignatureError("安装包 SHA-256 与签名文件不匹配")
    try:
        signature = base64.b64decode(document["signature"], validate=True)
        if len(signature) != 64:
            raise ValueError("Ed25519 signature must be 64 bytes")
        public_key = ECC.import_key(PUBLIC_KEY_DER)
        eddsa.new(public_key, mode="rfc8032").verify(
            signature_message(expected_asset_name, actual_sha256),
            signature,
        )
    except (ValueError, TypeError) as e:
        raise SignatureError("Ed25519 签名无效") from e


def verify_detached_signature(installer_path, signature_path,
                              expected_asset_name: str) -> None:
    try:
        document = json.loads(
            pathlib.Path(signature_path).read_text(encoding="utf-8")
        )
    except Exception as e:
        raise SignatureError(f"无法读取签名文件：{e}") from e
    verify_signature_document(installer_path, document, expected_asset_name)
