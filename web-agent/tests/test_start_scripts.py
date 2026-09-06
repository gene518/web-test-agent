from __future__ import annotations

import json
import os
import re
import stat
import unittest
from pathlib import Path


class StartScriptEncodingTestCase(unittest.TestCase):
    def test_macos_start_command_is_executable(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[2]
            / "start"
            / "desktop"
            / "macos-start.command"
        )
        self.assertTrue(script_path.exists(), "macos-start.command 必须存在。")
        if os.name != "nt":
            self.assertTrue(
                script_path.stat().st_mode & stat.S_IXUSR,
                "macos-start.command 必须带用户执行权限，否则 Finder 双击会提示没有访问权限。",
            )

    def test_windows_start_script_is_powershell_entry(self) -> None:
        script_path = (
            Path(__file__).resolve().parents[2]
            / "start"
            / "desktop"
            / "windows-start.ps1"
        )
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

    def test_start_scripts_separate_user_client_and_internal_backend_modes(
        self,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        macos_script = (
            project_root / "start" / "desktop" / "macos-start.command"
        ).read_text(encoding="utf-8")
        windows_script = (
            project_root / "start" / "desktop" / "windows-start.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertNotIn("FRONTEND_DIR", macos_script)
        self.assertIn("web-agent-client", macos_script)
        self.assertIn('PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"', macos_script)
        self.assertIn('BACKEND_LOG_FILE="$START_DIR/logs/backend.log"', macos_script)
        self.assertIn("deep_agent/assets/demo", macos_script)
        self.assertIn("pnpm tauri dev", macos_script)
        self.assertIn("stop_existing_client_vite_server", macos_script)
        self.assertIn("is_client_vite_listener", macos_script)
        self.assertIn("CLIENT_DEV_PORT=1420", macos_script)
        self.assertIn("backend)", macos_script)
        self.assertIn('"backend" {', windows_script)
        self.assertIn('"tauri", "dev"', windows_script)
        self.assertIn("VsDevCmd.bat", windows_script)
        self.assertIn("LLVM Clang", windows_script)
        self.assertIn("CARGO_BUILD_TARGET", windows_script)
        self.assertIn('$ErrorActionPreference = "Continue"', windows_script)
        self.assertIn('$ScriptPath.StartsWith("\\\\?\\")', windows_script)
        self.assertIn(
            "$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)",
            windows_script,
        )
        self.assertIn('Join-Path $ScriptDir "logs\\backend.log"', windows_script)

    def test_backend_start_commands_enable_configurable_thread_concurrency(
        self,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        macos_script = (
            project_root / "start" / "desktop" / "macos-start.command"
        ).read_text(encoding="utf-8")
        windows_script = (
            project_root / "start" / "desktop" / "windows-start.ps1"
        ).read_text(encoding="utf-8-sig")
        portable_builder = (
            project_root / "start" / "desktop" / "build-windows-x64-portable.ps1"
        ).read_text(encoding="utf-8-sig")

        self.assertIn("BACKEND_JOBS_PER_WORKER=4", macos_script)
        self.assertIn("^[1-9][0-9]*$", macos_script)
        self.assertIn('--n-jobs-per-worker "$BACKEND_JOBS_PER_WORKER"', macos_script)
        self.assertIn("$script:BackendJobsPerWorker = 4", windows_script)
        self.assertIn("[int]::TryParse", windows_script)
        self.assertIn(
            '"--n-jobs-per-worker", $script:BackendJobsPerWorker', windows_script
        )
        self.assertIn('"--n-jobs-per-worker", "4"', portable_builder)

    def test_desktop_client_is_the_only_ui_project(self) -> None:
        project_root = Path(__file__).resolve().parents[2]

        self.assertTrue((project_root / "web-agent-client" / "package.json").is_file())

    def test_windows_portable_builder_contains_all_runtime_layers(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        builder_path = (
            project_root / "start" / "desktop" / "build-windows-x64-portable.ps1"
        )
        builder_bytes = builder_path.read_bytes()
        self.assertTrue(
            builder_bytes.startswith(b"\xef\xbb\xbf"),
            "便携构建脚本包含中文 README，必须使用 UTF-8 BOM 供 Windows PowerShell 正确解码。",
        )
        builder = builder_bytes.decode("utf-8-sig")

        for required_runtime in (
            "web-agent-client.exe",
            "README.txt",
            "WEB_TEST_AGENT_ENV_FILE",
            "WEB_TEST_AGENT_NODE_EXECUTABLE",
            "WEB_TEST_AGENT_PLAYWRIGHT_CLI",
            "PLAYWRIGHT_BROWSERS_PATH",
            "Compress-Archive",
            "SHA256",
        ):
            self.assertIn(required_runtime, builder)
        self.assertIn('webview2 = "system-evergreen"', builder)
        self.assertNotIn("Microsoft.WebView2.FixedVersionRuntime", builder)
        self.assertNotIn("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", builder)
        self.assertIn('architecture = "x64"', builder)
        self.assertNotIn('architecture = "arm64"', builder)
        self.assertIn("MASTER_LLM__API_KEY", builder)
        self.assertIn("SPECIALIST_LLM__API_KEY", builder)

        tauri_entry = (
            project_root / "web-agent-client" / "src-tauri" / "src" / "lib.rs"
        ).read_text(encoding="utf-8")
        self.assertNotIn("WEBVIEW2_BROWSER_EXECUTABLE_FOLDER", tauri_entry)
        self.assertNotIn("runtime/webview2", tauri_entry)

    def test_model_template_contains_only_role_scoped_connection_fields(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        env_template = (project_root / "web-agent" / ".env.example").read_text(
            encoding="utf-8"
        )
        expected_fields = {
            f"{role}_LLM__{field}"
            for role in ("MASTER", "SPECIALIST")
            for field in (
                "FAMILY",
                "CHANNEL",
                "MODEL",
                "API_KEY",
                "BASE_URL",
                "THINKING",
            )
        }
        configured_fields = re.findall(
            r"^(?:MASTER|SPECIALIST)_LLM[^=]*(?==)", env_template, re.MULTILINE
        )

        self.assertEqual(set(configured_fields), expected_fields)
        self.assertEqual(len(configured_fields), len(expected_fields))

        macos_script = (
            project_root / "start" / "desktop" / "macos-start.command"
        ).read_text(encoding="utf-8")
        for role in ("MASTER", "SPECIALIST"):
            for field in ("FAMILY", "CHANNEL", "MODEL"):
                self.assertIn(
                    f'require_model_config "{role}_LLM__{field}"', macos_script
                )

    def test_start_directory_separates_desktop_and_container_entries(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        start_dir = project_root / "start"
        source_files = {
            path.name
            for path in start_dir.iterdir()
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix != ".log"
        }
        self.assertEqual(source_files, set())
        desktop_dir = start_dir / "desktop"
        container_dir = start_dir / "container"
        self.assertTrue(desktop_dir.is_dir())
        self.assertTrue(container_dir.is_dir())
        self.assertEqual(
            {path.name for path in desktop_dir.iterdir() if path.is_file()},
            {
                "build-windows-x64-portable.ps1",
                "macos-start.command",
                "windows-start.ps1",
            },
        )
        self.assertTrue((container_dir / "start-container.sh").is_file())
        self.assertTrue((container_dir / "README.md").is_file())
        container_script = (container_dir / "start-container.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("WEB_TEST_AGENT_CONTAINER_ENV_FILE", container_script)
        self.assertIn("WEB_TEST_AGENT_MODEL_ENV_FILE", container_script)
        self.assertIn('--env-file "$MODEL_ENV_FILE"', container_script)
        self.assertIn('--env-file "$DEPLOY_ENV_FILE"', container_script)
        self.assertIn(
            'print_service_group "核心运行组（Agent / Scheduler）"', container_script
        )
        self.assertIn('print_service_group "访问入口组（Web）"', container_script)
        self.assertNotIn("updater", container_script.lower())
        self.assertNotIn("docker compose pull", container_script)

    def test_langgraph_config_does_not_redeclare_env_file_loading(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "langgraph.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertNotIn(
            "env",
            config,
            "langgraph.json must not delegate .env loading to LangGraph CLI, because that path uses the OS default encoding on Windows.",
        )
