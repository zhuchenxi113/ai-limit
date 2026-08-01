import pathlib
import base64
import sys
import tempfile
import unittest
from unittest import mock

import win32crypt
from Cryptodome.PublicKey import ECC


WINDOWS_DIR = pathlib.Path(__file__).parents[1] / "menubar" / "windows"
sys.path.insert(0, str(WINDOWS_DIR))

import backup_windows_update_key
import restore_windows_update_key
import update_signing


class WindowsKeyBackupTests(unittest.TestCase):
    def test_exports_password_protected_portable_key(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")

        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )

        with self.assertRaises(ValueError):
            ECC.import_key(pem, passphrase="wrong password")
        restored = ECC.import_key(
            pem, passphrase="correct horse battery staple"
        )
        self.assertTrue(restored.has_private())
        self.assertEqual(
            restored.public_key().export_key(format="DER"), public_der
        )

    def test_rejects_short_password(self):
        with self.assertRaisesRegex(ValueError, "至少需要 16"):
            backup_windows_update_key.export_encrypted_pem(b"unused", "short")

    def test_rejects_key_that_does_not_match_embedded_public_key(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        with self.assertRaisesRegex(ValueError, "内置发布公钥不匹配"):
            backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )

    def test_refuses_to_overwrite_existing_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = pathlib.Path(temp_dir) / "source.dpapi"
            output = pathlib.Path(temp_dir) / "backup.pem"
            source.write_bytes(b"source")
            output.write_text("existing", encoding="ascii")
            with self.assertRaises(FileExistsError):
                backup_windows_update_key.backup_key(
                    source, output, "correct horse battery staple"
                )

    def test_dpapi_to_encrypted_pem_round_trip(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        protected = win32crypt.CryptProtectData(
            private_der, "AI Limit test key", None, None, None, 0
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            source = pathlib.Path(temp_dir) / "source.dpapi"
            output = pathlib.Path(temp_dir) / "backup.pem"
            source.write_bytes(protected)
            with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
                backup_windows_update_key.backup_key(
                    source, output, "correct horse battery staple"
                )

            pem = output.read_text(encoding="ascii")
            self.assertIn("BEGIN ENCRYPTED PRIVATE KEY", pem)
            restored = ECC.import_key(
                pem, passphrase="correct horse battery staple"
            )
            self.assertEqual(
                restored.public_key().export_key(format="DER"), public_der
            )

    def test_restore_accepts_bitwarden_note_around_pem(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                note = pathlib.Path(temp_dir) / "bitwarden-note.txt"
                note.write_text(
                    "用途：test\n公钥 ID：test\n\n" + pem + "\n",
                    encoding="utf-8",
                )
                restored = restore_windows_update_key.load_and_validate_backup(
                    note, "correct horse battery staple"
                )
        self.assertEqual(
            restored.public_key().export_key(format="DER"), public_der
        )

    def test_restore_accepts_compact_hidden_field_format(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )
            encrypted_der = base64.b64decode(
                "".join(
                    line
                    for line in pem.splitlines()
                    if not line.startswith("-----")
                )
            )
            compact = (
                restore_windows_update_key.COMPACT_PREFIX
                + base64.b64encode(encrypted_der).decode("ascii")
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                backup = pathlib.Path(temp_dir) / "hidden-field.txt"
                backup.write_text(compact, encoding="ascii")
                restored = restore_windows_update_key.load_and_validate_backup(
                    backup, "correct horse battery staple"
                )
        self.assertEqual(
            restored.public_key().export_key(format="DER"), public_der
        )

    def test_compact_backup_round_trip_preserves_encrypted_key(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )
            compact = backup_windows_update_key.compact_backup_from_pem(pem)
            restored = restore_windows_update_key.load_and_validate_text(
                compact, "correct horse battery staple"
            )
        self.assertEqual(
            restored.public_key().export_key(format="DER"), public_der
        )

    def test_restore_rejects_wrong_password(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                backup = pathlib.Path(temp_dir) / "backup.pem"
                backup.write_text(pem, encoding="ascii")
                with self.assertRaisesRegex(ValueError, "密码错误或 PEM 内容已损坏"):
                    restore_windows_update_key.load_and_validate_backup(
                        backup, "wrong password"
                    )

    def test_restore_writes_verified_dpapi_blob(self):
        private_key = ECC.generate(curve="Ed25519")
        private_der = private_key.export_key(format="DER", use_pkcs8=True)
        public_der = private_key.public_key().export_key(format="DER")
        with mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der):
            pem = backup_windows_update_key.export_encrypted_pem(
                private_der, "correct horse battery staple"
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                backup = pathlib.Path(temp_dir) / "backup.pem"
                destination = pathlib.Path(temp_dir) / "restored.dpapi"
                backup.write_text(pem, encoding="ascii")
                restore_windows_update_key.restore_backup(
                    backup, destination, "correct horse battery staple"
                )
                restored_der = win32crypt.CryptUnprotectData(
                    destination.read_bytes(), None, None, None, 0
                )[1]
                restored = ECC.import_key(restored_der)
        self.assertEqual(
            restored.public_key().export_key(format="DER"), public_der
        )


if __name__ == "__main__":
    unittest.main()
