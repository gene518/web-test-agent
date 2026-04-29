from __future__ import annotations

import unittest

from deep_agent.agent.master.models.intent import IntentClassification, build_extracted_params, compute_missing_params


class IntentModelTestCase(unittest.TestCase):
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
