from __future__ import annotations

import json
import unittest
from pathlib import Path


class StartScriptEncodingTestCase(unittest.TestCase):
    def test_windows_start_script_is_pure_ascii(self) -> None:
        """Windows 启动入口改为 polyglot bat 文件后，需要保证 bat 头部纯 ASCII。

        作用：cmd.exe 在中文 Windows 上默认用 GBK 读取批处理文件。如果脚本头部混入
        任何非 ASCII 字节（比如 UTF-8 BOM 或中文注释），cmd 会把它当成命令来解析，
        出现"不是内部或外部命令"之类的报错。

        注意 polyglot 设计：`:POWERSHELL_SECTION` 标记行之前由 cmd 解释执行，必须
        纯 ASCII；标记行之后是 PowerShell body，由 powershell 以 UTF-8 读回执行，
        允许中文注释和字符串。本测试只校验 cmd 段。
        """

        script_path = Path(__file__).resolve().parents[2] / "start" / "windows-start.bat"
        script_bytes = script_path.read_bytes()
        self.assertTrue(script_bytes, "windows-start.bat 不能为空。")
        self.assertFalse(
            script_bytes.startswith(b"\xef\xbb\xbf"),
            "windows-start.bat 不能带 UTF-8 BOM；cmd.exe 会把 BOM 字节当成命令解析。",
        )

        marker = b":POWERSHELL_SECTION"
        marker_index = script_bytes.find(marker)
        self.assertGreater(
            marker_index,
            0,
            "windows-start.bat 必须包含 `:POWERSHELL_SECTION` 分隔标记，polyglot 逻辑依赖该标记定位 PowerShell 段。",
        )

        cmd_section = script_bytes[:marker_index]
        non_ascii_indexes = [index for index, byte in enumerate(cmd_section) if byte > 0x7F]
        self.assertEqual(
            non_ascii_indexes,
            [],
            (
                "windows-start.bat 的 cmd 段（`:POWERSHELL_SECTION` 之前）必须保持纯 ASCII，"
                "发现非 ASCII 字节位于 "
                f"{non_ascii_indexes[:10]}。"
            ),
        )

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
