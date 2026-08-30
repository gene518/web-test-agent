"""Scheduler 执行后的总结节点、历史聚合和报告持久化。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from deep_agent.scheduler.analysis import (
    ISSUE_LABELS,
    ParsedPlaywrightOutput,
    classify_issue,
    parse_playwright_output,
)
from deep_agent.scheduler.report_models import (
    RunStatus,
    ScheduledExecutionSummary,
    ScheduledHistoryAnalysis,
    ScheduledIssueReport,
    ScheduledRunArtifacts,
    ScheduledRunMetadata,
    ScheduledRunReport,
    ScheduledTestCaseReport,
)
from deep_agent.scheduler.runner import PendingScheduledRun, ScheduledRunResult


DEFAULT_HISTORY_LIMIT = 20


class ScheduledReportEnricher(Protocol):
    """可选的模型分析接口；确定性报告不依赖该接口。"""

    async def enrich(self, report: ScheduledRunReport) -> str:
        """返回对既有结构化报告的补充分析。"""


class ScheduledRunSummaryStage(Protocol):
    """服务执行完成后必须调用的总结阶段协议。"""

    async def execute(
        self,
        run_request: PendingScheduledRun,
        result: ScheduledRunResult,
    ) -> "ScheduledRunSummaryResult":
        """分析并持久化一次调度结果。"""


@dataclass(frozen=True, slots=True)
class ScheduledRunSummaryResult:
    """总结阶段返回给调度服务的报告和文件位置。"""

    report: ScheduledRunReport
    report_path: Path
    latest_report_path: Path
    markdown_report_path: Path
    latest_markdown_report_path: Path


@dataclass(slots=True)
class _IssueAccumulator:
    current_occurrences: int = 0
    historical_occurrences: int = 0
    run_ids: set[str] = field(default_factory=set)
    cases: set[str] = field(default_factory=set)
    examples: list[str] = field(default_factory=list)


class ScheduledRunSummaryNode:
    """把当前执行和同任务历史运行汇总为结构化报告。"""

    def __init__(
        self,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        enricher: ScheduledReportEnricher | None = None,
    ) -> None:
        if history_limit < 0:
            raise ValueError("history_limit 不能小于 0。")
        self._history_limit = history_limit
        self._enricher = enricher

    async def execute(
        self,
        run_request: PendingScheduledRun,
        result: ScheduledRunResult,
    ) -> ScheduledRunSummaryResult:
        """执行确定性分析，可选补充模型洞察，最后原子落盘。"""

        (
            report_path,
            latest_report_path,
            markdown_report_path,
            latest_markdown_report_path,
        ) = _resolve_report_paths(run_request)
        parsed_output = parse_playwright_output(result.output_lines)
        history_reports, history_warnings = await asyncio.to_thread(
            _load_history_reports,
            report_path.parent,
            report_path,
            self._history_limit,
        )
        report = _build_report(
            run_request=run_request,
            result=result,
            parsed_output=parsed_output,
            history_reports=history_reports,
            report_path=report_path,
            latest_report_path=latest_report_path,
            markdown_report_path=markdown_report_path,
            latest_markdown_report_path=latest_markdown_report_path,
            history_warnings=history_warnings,
        )

        if self._enricher is not None:
            try:
                enriched_analysis = (await self._enricher.enrich(report)).strip()
            except Exception as exc:  # noqa: BLE001
                report.analysis_warnings.append(
                    f"可选模型分析不可用，已保留确定性总结：{type(exc).__name__}: {exc}"
                )
            else:
                if enriched_analysis:
                    report.analysis_mode = "model_enriched"
                    report.enriched_analysis = enriched_analysis

        await asyncio.to_thread(
            _persist_report,
            report,
            report_path,
            latest_report_path,
            markdown_report_path,
            latest_markdown_report_path,
        )
        return ScheduledRunSummaryResult(
            report=report,
            report_path=report_path,
            latest_report_path=latest_report_path,
            markdown_report_path=markdown_report_path,
            latest_markdown_report_path=latest_markdown_report_path,
        )


def _build_report(
    *,
    run_request: PendingScheduledRun,
    result: ScheduledRunResult,
    parsed_output: ParsedPlaywrightOutput,
    history_reports: list[ScheduledRunReport],
    report_path: Path,
    latest_report_path: Path,
    markdown_report_path: Path,
    latest_markdown_report_path: Path,
    history_warnings: list[str],
) -> ScheduledRunReport:
    """构建不依赖外部模型的完整报告。"""

    run_id = _run_id(run_request)
    run_status = _resolve_run_status(result, parsed_output)
    warnings = list(history_warnings)
    if result.output_truncated:
        warnings.append(
            "控制台输出超过采集上限，仅使用保留的输出分析；完整输出请查看 Scheduler 日志。"
        )
    if parsed_output.data_quality == "exit_code_only":
        warnings.append(
            "未识别到 Playwright 用例摘要，成功或失败仅依据进程退出状态判断。"
        )
    if parsed_output.counts.failed > len(parsed_output.failed_cases):
        warnings.append(
            "部分失败用例只有汇总计数，控制台输出中缺少可定位的文件和标题。"
        )
    if parsed_output.counts.flaky > len(parsed_output.retried_cases):
        warnings.append(
            "部分重试用例只有 flaky 汇总计数，控制台输出中缺少可定位的文件和标题。"
        )

    current_reasons = _collect_current_reasons(
        result=result,
        run_status=run_status,
        parsed_output=parsed_output,
    )
    common_issues = _build_common_issues(
        run_id=run_id,
        current_reasons=current_reasons,
        history_reports=history_reports,
    )
    history = _build_history_analysis(
        run_status=run_status,
        has_retries=_has_retries(parsed_output),
        history_reports=history_reports,
        common_issues=common_issues,
    )
    playwright_report_directory = _resolve_playwright_report_directory(
        run_request,
        result,
    )
    execution = ScheduledExecutionSummary(
        status=run_status,
        exit_code=result.exit_code,
        duration_seconds=max(0, result.duration_seconds),
        timed_out=result.timed_out,
        cancelled=result.cancelled,
        started_at=_datetime_text(result.started_at),
        finished_at=_datetime_text(result.finished_at),
        counts=parsed_output.counts,
        output_line_count=result.output_line_count,
        output_truncated=result.output_truncated,
        data_quality=parsed_output.data_quality,
        error_message=result.error_message,
    )
    conclusion = _build_conclusion(
        run_request=run_request,
        execution=execution,
        failed_cases=list(parsed_output.failed_cases),
        retried_cases=list(parsed_output.retried_cases),
        common_issues=common_issues,
        history=history,
    )
    return ScheduledRunReport(
        generated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        run=ScheduledRunMetadata(
            run_id=run_id,
            task_id=run_request.task_id,
            display_name=run_request.display_name,
            project_name=run_request.project_name,
            project_dir=str(run_request.project_dir),
            scheduled_for=run_request.scheduled_minute.isoformat(timespec="minutes"),
            schedule=run_request.schedule,
            locations=list(run_request.locations),
            headed=run_request.headed,
            timezone=run_request.timezone,
        ),
        execution=execution,
        failed_cases=list(parsed_output.failed_cases),
        retried_cases=list(parsed_output.retried_cases),
        common_issues=common_issues,
        history=history,
        diagnostic_excerpt=list(parsed_output.diagnostic_excerpt),
        analysis_warnings=warnings,
        conclusion=conclusion,
        artifacts=ScheduledRunArtifacts(
            scheduler_log=str(run_request.log_file_path),
            analysis_report=str(report_path),
            latest_analysis_report=str(latest_report_path),
            analysis_report_markdown=str(markdown_report_path),
            latest_analysis_report_markdown=str(latest_markdown_report_path),
            playwright_report_directory=(
                str(playwright_report_directory)
                if playwright_report_directory is not None
                else None
            ),
        ),
    )


def _collect_current_reasons(
    *,
    result: ScheduledRunResult,
    run_status: RunStatus,
    parsed_output: ParsedPlaywrightOutput,
) -> list[tuple[str, str]]:
    """收集用例级原因，信息不足时补充可信的进程级原因。"""

    collected: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    case_reasons: set[str] = set()
    for case in (*parsed_output.failed_cases, *parsed_output.retried_cases):
        for reason in (*case.failure_reasons, *case.retry_reasons):
            case_reasons.add(reason)
            item = (case.test_id, reason)
            if item not in seen:
                seen.add(item)
                collected.append(item)
    for reason in parsed_output.diagnostic_reasons:
        if reason in case_reasons:
            continue
        item = ("<run>", reason)
        if item not in seen:
            seen.add(item)
            collected.append(item)

    if result.error_message:
        item = ("<run>", result.error_message[:500])
        if item not in seen:
            seen.add(item)
            collected.append(item)

    if not collected and run_status != "passed":
        if result.timed_out:
            reason = "Scheduler task timeout: Playwright process exceeded the configured execution limit."
        elif result.cancelled:
            reason = "Scheduler task interrupted before Playwright completed."
        elif result.error_message:
            reason = result.error_message
        else:
            reason = f"Playwright process exited with code {result.exit_code}."
        collected.append(("<run>", reason[:500]))
    return collected


def _build_common_issues(
    *,
    run_id: str,
    current_reasons: list[tuple[str, str]],
    history_reports: list[ScheduledRunReport],
) -> list[ScheduledIssueReport]:
    """合并当前与历史原因，产出可排序的共性问题。"""

    accumulators: defaultdict[str, _IssueAccumulator] = defaultdict(_IssueAccumulator)
    for case_id, reason in current_reasons:
        category = classify_issue(reason)
        accumulator = accumulators[category]
        accumulator.current_occurrences += 1
        accumulator.run_ids.add(run_id)
        if case_id != "<run>":
            accumulator.cases.add(case_id)
        if reason not in accumulator.examples and len(accumulator.examples) < 3:
            accumulator.examples.append(reason)

    for history_report in history_reports:
        for issue in history_report.common_issues:
            if issue.current_occurrences <= 0:
                continue
            accumulator = accumulators[issue.category]
            accumulator.historical_occurrences += issue.current_occurrences
            accumulator.run_ids.add(history_report.run.run_id)
            accumulator.cases.update(issue.affected_cases)
            for example in issue.examples:
                if (
                    example not in accumulator.examples
                    and len(accumulator.examples) < 3
                ):
                    accumulator.examples.append(example)

    issues: list[ScheduledIssueReport] = []
    for category, accumulator in accumulators.items():
        total = accumulator.current_occurrences + accumulator.historical_occurrences
        affected_run_count = len(accumulator.run_ids)
        affected_cases = sorted(accumulator.cases)
        issues.append(
            ScheduledIssueReport(
                category=category,
                label=ISSUE_LABELS.get(category, ISSUE_LABELS["unknown"]),
                current_occurrences=accumulator.current_occurrences,
                historical_occurrences=accumulator.historical_occurrences,
                total_occurrences=total,
                affected_run_count=affected_run_count,
                affected_cases=affected_cases[:20],
                examples=list(accumulator.examples),
                recurring=affected_run_count >= 2 or total >= 2,
            )
        )
    return sorted(
        issues,
        key=lambda issue: (
            not issue.recurring,
            -issue.total_occurrences,
            issue.category,
        ),
    )


def _build_history_analysis(
    *,
    run_status: RunStatus,
    has_retries: bool,
    history_reports: list[ScheduledRunReport],
    common_issues: list[ScheduledIssueReport],
) -> ScheduledHistoryAnalysis:
    """计算同任务最近运行的成功率、重试率和重复问题。"""

    statuses = [report.execution.status for report in history_reports]
    statuses.append(run_status)
    retry_runs = sum(
        bool(report.retried_cases) or report.execution.counts.flaky > 0
        for report in history_reports
    )
    retry_runs += int(has_retries)
    successful_runs = sum(
        status in {"passed", "passed_with_retries"} for status in statuses
    )
    analyzed_runs = len(statuses)
    return ScheduledHistoryAnalysis(
        analyzed_runs=analyzed_runs,
        successful_runs=successful_runs,
        failed_runs=analyzed_runs - successful_runs,
        runs_with_retries=retry_runs,
        success_rate=round(successful_runs / analyzed_runs, 4),
        retry_rate=round(retry_runs / analyzed_runs, 4),
        recurring_issue_categories=[
            issue.category for issue in common_issues if issue.recurring
        ],
    )


def _build_conclusion(
    *,
    run_request: PendingScheduledRun,
    execution: ScheduledExecutionSummary,
    failed_cases: list[ScheduledTestCaseReport],
    retried_cases: list[ScheduledTestCaseReport],
    common_issues: list[ScheduledIssueReport],
    history: ScheduledHistoryAnalysis,
) -> str:
    """生成无需模型也完整可读的中文结论。"""

    status_labels = {
        "passed": "执行成功",
        "passed_with_retries": "重试后执行成功",
        "failed": "执行失败",
        "timed_out": "执行超时",
        "cancelled": "执行被取消",
        "error": "执行异常",
    }
    counts = execution.counts
    parts = [
        f"任务 {run_request.display_name} {status_labels[execution.status]}，"
        f"退出码 {execution.exit_code}，耗时 {execution.duration_seconds:.3f} 秒。"
    ]
    if counts.total:
        parts.append(
            "Playwright 汇总："
            f"通过 {counts.passed}、失败 {counts.failed}、重试后通过 {counts.flaky}、"
            f"跳过 {counts.skipped}、中断/未执行 {counts.interrupted + counts.did_not_run}。"
        )
    if failed_cases:
        parts.append(f"已定位 {len(failed_cases)} 个失败用例及其原因。")
    elif counts.failed:
        parts.append(
            f"共有 {counts.failed} 个失败用例，但输出未包含足够的用例定位信息。"
        )
    if retried_cases:
        parts.append(f"已识别 {len(retried_cases)} 个重试用例并保留重试原因。")
    elif counts.flaky:
        parts.append(
            f"共有 {counts.flaky} 个重试后通过的用例，但输出未包含完整用例明细。"
        )

    recurring_labels = [issue.label for issue in common_issues if issue.recurring]
    if recurring_labels:
        parts.append(f"共性问题集中在：{'、'.join(recurring_labels[:3])}。")
    elif common_issues:
        parts.append(
            f"本次主要问题为：{'、'.join(issue.label for issue in common_issues[:3])}。"
        )
    parts.append(
        f"本次及最近历史共分析 {history.analyzed_runs} 次运行，"
        f"成功率 {history.success_rate:.1%}，发生重试的运行占比 {history.retry_rate:.1%}。"
    )
    return "".join(parts)


def _resolve_run_status(
    result: ScheduledRunResult,
    parsed_output: ParsedPlaywrightOutput,
) -> RunStatus:
    """同时考虑进程状态和 Playwright 汇总判定最终状态。"""

    if result.cancelled:
        return "cancelled"
    if result.timed_out:
        return "timed_out"
    if result.error_message and result.exit_code != 0:
        return "error"
    if result.exit_code != 0 or parsed_output.counts.failed > 0:
        return "failed"
    if _has_retries(parsed_output):
        return "passed_with_retries"
    return "passed"


def _has_retries(parsed_output: ParsedPlaywrightOutput) -> bool:
    return bool(parsed_output.retried_cases or parsed_output.counts.flaky)


def _resolve_report_paths(
    run_request: PendingScheduledRun,
) -> tuple[Path, Path, Path, Path]:
    """返回位于测试根目录内的本次报告和 latest 报告路径。"""

    test_root = run_request.test_root_dir.resolve()
    task_digest = hashlib.sha256(
        f"{run_request.project_dir}::{run_request.task_id}".encode()
    ).hexdigest()[:8]
    task_component = f"{_safe_component(run_request.task_id)}-{task_digest}"
    report_directory = test_root / "scheduler-reports" / task_component
    report_directory.mkdir(parents=True, exist_ok=True)
    resolved_directory = report_directory.resolve()
    if not resolved_directory.is_relative_to(test_root):
        raise RuntimeError("Scheduler 报告目录不能逃逸测试根目录。")
    scheduled_text = run_request.scheduled_minute.strftime("%Y%m%dT%H%M%S%z")
    report_stem = f"{scheduled_text}-{_run_id(run_request)}"
    return (
        resolved_directory / f"{report_stem}.json",
        resolved_directory / "latest.json",
        resolved_directory / f"{report_stem}.md",
        resolved_directory / "latest.md",
    )


def _run_id(run_request: PendingScheduledRun) -> str:
    return hashlib.sha256(run_request.run_key.encode()).hexdigest()[:16]


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return (normalized or "task")[:64]


def _resolve_playwright_report_directory(
    run_request: PendingScheduledRun,
    result: ScheduledRunResult,
) -> Path | None:
    if not result.report_name:
        return None
    candidate = (
        run_request.project_dir / "test-results" / result.report_name
    ).resolve()
    if not candidate.is_relative_to(run_request.project_dir.resolve()):
        return None
    return candidate if candidate.exists() else None


def _load_history_reports(
    report_directory: Path,
    current_report_path: Path,
    history_limit: int,
) -> tuple[list[ScheduledRunReport], list[str]]:
    """读取同一任务最近的有效报告，损坏文件只产生警告。"""

    if history_limit == 0 or not report_directory.is_dir():
        return [], []
    candidates = [
        path
        for path in report_directory.glob("*.json")
        if path.name != "latest.json" and path != current_report_path
    ]
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    reports: list[ScheduledRunReport] = []
    warnings: list[str] = []
    for path in candidates[:history_limit]:
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("文件超过 2 MiB")
            raw_report = json.loads(path.read_text(encoding="utf-8"))
            reports.append(ScheduledRunReport.model_validate(raw_report))
        except (OSError, ValueError, json.JSONDecodeError, ValidationError) as exc:
            warnings.append(f"忽略无法读取的历史报告 {path.name}：{exc}")
    return reports, warnings


def _persist_report(
    report: ScheduledRunReport,
    report_path: Path,
    latest_report_path: Path,
    markdown_report_path: Path,
    latest_markdown_report_path: Path,
) -> None:
    """原子写入结构化、人类可读报告及各自的 latest 快照。"""

    payload = (
        json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    _atomic_write_text(report_path, payload)
    _atomic_write_text(latest_report_path, payload)
    markdown_payload = _render_markdown_report(report)
    _atomic_write_text(markdown_report_path, markdown_payload)
    _atomic_write_text(latest_markdown_report_path, markdown_payload)


def _render_markdown_report(report: ScheduledRunReport) -> str:
    """把结构化报告渲染为无需额外工具即可阅读的完整分析报告。"""

    execution = report.execution
    counts = execution.counts
    lines = [
        "# Scheduler 执行分析报告",
        "",
        report.conclusion,
        "",
        "## 任务与执行",
        "",
        "| 字段 | 值 |",
        "| --- | --- |",
        f"| 任务 | {_markdown_cell(report.run.display_name)} |",
        f"| 计划时间 | {_markdown_cell(report.run.scheduled_for)} |",
        f"| Cron | `{_markdown_code(report.run.schedule)}` |",
        f"| 执行状态 | `{execution.status}` |",
        f"| 退出码 | `{execution.exit_code}` |",
        f"| 耗时 | {execution.duration_seconds:.3f} 秒 |",
        f"| 数据质量 | `{execution.data_quality}` |",
        "",
        "## Playwright 汇总",
        "",
        "| 总数 | 通过 | 失败 | 重试后通过 | 跳过 | 中断 | 超时 | 未执行 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| {counts.total} | {counts.passed} | {counts.failed} | "
            f"{counts.flaky} | {counts.skipped} | {counts.interrupted} | "
            f"{counts.timed_out} | {counts.did_not_run} |"
        ),
        "",
    ]
    _append_case_section(lines, "失败用例", report.failed_cases)
    _append_case_section(lines, "重试用例", report.retried_cases)

    lines.extend(["## 共性问题", ""])
    if report.common_issues:
        for issue in report.common_issues:
            recurrence = "是" if issue.recurring else "否"
            lines.append(
                f"- **{_markdown_text(issue.label)}** (`{_markdown_code(issue.category)}`)："
                f"本次 {issue.current_occurrences} 次，历史 {issue.historical_occurrences} 次，"
                f"影响 {issue.affected_run_count} 次运行，是否重复出现：{recurrence}。"
            )
            for example in issue.examples:
                lines.append(f"  - 原因：{_markdown_text(example)}")
    else:
        lines.append("未识别到失败或重试问题。")
    lines.extend(
        [
            "",
            "## 历史稳定性",
            "",
            f"- 分析运行数：{report.history.analyzed_runs}",
            f"- 成功运行数：{report.history.successful_runs}",
            f"- 失败运行数：{report.history.failed_runs}",
            f"- 发生重试的运行数：{report.history.runs_with_retries}",
            f"- 成功率：{report.history.success_rate:.1%}",
            f"- 重试率：{report.history.retry_rate:.1%}",
            "",
        ]
    )
    if report.enriched_analysis:
        lines.extend(["## 模型补充分析", "", report.enriched_analysis, ""])
    _append_text_section(lines, "诊断摘录", report.diagnostic_excerpt)
    _append_text_section(lines, "分析限制与警告", report.analysis_warnings)
    lines.extend(
        [
            "## 产物",
            "",
            f"- Scheduler 日志：`{_markdown_code(report.artifacts.scheduler_log)}`",
            f"- 结构化报告：`{_markdown_code(report.artifacts.analysis_report)}`",
        ]
    )
    if report.artifacts.analysis_report_markdown:
        lines.append(
            "- Markdown 报告："
            f"`{_markdown_code(report.artifacts.analysis_report_markdown)}`"
        )
    if report.artifacts.playwright_report_directory:
        lines.append(
            "- Playwright 报告目录："
            f"`{_markdown_code(report.artifacts.playwright_report_directory)}`"
        )
    lines.extend(["", f"生成时间：{_markdown_text(report.generated_at)}", ""])
    return "\n".join(lines)


def _append_case_section(
    lines: list[str],
    title: str,
    cases: list[ScheduledTestCaseReport],
) -> None:
    lines.extend([f"## {title}", ""])
    if not cases:
        lines.extend(["无。", ""])
        return
    for case in cases:
        location = case.file or "<unknown>"
        if case.line is not None:
            location = f"{location}:{case.line}"
        lines.append(
            f"### {_markdown_text(case.title)} (`{_markdown_code(case.final_status)}`)"
        )
        lines.extend(
            [
                "",
                f"- 位置：`{_markdown_code(location)}`",
                f"- 重试次数：{case.retry_count}",
            ]
        )
        reasons = list(dict.fromkeys([*case.failure_reasons, *case.retry_reasons]))
        if reasons:
            lines.append("- 原因：")
            lines.extend(f"  - {_markdown_text(reason)}" for reason in reasons)
        lines.append("")


def _append_text_section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.extend([f"## {title}", ""])
    lines.extend(f"- {_markdown_text(item)}" for item in items)
    lines.append("")


def _markdown_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_")


def _markdown_cell(value: str) -> str:
    return _markdown_text(value).replace("|", "\\|").replace("\n", " ")


def _markdown_code(value: str) -> str:
    return value.replace("`", "'").replace("\n", " ")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(content)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_path = Path(temp_file.name)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _datetime_text(value: datetime | None) -> str | None:
    return value.isoformat(timespec="milliseconds") if value is not None else None


__all__ = [
    "ScheduledReportEnricher",
    "ScheduledRunSummaryNode",
    "ScheduledRunSummaryResult",
    "ScheduledRunSummaryStage",
]
