from __future__ import annotations

from deep_agent.helpers.artifacts import build_final_turn_summary, build_stage_summary


def _plan_artifact() -> dict[str, object]:
    return {
        "artifact_id": "plan-1",
        "project_dir": "/tmp/demo",
        "output_files": ["test_case/aaaplanning_demo/aaa_demo.md"],
        "planned_test_case_files": ["test_case/aaaplanning_demo/login.spec.ts"],
        "saved_test_cases": [],
    }


def test_single_stage_success_keeps_optional_follow_up() -> None:
    summary = build_stage_summary(
        stage="plan",
        status="success",
        artifact=_plan_artifact(),
    )

    assert "可选后续操作" in summary["text"]
    assert "下一阶段建议输入" not in summary["text"]


def test_pipeline_stage_success_omits_follow_up() -> None:
    summary = build_stage_summary(
        stage="plan",
        status="success",
        artifact=_plan_artifact(),
        include_follow_up=False,
    )

    assert "可选后续操作" not in summary["text"]
    assert "下一阶段建议输入" not in summary["text"]


def test_failure_summary_focuses_on_reason_and_current_stage_retry() -> None:
    summary = build_stage_summary(
        stage="plan",
        status="failure",
        artifact=None,
        fallback_message="页面无法访问。",
    )

    assert "页面无法访问" in summary["text"]
    assert "重新执行当前阶段" in summary["text"]
    assert "下一阶段" not in summary["text"]
    assert "可选后续操作" not in summary["text"]


def test_multi_stage_success_removes_legacy_hints_and_marks_request_complete() -> None:
    result = build_final_turn_summary(
        [
            {
                "stage": "plan",
                "status": "success",
                "text": "**Plan 阶段**\n- 状态：成功\n- 下一阶段建议输入：继续生成脚本",
            },
            {
                "stage": "generator",
                "status": "success",
                "text": "**Generator 阶段**\n- 状态：成功\n- 可选后续操作：继续调试",
            },
        ]
    )

    assert "下一阶段建议输入" not in result
    assert "可选后续操作" not in result
    assert "当前请求已完成，无需补充信息" in result


def test_multi_stage_failure_does_not_claim_completion() -> None:
    result = build_final_turn_summary(
        [
            {"stage": "plan", "status": "success", "text": "Plan 成功"},
            {
                "stage": "generator",
                "status": "failure",
                "text": "Generator 失败：文件不存在",
            },
        ]
    )

    assert "当前请求未完整完成" in result
    assert "无需补充信息" not in result
