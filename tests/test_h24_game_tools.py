"""Release-facing contracts for the external game-tool installer."""
from pathlib import Path
import hashlib
import importlib.util
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "install_game_tools.py"
SPEC = importlib.util.spec_from_file_location("install_game_tools_under_test", MODULE_PATH)
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


class GameToolInstallerTests(unittest.TestCase):
    def test_witchy_download_is_pinned_to_official_release(self):
        self.assertEqual(INSTALLER.WITCHY_VERSION, "3.0.1.0")
        self.assertEqual(
            INSTALLER.WITCHY_RELEASE_URL,
            "https://github.com/ividyon/WitchyBND/releases/download/"
            "3.0.1.0/WitchyBND-3.0.1.0-win-x64.zip",
        )
        self.assertEqual(
            INSTALLER.WITCHY_ZIP_SHA256,
            "a3e6b2a0f7eac13f5e83b6602a1149322439c0662baa140ecdd84be28af50364",
        )

    def test_safe_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory(prefix="h24-witchy-zip-") as tmp:
            archive = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../WitchyBND.exe", b"not executable")
            with self.assertRaises(ValueError):
                INSTALLER.safe_extract_zip(archive, Path(tmp) / "out")

    def test_safe_extract_accepts_normal_archive(self):
        with tempfile.TemporaryDirectory(prefix="h24-witchy-zip-") as tmp:
            archive = Path(tmp) / "good.zip"
            payload = b"synthetic executable"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("WitchyBND.exe", payload)
            output = Path(tmp) / "out"
            INSTALLER.safe_extract_zip(archive, output)
            self.assertEqual(
                hashlib.sha256((output / "WitchyBND.exe").read_bytes()).hexdigest(),
                hashlib.sha256(payload).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
