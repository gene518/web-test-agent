from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from deep_agent.core.config import load_project_env_file


class ProjectEnvLoadingTestCase(unittest.TestCase):
    def test_load_project_env_file_reads_utf8_and_preserves_existing_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "# 中文注释，回归 Windows 默认 GBK 读取失败场景\n"
                "LOG_LEVEL=DEBUG\n"
                "CUSTOM_LABEL=中文值\n"
                "QUOTED_VALUE=\"kept\"\n",
                encoding="utf-8",
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
