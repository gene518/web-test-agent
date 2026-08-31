from __future__ import annotations

import unittest

from deep_agent.agent.master.models.intent import (
    IntentClassification,
    build_extracted_params,
    build_requested_pipeline,
    compute_missing_params,
    normalize_thread_title,
)


class IntentModelTestCase(unittest.TestCase):
    def test_thread_title_removes_model_decoration_and_caps_length(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            thread_title="  **“修复多会话并发。”**  ",
        )

        self.assertEqual(classification.thread_title, "修复多会话并发")
        self.assertEqual(normalize_thread_title("a" * 40), "a" * 32)

    def test_thread_title_rejects_empty_and_non_text_values(self) -> None:
        self.assertIsNone(IntentClassification(thread_title=" undefined ").thread_title)
        self.assertIsNone(normalize_thread_title({"title": "不可用"}))

    def test_plan_requires_project_name_and_url(self) -> None:
        classification = IntentClassification(intent_type="plan")
        self.assertEqual(compute_missing_params(classification), ["project_name", "url"])

        classification = IntentClassification(
            intent_type="plan",
            project_name="baidu-demo",
            url="https://example.com",
        )
        self.assertEqual(compute_missing_params(classification), [])

        classification = IntentClassification(
            intent_type="plan",
            url="https://example.com",
        )
        self.assertEqual(compute_missing_params(classification), ["project_name"])

    def test_build_extracted_params_includes_project_dir(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            project_name=" demo-project ",
            project_dir=" ~/demo-project ",
            url=" https://example.com ",
        )

        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_name": "demo-project",
                "project_dir": "~/demo-project",
                "url": "https://example.com",
            },
        )

    def test_generator_requires_project_identifier_and_test_plan_files(self) -> None:
        classification = IntentClassification(intent_type="generator")
        self.assertEqual(compute_missing_params(classification), ["project_name", "test_plan_files"])

        classification = IntentClassification(
            intent_type="generator",
            project_dir="~/demo-project",
            test_plan_files=[" test_case/demo/aaa_demo.md "],
        )
        self.assertEqual(compute_missing_params(classification), [])
        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_dir": "~/demo-project",
                "test_plan_files": ["test_case/demo/aaa_demo.md"],
            },
        )

    def test_healer_requires_project_identifier_and_test_scripts(self) -> None:
        classification = IntentClassification(intent_type="healer")
        self.assertEqual(compute_missing_params(classification), ["project_name", "test_scripts"])

        classification = IntentClassification(
            intent_type="healer",
            project_dir="~/demo-project",
            test_scripts=[" test_case/demo/a_case.spec.ts ", "undefined"],
        )
        self.assertEqual(compute_missing_params(classification), [])
        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_dir": "~/demo-project",
                "test_scripts": ["test_case/demo/a_case.spec.ts"],
            },
        )

    def test_scheduler_requires_project_identifier_and_schedule(self) -> None:
        classification = IntentClassification(intent_type="scheduler")
        self.assertEqual(compute_missing_params(classification), ["project_name", "schedule_cron"])

        classification = IntentClassification(
            intent_type="scheduler",
            project_dir="~/demo-project",
            schedule_task_id=" daily_smoke ",
            schedule_cron=" 0 9 * * * ",
            schedule_headed=False,
            schedule_enabled=True,
            schedule_locations=[" test_case/demo/a_case.spec.ts ", "undefined"],
        )
        self.assertEqual(compute_missing_params(classification), [])
        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_dir": "~/demo-project",
                "schedule_cron": "0 9 * * *",
                "schedule_headed": False,
                "schedule_enabled": True,
                "schedule_locations": ["test_case/demo/a_case.spec.ts"],
            },
        )

    def test_scheduler_ignores_user_supplied_task_id(self) -> None:
        classification = IntentClassification(
            intent_type="scheduler",
            project_dir="~/demo-project",
            schedule_task_id="user-defined-id",
            schedule_cron="0 9 * * *",
        )

        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_dir": "~/demo-project",
                "schedule_cron": "0 9 * * *",
            },
        )

    def test_optional_scheduler_booleans_treat_empty_strings_as_missing(self) -> None:
        classification = IntentClassification(
            intent_type="scheduler",
            schedule_headed="",
            schedule_enabled="  ",
        )

        self.assertIsNone(classification.schedule_headed)
        self.assertIsNone(classification.schedule_enabled)
        self.assertEqual(build_extracted_params(classification), {})

    def test_optional_scheduler_booleans_keep_valid_boolean_strings(self) -> None:
        classification = IntentClassification(
            intent_type="scheduler",
            schedule_headed="false",
            schedule_enabled="true",
        )

        self.assertIs(classification.schedule_headed, False)
        self.assertIs(classification.schedule_enabled, True)
        self.assertEqual(
            build_extracted_params(classification),
            {
                "schedule_headed": False,
                "schedule_enabled": True,
            },
        )

    def test_optional_scheduler_booleans_treat_null_like_strings_as_missing(self) -> None:
        classification = IntentClassification(
            intent_type="scheduler",
            schedule_headed="undefined",
            schedule_enabled="null",
        )

        self.assertIsNone(classification.schedule_headed)
        self.assertIsNone(classification.schedule_enabled)
        self.assertEqual(build_extracted_params(classification), {})

    def test_null_like_placeholders_are_treated_as_missing(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            project_name="null",
            url=" None ",
        )

        self.assertEqual(compute_missing_params(classification), ["project_name", "url"])
        self.assertEqual(build_extracted_params(classification), {})

    def test_url_value_keeps_model_output(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            project_name="demo-project",
            url="www.baidu.com",
        )

        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_name": "demo-project",
                "url": "www.baidu.com",
            },
        )
        self.assertEqual(compute_missing_params(classification), [])

    def test_generator_list_values_drop_null_like_entries(self) -> None:
        classification = IntentClassification(
            intent_type="generator",
            project_name="demo-project",
            test_plan_files=[" test_case/demo/aaa_demo.md ", "null", ""],
            test_cases=["  case-a  ", "undefined"],
        )

        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_name": "demo-project",
                "test_plan_files": ["test_case/demo/aaa_demo.md"],
                "test_cases": ["case-a"],
            },
        )

    def test_non_scheduler_requests_ignore_empty_scheduler_boolean_fields(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            project_name="demo-project",
            url="https://example.com",
            schedule_headed="",
            schedule_enabled="undefined",
        )

        self.assertEqual(compute_missing_params(classification), [])
        self.assertEqual(
            build_extracted_params(classification),
            {
                "project_name": "demo-project",
                "url": "https://example.com",
            },
        )

    def test_build_requested_pipeline_prefers_structured_output(self) -> None:
        classification = IntentClassification(
            intent_type="plan",
            requested_pipeline=["plan", "generator", "healer"],
        )

        self.assertEqual(
            build_requested_pipeline(classification, latest_user_request="先生成计划，再写脚本，再调试"),
            ["plan", "generator", "healer"],
        )

    def test_build_requested_pipeline_can_infer_multi_stage_from_user_text(self) -> None:
        classification = IntentClassification(intent_type="plan")

        self.assertEqual(
            build_requested_pipeline(classification, latest_user_request="先生成测试计划，再生成脚本，然后调试失败用例"),
            ["plan", "generator", "healer"],
        )

    def test_scheduler_path_containing_test_does_not_route_to_healer(self) -> None:
        classification = IntentClassification(intent_type="scheduler")

        self.assertEqual(
            build_requested_pipeline(
                classification,
                latest_user_request="/Users/jin/webautotest/yn 把这个项目设置为每天 03:20 执行一次",
            ),
            [],
        )
