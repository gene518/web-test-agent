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

        script_text = script_bytes.decode("utf-8-sig")
        self.assertIn(
            "exec next start",
            script_text,
            "Windows 启动入口应运行生产前端，避免 next dev 在长任务期间占用额外资源。",
        )
        self.assertNotIn(
            "exec next dev",
            script_text,
            "Windows 启动入口不应继续运行开发服务器。",
        )
        self.assertIn(
            'Join-Path $FrontendDir ".next"',
            script_text,
            "生产构建前应清理旧 .next 产物，避免 Windows 复制冲突文件破坏构建。",
        )
        self.assertIn(
            'if ($ClientBackendOnly -ne "1")',
            script_text,
            "Windows 启动入口必须允许桌面客户端跳过 Web Portal 构建和启动。",
        )
        self.assertIn(
            '$env:CLIENT_BACKEND_ONLY',
            script_text,
            "Windows 启动入口必须读取 CLIENT_BACKEND_ONLY。",
        )

    def test_macos_start_script_supports_backend_only_mode(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "start" / "macos-start.command"
        script_text = script_path.read_text(encoding="utf-8")

        self.assertIn('CLIENT_BACKEND_ONLY="${CLIENT_BACKEND_ONLY:-0}"', script_text)
        self.assertIn('if [ "$CLIENT_BACKEND_ONLY" != "1" ]; then', script_text)
        self.assertIn(
            "客户端模式不会改用其他端口",
            script_text,
            "桌面客户端要求固定端口，不能静默切换到其他端口。",
        )

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
