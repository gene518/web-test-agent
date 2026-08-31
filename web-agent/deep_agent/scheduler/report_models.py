"""Scheduler 执行总结报告的数据契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


RunStatus = Literal[
    "passed",
    "passed_with_retries",
    "failed",
    "timed_out",
    "cancelled",
    "error",
]
DataQuality = Literal["case_details", "summary_only", "exit_code_only"]
CaseStatus = Literal[
    "passed",
    "failed",
    "flaky",
    "skipped",
    "interrupted",
    "timed_out",
    "did_not_run",
    "unknown",
]
FailureOwner = Literal[
    "test_automation",
    "product",
    "environment",
    "data",
    "unknown",
]
HealingStatus = Literal[
    "not_needed",
    "not_eligible",
    "attempted",
    "succeeded",
    "failed",
]
ConversationStatus = Literal[
    "running",
    "completed",
    "cancelled",
    "error",
    "unavailable",
]


class ScheduledRunMetadata(BaseModel):
    """一次调度执行的稳定身份和输入范围。"""

    run_id: str
    task_id: str
    display_name: str
    project_name: str
    project_dir: str
    scheduled_for: str
    schedule: str
    locations: list[str]
    headed: bool
    timezone: str | None = None


class ScheduledExecutionCounts(BaseModel):
    """从 Playwright 输出中提取的最终用例统计。"""

    total: int = 0
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    skipped: int = 0
    interrupted: int = 0
    timed_out: int = 0
    did_not_run: int = 0


class ScheduledExecutionSummary(BaseModel):
    """进程级结果和可验证的 Playwright 摘要。"""

    status: RunStatus
    exit_code: int
    duration_seconds: float = Field(ge=0)
    timed_out: bool = False
    cancelled: bool = False
    started_at: str | None = None
    finished_at: str | None = None
    counts: ScheduledExecutionCounts = Field(default_factory=ScheduledExecutionCounts)
    output_line_count: int = 0
    output_truncated: bool = False
    data_quality: DataQuality
    error_message: str | None = None


class ScheduledTestCaseReport(BaseModel):
    """失败或发生重试的单个 Playwright 用例。"""

    test_id: str
    title: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    project_name: str | None = None
    final_status: CaseStatus
    retry_count: int = Field(default=0, ge=0)
    failure_reasons: list[str] = Field(default_factory=list)
    retry_reasons: list[str] = Field(default_factory=list)


class ScheduledIssueReport(BaseModel):
    """当前及历史执行中归一化后的问题类别。"""

    category: str
    label: str
    current_occurrences: int = Field(default=0, ge=0)
    historical_occurrences: int = Field(default=0, ge=0)
    total_occurrences: int = Field(default=0, ge=0)
    affected_run_count: int = Field(default=0, ge=0)
    affected_cases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    recurring: bool = False


class ScheduledHistoryAnalysis(BaseModel):
    """同一任务最近运行的稳定性指标。"""

    analyzed_runs: int = Field(default=1, ge=1)
    successful_runs: int = Field(default=0, ge=0)
    failed_runs: int = Field(default=0, ge=0)
    runs_with_retries: int = Field(default=0, ge=0)
    success_rate: float = Field(default=0, ge=0, le=1)
    retry_rate: float = Field(default=0, ge=0, le=1)
    recurring_issue_categories: list[str] = Field(default_factory=list)


class ScheduledFailureDiagnosis(BaseModel):
    """模型对一个失败用例给出的结构化责任归属。"""

    test_id: str
    owner: FailureOwner = "unknown"
    confidence: float = Field(default=0, ge=0, le=1)
    repair_allowed: bool = False
    reason: str
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class ScheduledHealingReport(BaseModel):
    """自动 Healer 的门禁、作用域和验证结果。"""

    status: HealingStatus = "not_needed"
    attempted: bool = False
    eligible_test_ids: list[str] = Field(default_factory=list)
    test_scripts: list[str] = Field(default_factory=list)
    test_plan_files: list[str] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    validation_status: Literal["not_run", "passed", "failed"] = "not_run"
    reason: str | None = None


class ScheduledConversationReport(BaseModel):
    """本次运行对应的只读监控对话。"""

    thread_id: str | None = None
    graph_id: str = "web-autotest-scheduled-run"
    readonly: bool = True
    status: ConversationStatus = "unavailable"
    error_message: str | None = None


class ScheduledRunArtifacts(BaseModel):
    """本次执行和总结阶段产出的文件。"""

    scheduler_log: str
    analysis_report: str
    latest_analysis_report: str
    analysis_report_markdown: str | None = None
    latest_analysis_report_markdown: str | None = None
    playwright_report_directory: str | None = None


class ScheduledRunReport(BaseModel):
    """Scheduler 总结节点落盘的完整结构化报告。"""

    # 保留普通 int 而不是 Literal[2]，这样 schema v1 历史报告仍可直接读取。
    schema_version: int = 2
    generated_at: str
    run: ScheduledRunMetadata
    execution: ScheduledExecutionSummary
    failed_cases: list[ScheduledTestCaseReport] = Field(default_factory=list)
    retried_cases: list[ScheduledTestCaseReport] = Field(default_factory=list)
    common_issues: list[ScheduledIssueReport] = Field(default_factory=list)
    history: ScheduledHistoryAnalysis
    diagnostic_excerpt: list[str] = Field(default_factory=list)
    analysis_warnings: list[str] = Field(default_factory=list)
    conclusion: str
    analysis_mode: Literal["deterministic", "model_enriched"] = "deterministic"
    enriched_analysis: str | None = None
    diagnoses: list[ScheduledFailureDiagnosis] = Field(default_factory=list)
    healing: ScheduledHealingReport = Field(default_factory=ScheduledHealingReport)
    conversation: ScheduledConversationReport = Field(
        default_factory=ScheduledConversationReport
    )
    artifacts: ScheduledRunArtifacts


__all__ = [
    "CaseStatus",
    "ConversationStatus",
    "DataQuality",
    "FailureOwner",
    "HealingStatus",
    "RunStatus",
    "ScheduledExecutionCounts",
    "ScheduledExecutionSummary",
    "ScheduledHistoryAnalysis",
    "ScheduledFailureDiagnosis",
    "ScheduledHealingReport",
    "ScheduledConversationReport",
    "ScheduledIssueReport",
    "ScheduledRunArtifacts",
    "ScheduledRunMetadata",
    "ScheduledRunReport",
    "ScheduledTestCaseReport",
]
