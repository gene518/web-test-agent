from __future__ import annotations

import json
import unittest
from pathlib import Path


class StartScriptEncodingTestCase(unittest.TestCase):
    def test_windows_start_script_uses_utf8_bom(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "start" / "script" / "windows-start.ps1"
        script_bytes = script_path.read_bytes()

        self.assertTrue(
            script_bytes.startswith(b"\xef\xbb\xbf"),
            "windows-start.ps1 must keep a UTF-8 BOM so Windows PowerShell 5.1 reads Chinese text correctly.",
        )
        decoded = script_bytes.decode("utf-8-sig")
        self.assertIn("Write-SetupLog", decoded)

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
