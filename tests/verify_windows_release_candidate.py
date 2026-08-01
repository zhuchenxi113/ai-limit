"""Verify the final Windows installer and its failure-closed update gates."""

import argparse
import json
import pathlib
import shutil
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import updater_win


def _expect_rejection(installer: pathlib.Path, signature: pathlib.Path,
                      version: str, asset_name: str, expected_reason: str) -> None:
    try:
        updater_win.verify_installer(installer, signature, version, asset_name)
    except updater_win.UpdateFailed as error:
        if error.reason != expected_reason:
            raise AssertionError(
                f"expected {expected_reason}, got {error.reason}: {error}"
            ) from error
        return
    raise AssertionError(f"expected rejection: {expected_reason}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("installer", type=pathlib.Path)
    parser.add_argument("signature", type=pathlib.Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    installer = args.installer.resolve()
    signature = args.signature.resolve()
    expected_name = installer.name

    trust = updater_win.verify_installer(
        installer, signature, args.version, expected_name
    )
    if trust not in ("signed", "unsigned"):
        raise AssertionError(f"unexpected trust result: {trust}")

    with tempfile.TemporaryDirectory(prefix="ai-limit-release-verify-") as temp:
        temp_dir = pathlib.Path(temp)

        _expect_rejection(
            installer,
            temp_dir / "missing.sig",
            args.version,
            expected_name,
            "update_signature_invalid",
        )

        tampered_installer = temp_dir / expected_name
        shutil.copy2(installer, tampered_installer)
        with tampered_installer.open("r+b") as file:
            file.seek(-1, 2)
            last = file.read(1)
            file.seek(-1, 2)
            file.write(bytes([last[0] ^ 0x01]))
        _expect_rejection(
            tampered_installer,
            signature,
            args.version,
            expected_name,
            "update_signature_invalid",
        )

        bad_document = json.loads(signature.read_text(encoding="utf-8"))
        encoded = bad_document["signature"]
        bad_document["signature"] = ("A" if encoded[0] != "A" else "B") + encoded[1:]
        bad_signature = temp_dir / "wrong.sig"
        bad_signature.write_text(
            json.dumps(bad_document, ensure_ascii=False), encoding="utf-8"
        )
        _expect_rejection(
            installer,
            bad_signature,
            args.version,
            expected_name,
            "update_signature_invalid",
        )

        version_parts = args.version.split(".")
        wrong_version = ".".join(version_parts[:-1] + [str(int(version_parts[-1]) + 1)])
        _expect_rejection(
            installer,
            signature,
            wrong_version,
            expected_name,
            "version_mismatch",
        )

    print(f"valid={trust}")
    print("missing_signature=reject")
    print("tampered_installer=reject")
    print("wrong_signature=reject")
    print("wrong_version=reject")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
