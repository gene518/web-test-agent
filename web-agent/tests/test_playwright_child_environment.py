from __future__ import annotations

import unittest

from deep_agent.tools.playwright.runtime_environment import (
    build_playwright_child_environment,
)


class PlaywrightChildEnvironmentTestCase(unittest.TestCase):
    def test_allowlist_retains_runtime_settings_without_credentials(self) -> None:
        environment = build_playwright_child_environment(
            {"PW_SCHEDULE_TASK_ID": "nightly", "PW_TEST_REPORT_NAME": "report"},
            source={
                "PATH": "/usr/local/bin:/usr/bin",
                "HOME": "/tmp/home",
                "TMPDIR": "/tmp",
                "XDG_CACHE_HOME": "/tmp/cache",
                "LANG": "zh_CN.UTF-8",
                "NODE_OPTIONS": "--enable-source-maps",
                "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
                "PWTEST_HEADED": "0",
                "WEB_TEST_AGENT_NODE_EXECUTABLE": "/runtime/node",
                "MASTER_LLM__FAMILY": "generic",
                "MASTER_LLM__API_KEY": "master-secret",
                "SPECIALIST_LLM__MODEL": "gpt-5.6-terra",
                "SPECIALIST_LLM__API_KEY": "specialist-secret",
                "LANGSMITH_API_KEY": "langsmith-secret",
                "OPENAI_API_KEY": "openai-secret",
                "SCHEDULER_LANGGRAPH_API_KEY": "scheduler-secret",
                "GITHUB_TOKEN": "github-secret",
                "PWTEST_API_KEY": "test-secret",
                "PLAYWRIGHT_AUTH_TOKEN": "browser-secret",
                "UNRELATED_SETTING": "must-not-pass",
            },
        )

        self.assertEqual(environment["PATH"], "/usr/local/bin:/usr/bin")
        self.assertEqual(environment["HOME"], "/tmp/home")
        self.assertEqual(environment["TMPDIR"], "/tmp")
        self.assertEqual(environment["XDG_CACHE_HOME"], "/tmp/cache")
        self.assertEqual(environment["LANG"], "zh_CN.UTF-8")
        self.assertEqual(environment["NODE_OPTIONS"], "--enable-source-maps")
        self.assertEqual(environment["PLAYWRIGHT_BROWSERS_PATH"], "/ms-playwright")
        self.assertEqual(environment["PWTEST_HEADED"], "0")
        self.assertEqual(environment["WEB_TEST_AGENT_NODE_EXECUTABLE"], "/runtime/node")
        self.assertEqual(environment["PW_SCHEDULE_TASK_ID"], "nightly")
        self.assertEqual(environment["PW_TEST_REPORT_NAME"], "report")
        for key in (
            "MASTER_LLM__FAMILY",
            "MASTER_LLM__API_KEY",
            "SPECIALIST_LLM__MODEL",
            "SPECIALIST_LLM__API_KEY",
            "LANGSMITH_API_KEY",
            "OPENAI_API_KEY",
            "SCHEDULER_LANGGRAPH_API_KEY",
            "GITHUB_TOKEN",
            "PWTEST_API_KEY",
            "PLAYWRIGHT_AUTH_TOKEN",
            "UNRELATED_SETTING",
        ):
            self.assertNotIn(key, environment)

    def test_rejects_credential_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "不在白名单"):
            build_playwright_child_environment(
                {"SPECIALIST_LLM__API_KEY": "must-not-pass"}, source={}
            )
