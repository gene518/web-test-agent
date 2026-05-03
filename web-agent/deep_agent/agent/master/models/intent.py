"""Master 节点使用的结构化意图识别模型。"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, Field
from deep_agent.agent.artifacts import normalize_requested_pipeline


IntentType = Literal["plan", "generator", "healer", "scheduler", "general", "unknown"]
NULL_LIKE_TEXT_VALUES = frozenset({"null", "none", "nil", "undefined"})
SPECIALIST_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "plan": ("生成计划", "测试计划", "制定计划", "测试方案", "生成用例", "用例设计", "plan"),
    "generator": ("生成脚本", "写脚本", "写代码", "自动化脚本", "脚本生成", "按照计划生成", "generator"),
    "healer": ("调试", "修复", "失败", "报错", "排查问题", "heal", "fix", "test", "run test"),
}

# 这张表定义了每种意图至少要收集到哪些参数，后面追问逻辑会直接使用它。
REQUIRED_PARAMS_BY_INTENT: dict[str, tuple[str, ...]] = {
    "plan": ("project_name", "url"),
    "generator": ("test_plan_files",),
    "healer": ("test_scripts",),
    "scheduler": ("schedule_task_id",),
}


class IntentClassification(BaseModel):
    """描述 Master Agent 对单轮输入的结构化判断结果。

    该模型会被 LangChain 的 `with_structured_output` 直接使用，因此字段尽量保持
    扁平，便于序列化、调试和透传到 LangGraph 的 state 中。
    """

    intent_type: IntentType = Field(
        default="unknown",
        description="Master 识别出的任务类型，用于驱动后续 LangGraph 路由。",
    )
    project_name: str | None = Field(default=None, description="自动化工程名字；Plan 阶段必填。")
    project_dir: str | None = Field(default=None, description="自动化项目目录或工程目录。")
    url: str | None = Field(default=None, description="Plan 阶段需要的页面 URL。")
    feature_points: list[str] = Field(default_factory=list, description="Plan 阶段功能点列表。")
    test_plan_files: list[str] = Field(default_factory=list, description="Generator 阶段待消费的测试计划路径列表，可为文件或目录。")
    test_cases: list[str] = Field(default_factory=list, description="Generator 阶段测试用例列表。")
    test_scripts: list[str] = Field(default_factory=list, description="Healer 阶段待调试脚本路径列表，可为文件或目录。")
    schedule_task_id: str | None = Field(default=None, description="定时任务配置中已存在的任务 ID。")
    schedule_cron: str | None = Field(default=None, description="需要更新成的五段 Cron 表达式。")
    schedule_headed: bool | None = Field(default=None, description="需要更新成的浏览器模式；`true` 为有头，`false` 为无头。")
    schedule_enabled: bool | None = Field(default=None, description="需要更新成的启用状态；`true` 为启用，`false` 为禁用。")
    schedule_locations: list[str] = Field(default_factory=list, description="需要更新成的测试脚本或目录列表。")
    requested_pipeline: list[str] = Field(
        default_factory=list,
        description="本轮期望执行的 specialist 阶段链，按顺序返回，例如 ['plan', 'generator']。",
    )
    missing_params: list[str] = Field(default_factory=list, description="模型推断出的缺失参数，仅用于调试。")
    reasoning: str = Field(default="", description="本次分类的理由说明。")


def build_extracted_params(result: IntentClassification) -> dict[str, Any]:
    """从结构化结果中提取业务侧真正关心的参数。

    Args:
        result: Master 节点的结构化识别结果。

    Returns:
        dict[str, Any]: 仅保留非空参数的字典。

    Raises:
        None.
    """

    # 这里只保留已经成功识别出的非空字段，避免把空值继续传给后续节点。
    extracted: dict[str, Any] = {}
    project_name = _normalized_optional_text(result.project_name)
    if project_name:
        extracted["project_name"] = project_name

    project_dir = _normalized_optional_text(result.project_dir)
    if project_dir:
        extracted["project_dir"] = project_dir

    url = _normalized_optional_text(result.url)
    if url:
        extracted["url"] = url

    feature_points = _normalized_string_list(result.feature_points)
    if feature_points:
        extracted["feature_points"] = feature_points

    test_plan_files = _normalized_string_list(result.test_plan_files)
    if test_plan_files:
        extracted["test_plan_files"] = test_plan_files

    test_cases = _normalized_string_list(result.test_cases)
    if test_cases:
        extracted["test_cases"] = test_cases

    test_scripts = _normalized_string_list(result.test_scripts)
    if test_scripts:
        extracted["test_scripts"] = test_scripts

    schedule_task_id = _normalized_optional_text(result.schedule_task_id)
    if schedule_task_id:
        extracted["schedule_task_id"] = schedule_task_id

    schedule_cron = _normalized_optional_text(result.schedule_cron)
    if schedule_cron:
        extracted["schedule_cron"] = schedule_cron

    if result.schedule_headed is not None:
        extracted["schedule_headed"] = result.schedule_headed

    if result.schedule_enabled is not None:
        extracted["schedule_enabled"] = result.schedule_enabled

    schedule_locations = _normalized_string_list(result.schedule_locations)
    if schedule_locations:
        extracted["schedule_locations"] = schedule_locations
    return extracted


def compute_missing_params(result: IntentClassification) -> list[str]:
    """根据目标意图重新计算缺失参数。

    这里不完全信任模型返回的 `missing_params`，而是用规则再次校验一次，
    让追问逻辑更稳定、可预测。

    Args:
        result: Master 节点的结构化识别结果。

    Returns:
        list[str]: 当前目标 Agent 仍然缺失的必要参数名列表。

    Raises:
        None.
    """

    return compute_missing_params_for_intent(
        result.intent_type,
        {
            "project_name": result.project_name,
            "project_dir": result.project_dir,
            "url": result.url,
            "feature_points": result.feature_points,
            "test_plan_files": result.test_plan_files,
            "test_cases": result.test_cases,
            "test_scripts": result.test_scripts,
            "schedule_task_id": result.schedule_task_id,
            "schedule_cron": result.schedule_cron,
            "schedule_headed": result.schedule_headed,
            "schedule_enabled": result.schedule_enabled,
            "schedule_locations": result.schedule_locations,
        },
    )


def compute_missing_params_for_intent(intent_type: str, params: Mapping[str, Any]) -> list[str]:
    """根据指定意图和参数字典计算缺失字段。"""

    if intent_type in {"generator", "healer", "scheduler"}:
        missing: list[str] = []
        project_name = _normalized_optional_text(_as_optional_text(params.get("project_name")))
        project_dir = _normalized_optional_text(_as_optional_text(params.get("project_dir")))
        if not project_name and not project_dir:
            missing.append("project_name")

        if intent_type == "scheduler":
            task_id = _normalized_optional_text(_as_optional_text(params.get("schedule_task_id")))
            if not task_id:
                missing.append("schedule_task_id")
            return missing

        list_field_name = "test_plan_files" if intent_type == "generator" else "test_scripts"
        normalized_list = _normalized_string_list(params.get(list_field_name))
        if not normalized_list:
            missing.append(list_field_name)
        return missing

    required_params = REQUIRED_PARAMS_BY_INTENT.get(intent_type, ())
    missing: list[str] = []

    for field_name in required_params:
        value = params.get(field_name)
        if value is None:
            missing.append(field_name)
            continue
        if isinstance(value, str):
            normalized_value = _normalized_optional_text(value)
            if normalized_value is None:
                missing.append(field_name)
                continue
        if isinstance(value, list) and not _normalized_string_list(value):
            missing.append(field_name)

    return missing


def build_requested_pipeline(result: IntentClassification, *, latest_user_request: str = "") -> list[str]:
    """从结构化结果和原始用户文本构建稳定的阶段执行链。"""

    normalized_pipeline = normalize_requested_pipeline(result.requested_pipeline, default_stage=result.intent_type)
    inferred_pipeline = infer_requested_pipeline_from_text(
        latest_user_request,
        default_stage=result.intent_type,
    )
    if len(normalized_pipeline) > 1:
        return normalized_pipeline
    if len(inferred_pipeline) > len(normalized_pipeline):
        return inferred_pipeline
    return normalized_pipeline


def infer_requested_pipeline_from_text(text: str, *, default_stage: str | None = None) -> list[str]:
    """用轻量关键词顺序兜底推断多阶段执行链。"""

    normalized_text = (text or "").lower()
    ordered_hits: list[tuple[int, str]] = []
    for stage, keywords in SPECIALIST_STAGE_KEYWORDS.items():
        positions = [normalized_text.find(keyword.lower()) for keyword in keywords if normalized_text.find(keyword.lower()) >= 0]
        if positions:
            ordered_hits.append((min(positions), stage))

    ordered_hits.sort(key=lambda item: item[0])
    return normalize_requested_pipeline([stage for _, stage in ordered_hits], default_stage=default_stage)


def _as_optional_text(value: Any) -> str | None:
    """把任意标量值转成可归一化文本。"""

    if value is None or isinstance(value, list):
        return None
    return str(value)


def _normalized_string_list(value: Any) -> list[str]:
    """把模型输出的字符串列表归一化为非空字符串数组。"""

    if not isinstance(value, list):
        return []

    normalized_values: list[str] = []
    for item in value:
        normalized_item = _normalized_optional_text(str(item)) if item is not None else None
        if normalized_item is None:
            continue
        normalized_values.append(normalized_item)
    return normalized_values


def _normalized_optional_text(value: str | None) -> str | None:
    """去掉前后空白，并把空字符串归一化为 None。"""

    if value is None:
        return None

    normalized_value = value.strip()
    if normalized_value.lower() in NULL_LIKE_TEXT_VALUES:
        return None
    return normalized_value or None
