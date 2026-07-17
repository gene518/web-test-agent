from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from deep_agent.core.config import (
    AppSettings,
    _discover_default_env_file,
    load_project_env_file,
)


class ProjectEnvLoadingTestCase(unittest.TestCase):
    def test_portable_layout_discovers_package_config_without_injected_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir) / "Web-Test-Agent-Windows-x64"
            app_root = package_root / "runtime" / "app"
            env_file = package_root / "config" / ".env"
            app_root.mkdir(parents=True)
            env_file.parent.mkdir(parents=True)
            env_file.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

            resolved = _discover_default_env_file(app_root, None)

        self.assertEqual(resolved, env_file.resolve())

    def test_empty_portable_template_model_names_use_defaults(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_model=" ",
            specialist_model="",
        )

        self.assertEqual(settings.master_model, "openai:gpt-4.1")
        self.assertEqual(settings.specialist_model, "openai:gpt-5.4")

    def test_flat_role_fields_load_without_nested_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "MASTER_LLM_MODEL=qwen-internal\n"
                "MASTER_LLM_API_KEY=master-key\n"
                "MASTER_LLM_BASE_URL=https://master.example.test/v1\n"
                "MASTER_LLM_THINKING=false\n"
                "SPECIALIST_LLM_MODEL=qwen-specialist\n"
                "SPECIALIST_LLM_API_KEY=specialist-key\n"
                "SPECIALIST_LLM_BASE_URL=https://specialist.example.test/v1\n"
                "SPECIALIST_LLM_THINKING=true\n",
                encoding="utf-8",
            )

            settings = AppSettings(_env_file=env_path)
            master_kwargs = settings.build_model_kwargs(settings.master_model, role="master")
            specialist_kwargs = settings.build_model_kwargs(
                settings.specialist_model,
                role="specialist",
            )

        self.assertEqual(settings.master_llm_base_url, "https://master.example.test/v1")
        self.assertEqual(master_kwargs["model"], "qwen-internal")
        self.assertEqual(master_kwargs["model_provider"], "openai")
        self.assertEqual(master_kwargs["api_key"], "master-key")
        self.assertEqual(master_kwargs["base_url"], "https://master.example.test/v1")
        self.assertEqual(master_kwargs["extra_body"], {"enable_thinking": False})
        self.assertFalse(master_kwargs["use_responses_api"])
        self.assertEqual(specialist_kwargs["model"], "qwen-specialist")
        self.assertEqual(specialist_kwargs["api_key"], "specialist-key")
        self.assertEqual(
            specialist_kwargs["base_url"],
            "https://specialist.example.test/v1",
        )
        self.assertEqual(specialist_kwargs["extra_body"], {"enable_thinking": True})

    def test_load_project_env_file_reads_utf8_and_preserves_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "# 中文注释，回归 Windows 默认 GBK 读取失败场景\n"
                "LOG_LEVEL=DEBUG\n"
                "CUSTOM_LABEL=中文值\n"
                "QUOTED_VALUE=\"kept\"\n",
                encoding="utf-8-sig",
            )

            previous_log_level = os.environ.get("LOG_LEVEL")
            previous_custom_label = os.environ.get("CUSTOM_LABEL")
            previous_quoted_value = os.environ.get("QUOTED_VALUE")
            os.environ["LOG_LEVEL"] = "WARNING"
            os.environ.pop("CUSTOM_LABEL", None)
            os.environ.pop("QUOTED_VALUE", None)
            try:
                load_project_env_file(env_path)
                self.assertEqual(os.environ["LOG_LEVEL"], "WARNING")
                self.assertEqual(os.environ["CUSTOM_LABEL"], "中文值")
                self.assertEqual(os.environ["QUOTED_VALUE"], "kept")
            finally:
                if previous_log_level is None:
                    os.environ.pop("LOG_LEVEL", None)
                else:
                    os.environ["LOG_LEVEL"] = previous_log_level

                if previous_custom_label is None:
                    os.environ.pop("CUSTOM_LABEL", None)
                else:
                    os.environ["CUSTOM_LABEL"] = previous_custom_label

                if previous_quoted_value is None:
                    os.environ.pop("QUOTED_VALUE", None)
                else:
                    os.environ["QUOTED_VALUE"] = previous_quoted_value
