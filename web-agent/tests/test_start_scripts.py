from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


class StartScriptEncodingTestCase(unittest.TestCase):
    def test_macos_start_command_is_executable(self) -> None:
        script_path = Path(__file__).resolve().parents[2] / "start" / "macos-start.command"
        self.assertTrue(script_path.exists(), "macos-start.command 必须存在。")
        if os.name != "nt":
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
        self.assertNotIn("FrontendDir", script_text)
        self.assertIn("web-agent-client", script_text)

    def test_start_scripts_separate_user_client_and_internal_backend_modes(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        macos_script = (project_root / "start" / "macos-start.command").read_text(
            encoding="utf-8"
        )
        windows_script = (project_root / "start" / "windows-start.ps1").read_text(
            encoding="utf-8-sig"
        )

        self.assertNotIn("FRONTEND_DIR", macos_script)
        self.assertIn("web-agent-client", macos_script)
        self.assertIn("deep_agent/assets/demo", macos_script)
        self.assertIn("pnpm tauri dev", macos_script)
        self.assertIn("backend)", macos_script)
        self.assertIn('"backend" {', windows_script)
        self.assertIn('"tauri", "dev"', windows_script)
        self.assertIn("VsDevCmd.bat", windows_script)
        self.assertIn("LLVM Clang", windows_script)
        self.assertIn("CARGO_BUILD_TARGET", windows_script)
        self.assertIn('$ErrorActionPreference = "Continue"', windows_script)
        self.assertIn('$ScriptPath.StartsWith("\\\\?\\")', windows_script)

    def test_desktop_client_is_the_only_ui_project(self) -> None:
        project_root = Path(__file__).resolve().parents[2]

        self.assertTrue((project_root / "web-agent-client" / "package.json").is_file())

    def test_windows_portable_builder_contains_all_runtime_layers(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        builder = (project_root / "start" / "build-windows-x64-portable.ps1").read_text(
            encoding="utf-8"
        )

        for required_runtime in (
            "web-agent-client.exe",
            "WEB_TEST_AGENT_ENV_FILE",
            "WEB_TEST_AGENT_NODE_EXECUTABLE",
            "WEB_TEST_AGENT_PLAYWRIGHT_CLI",
            "PLAYWRIGHT_BROWSERS_PATH",
            "Microsoft.WebView2.FixedVersionRuntime",
            "Compress-Archive",
            "SHA256",
        ):
            self.assertIn(required_runtime, builder)
        self.assertIn('architecture = "x64"', builder)
        self.assertNotIn('architecture = "arm64"', builder)

    @unittest.skipUnless(shutil.which("git") and shutil.which("unzip"), "需要 git/unzip")
    def test_windows_migration_package_is_complete_and_excludes_runtime_data(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        source_script = project_root / "start" / "package-for-windows.command"
        source_helper = project_root / "start" / "package-for-windows.py"
        required_files = (
            "README.md",
            "start/README.md",
            "start/macos-start.command",
            "start/windows-start.ps1",
            "start/package-for-windows.command",
            "start/package-for-windows.py",
            "web-agent/.env",
            "web-agent/.env.example",
            "web-agent/langgraph.json",
            "web-agent/pyproject.toml",
            "web-agent/uv.lock",
            "web-agent/deep_agent/app.py",
            "web-agent/deep_agent/assets/demo/package.json",
            "web-agent-client/package.json",
            "web-agent-client/pnpm-lock.yaml",
            "web-agent-client/src/App.tsx",
            "web-agent-client/src-tauri/Cargo.toml",
            "web-agent-client/src-tauri/Cargo.lock",
            "web-agent-client/src-tauri/tauri.conf.json",
            "web-agent-client/src-tauri/src/main.rs",
            "web-agent-client/src-tauri/icons/icon.ico",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            fixture_root = temp_root / "web-test-agent-fixture"
            package_output = temp_root / "packages"
            for relative_path in required_files:
                target = fixture_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("fixture\n", encoding="utf-8")
            shutil.copy2(source_script, fixture_root / "start" / source_script.name)
            shutil.copy2(source_helper, fixture_root / "start" / source_helper.name)
            os.chmod(fixture_root / "start" / source_script.name, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            unicode_file = "doc/中文说明.md"
            unicode_path = fixture_root / unicode_file
            unicode_path.parent.mkdir(parents=True, exist_ok=True)
            unicode_path.write_text("中文文件名必须在 Windows 正确解压。\n", encoding="utf-8")

            subprocess.run(["git", "init", "-q"], cwd=fixture_root, check=True)
            subprocess.run(["git", "add", "."], cwd=fixture_root, check=True)

            excluded_files = (
                ".langgraph_api/state.pckl",
                "start/backend.log",
                "output/result.txt",
                "web-agent/.idea/workspace.xml",
                "web-agent/.venv/ignored.txt",
                "web-agent/.langgraph_api/state.pckl",
                "web-agent/runtime/session.json",
                "web-agent-client/node_modules/ignored.js",
                "web-agent-client/dist/index.html",
                "web-agent-client/src-tauri/target/debug/client.exe",
            )
            for relative_path in excluded_files:
                target = fixture_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("runtime\n", encoding="utf-8")

            env = {
                **os.environ,
                "PACKAGE_OUTPUT_DIR": str(package_output),
                "PACKAGE_NO_PAUSE": "1",
            }
            subprocess.run(
                ["bash", str(fixture_root / "start" / source_script.name)],
                cwd=fixture_root,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            archives = list(package_output.glob("*.zip"))
            self.assertEqual(len(archives), 1)
            prefix = f"{fixture_root.name}/"
            with zipfile.ZipFile(archives[0]) as archive:
                entries = {entry.filename: entry for entry in archive.infolist()}
            for relative_path in required_files:
                self.assertIn(f"{prefix}{relative_path}", entries)
            for relative_path in excluded_files:
                self.assertNotIn(f"{prefix}{relative_path}", entries)
            unicode_entry = entries[f"{prefix}{unicode_file}"]
            self.assertTrue(
                unicode_entry.flag_bits & 0x800,
                "中文文件名必须设置 ZIP UTF-8 标志，否则 Windows 会解压为乱码。",
            )

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
