from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


def _load_package_windows_zip_module():
    module_path = Path(__file__).resolve().parents[2] / "package_windows_zip.py"
    spec = importlib.util.spec_from_file_location("package_windows_zip", module_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WindowsPackageZipTestCase(unittest.TestCase):
    def test_packaged_powershell_script_uses_utf8_bom_and_crlf(self) -> None:
        package_windows_zip = _load_package_windows_zip_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "bundle.zip"
            package_windows_zip.build_zip(output_path)

            with ZipFile(output_path) as archive:
                script_bytes = archive.read("start/script/windows-start.ps1")

        self.assertTrue(
            script_bytes.startswith(b"\xef\xbb\xbf"),
            "The packaged Windows launcher must keep a UTF-8 BOM for Windows PowerShell 5.1.",
        )
        self.assertIn(
            b"\r\n",
            script_bytes,
            "The packaged Windows launcher must use CRLF line endings.",
        )
        self.assertNotIn(b"\n", script_bytes.replace(b"\r\n", b""))
