from __future__ import annotations

import json
import stat
import unittest
from pathlib import Path


class StartScriptEncodingTestCase(unittest.TestCase):
    def test_macos_start_command_is_executable(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "start" / "macos-start.command"
        self.assertTrue(script_path.exists(), "macos-start.command 必须存在。")
        self.assertTrue(
            script_path.stat().st_mode & stat.S_IXUSR,
            "macos-start.command 必须带用户执行权限，否则 Finder 双击会提示没有访问权限。",
        )

    def test_windows_start_script_is_powershell_entry(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "start" / "windows-start.ps1"
        script_bytes = script_path.read_bytes()
        self.assertTrue(script_bytes, "windows-start.ps1 不能为空。")
        self.assertTrue(
            script_bytes.startswith(b"\xef\xbb\xbf"),
            "windows-start.ps1 保持 UTF-8 BOM，便于 Windows PowerShell 正确读取中文提示。",
        )
        self.assertIn(
            b"param(",
            script_bytes,
            "windows-start.ps1 必须保留 PowerShell 参数入口。",
        )
        self.assertNotIn(
            b":POWERSHELL_SECTION",
            script_bytes,
            "windows-start.ps1 已是独立 PowerShell 入口，不应再包含旧 bat polyglot 标记。",
        )

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
