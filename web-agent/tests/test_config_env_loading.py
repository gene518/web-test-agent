from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from deep_agent.core.config import (
    AppSettings,
    _default_relative_path_root,
    _discover_default_env_file,
    get_settings,
    load_project_env_file,
)
from deep_agent.model.errors import ModelConfigurationError


class ProjectEnvLoadingTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()

    def test_runtime_only_settings_skip_model_diagnostics(self) -> None:
        settings = AppSettings(_env_file=None)
        with (
            patch("deep_agent.core.config.AppSettings", return_value=settings),
            patch("deep_agent.core.config.load_project_env_file"),
            patch("deep_agent.core.config.configure_logging_from_env"),
            patch("deep_agent.core.config.configure_logging"),
            patch("deep_agent.model.diagnostics.collect_model_diagnostics") as diagnostics,
        ):
            self.assertIs(get_settings(validate_models=False), settings)
        diagnostics.assert_not_called()

    def test_portable_layout_discovers_package_config_without_injected_env(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir) / "Web-Test-Agent-Windows-x64"
            app_root = package_root / "runtime" / "app"
            env_file = package_root / "config" / ".env"
            app_root.mkdir(parents=True)
            env_file.parent.mkdir(parents=True)
            env_file.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")

            resolved = _discover_default_env_file(app_root, None)

        self.assertEqual(resolved, env_file.resolve())

    def test_relative_path_root_uses_source_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "web-agent"
            env_file = project_root / ".env"

            resolved = _default_relative_path_root(project_root, env_file)

        self.assertEqual(resolved, project_root.resolve())

    def test_relative_path_root_uses_portable_package_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = Path(temp_dir) / "Web-Test-Agent-Windows-x64"
            app_root = package_root / "runtime" / "app"
            env_file = package_root / "config" / ".env"

            resolved = _default_relative_path_root(app_root, env_file)

        self.assertEqual(resolved, package_root.resolve())

    def test_nested_role_configs_load_from_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "MASTER_LLM__FAMILY=qwen\n"
                "MASTER_LLM__CHANNEL=dashscope_openai\n"
                "MASTER_LLM__MODEL=qwen3.5-plus\n"
                "MASTER_LLM__API_KEY=master-key\n"
                "MASTER_LLM__BASE_URL=https://master.example.test/v1\n"
                "MASTER_LLM__THINKING=disabled\n"
                "SPECIALIST_LLM__FAMILY=minimax\n"
                "SPECIALIST_LLM__CHANNEL=minimax_anthropic\n"
                "SPECIALIST_LLM__MODEL=MiniMax-M2.7\n"
                "SPECIALIST_LLM__API_KEY=specialist-key\n"
                "SPECIALIST_LLM__BASE_URL=https://specialist.example.test/anthropic\n"
                "SPECIALIST_LLM__THINKING=enabled\n",
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                settings = AppSettings(_env_file=env_path)

        master = settings.resolve_model_connection("master")
        specialist = settings.resolve_model_connection("specialist")
        self.assertEqual(
            (
                master.family,
                master.channel,
                master.api_model_name,
                master.api_key,
                master.base_url,
                master.thinking,
            ),
            (
                "qwen",
                "dashscope_openai",
                "qwen3.5-plus",
                "master-key",
                "https://master.example.test/v1",
                "disabled",
            ),
        )
        self.assertEqual(
            (
                specialist.family,
                specialist.channel,
                specialist.api_model_name,
                specialist.api_key,
                specialist.base_url,
                specialist.thinking,
            ),
            (
                "minimax",
                "minimax_anthropic",
                "MiniMax-M2.7",
                "specialist-key",
                "https://specialist.example.test/anthropic",
                "enabled",
            ),
        )

    def test_role_model_connections_are_isolated(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "generic",
                "channel": "generic_openai",
                "model": "master-model",
                "api_key": "master-key",
                "base_url": "https://master.example.test/v1",
                "thinking": "disabled",
            },
            specialist_llm={
                "family": "generic",
                "channel": "generic_anthropic",
                "model": "specialist-model",
                "api_key": "specialist-key",
                "base_url": "https://specialist.example.test/v1",
                "thinking": "enabled",
            },
        )

        master = settings.resolve_model_connection("master")
        specialist = settings.resolve_model_connection("specialist")

        self.assertEqual(
            (master.api_model_name, master.api_key, master.base_url, master.protocol),
            (
                "master-model",
                "master-key",
                "https://master.example.test/v1",
                "openai",
            ),
        )
        self.assertEqual(
            (
                specialist.api_model_name,
                specialist.api_key,
                specialist.base_url,
                specialist.protocol,
            ),
            (
                "specialist-model",
                "specialist-key",
                "https://specialist.example.test/v1",
                "anthropic",
            ),
        )

    def test_missing_required_role_fields_raise_configuration_error(self) -> None:
        invalid_configs = {
            "family": {"channel": "generic_openai", "model": "custom-model"},
            "channel": {"family": "generic", "model": "custom-model"},
            "model": {
                "family": "generic",
                "channel": "generic_openai",
                "model": " ",
            },
        }

        for missing_field, role_config in invalid_configs.items():
            with self.subTest(missing_field=missing_field):
                settings = AppSettings(_env_file=None, master_llm=role_config)
                with self.assertRaises(ModelConfigurationError):
                    settings.resolve_model_connection("master")

    def test_empty_api_key_and_base_url_are_allowed(self) -> None:
        settings = AppSettings(
            _env_file=None,
            master_llm={
                "family": "generic",
                "channel": "generic_openai",
                "model": "custom-model",
                "api_key": " ",
                "base_url": "",
            },
        )

        connection = settings.resolve_model_connection("master")

        self.assertIsNone(connection.api_key)
        self.assertIsNone(connection.base_url)
        self.assertEqual(connection.thinking, "auto")

    def test_model_id_rejects_provider_prefix(self) -> None:
        for model_name in ("openai:gpt-5.6-terra", "anthropic:claude-sonnet"):
            with self.subTest(model_name=model_name):
                settings = AppSettings(
                    _env_file=None,
                    master_llm={
                        "family": "generic",
                        "channel": "generic_openai",
                        "model": model_name,
                    },
                )

                with self.assertRaises(ModelConfigurationError):
                    settings.resolve_model_connection("master")

    def test_role_model_config_rejects_removed_override_fields(self) -> None:
        removed_fields = {
            "reasoning_effort": "max",
            "context_window": 100_000,
            "max_output_tokens": 10_000,
            "timeout_seconds": 30,
            "max_retries": 5,
            "stream_chunk_timeout_seconds": 15,
        }

        for field_name, value in removed_fields.items():
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    AppSettings(
                        _env_file=None,
                        master_llm={
                            "family": "generic",
                            "channel": "generic_openai",
                            "model": "custom-model",
                            field_name: value,
                        },
                    )

    def test_load_project_env_file_reads_utf8_and_preserves_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "# 中文注释，回归 Windows 默认 GBK 读取失败场景\n"
                "LOG_LEVEL=DEBUG\n"
                "CUSTOM_LABEL=中文值\n"
                'QUOTED_VALUE="kept"\n',
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
