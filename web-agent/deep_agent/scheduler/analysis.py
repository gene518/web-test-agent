"""从 Playwright 控制台输出中提取用例、重试和失败原因。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from deep_agent.scheduler.report_models import (
    CaseStatus,
    DataQuality,
    ScheduledExecutionCounts,
    ScheduledTestCaseReport,
)


_ANSI_ESCAPE_PATTERN = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))"
)
_SUMMARY_PATTERN = re.compile(
    r"^\s*(?P<count>\d+)\s+"
    r"(?P<status>passed|failed|flaky|skipped|interrupted|timed\s+out|did\s+not\s+run)\b",
    re.IGNORECASE,
)
_LOCATION_PATTERN = re.compile(
    r"(?P<file>(?:[A-Za-z]:[\\/])?[^:<>|\"\n]*?\.(?:spec|test)\.[cm]?[jt]sx?)"
    r":(?P<line>\d+):(?P<column>\d+)",
    re.IGNORECASE,
)
_RETRY_PATTERN = re.compile(r"\bretry\s*#?\s*(?P<retry>\d+)\b", re.IGNORECASE)
_ERROR_SIGNAL_PATTERN = re.compile(
    r"(?:\b(?:error|timeout(?:error)?|syntaxerror|typeerror|referenceerror)\b|"
    r"expected\b|received\b|waiting\s+for|strict\s+mode|"
    r"cannot\s+find\s+module|net::|econn|enotfound|"
    r"\b(?:401|403|429|5\d\d)\b|target\s+(?:page|browser|context).*closed)",
    re.IGNORECASE,
)
_DURATION_SUFFIX_PATTERN = re.compile(r"\s+\([^()]*(?:ms|s|m)\)\s*$", re.IGNORECASE)
_TITLE_DECORATION_SUFFIX_PATTERN = re.compile(r"\s*[─━-]{3,}.*$")

_STATUS_NAMES: dict[str, str] = {
    "passed": "passed",
    "failed": "failed",
    "flaky": "flaky",
    "skipped": "skipped",
    "interrupted": "interrupted",
    "timed out": "timed_out",
    "did not run": "did_not_run",
}

ISSUE_LABELS: dict[str, str] = {
    "timeout": "等待或执行超时",
    "assertion": "断言与页面实际状态不一致",
    "selector": "元素定位或交互失败",
    "network": "网络、接口或服务不可用",
    "authentication": "登录态或权限异常",
    "browser": "浏览器、页面或上下文提前关闭",
    "configuration": "测试配置、依赖或脚本语法异常",
    "interrupted": "执行被中断或用例未运行",
    "unknown": "未归类的执行错误",
}


@dataclass(slots=True)
class _ParsedCase:
    test_id: str
    title: str
    file: str | None
    line: int | None
    column: int | None
    project_name: str | None
    final_status: CaseStatus = "unknown"
    retry_count: int = 0
    reasons: list[str] = field(default_factory=list)

    def add_reason(self, reason: str) -> None:
        """添加去重后的诊断原因。"""

        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    def to_report(self) -> ScheduledTestCaseReport:
        """转换为公开报告模型。"""

        retry_reasons = list(self.reasons) if self.retry_count else []
        if self.retry_count and not retry_reasons:
            retry_reasons = [
                "用例首次执行未通过，后续发生重试；控制台未提供可归类的错误详情。"
            ]
        failure_reasons = list(self.reasons)
        if (
            self.final_status in {"failed", "interrupted", "timed_out", "did_not_run"}
            and not failure_reasons
        ):
            failure_reasons = ["用例未通过；控制台未提供可归类的错误详情。"]
        return ScheduledTestCaseReport(
            test_id=self.test_id,
            title=self.title,
            file=self.file,
            line=self.line,
            column=self.column,
            project_name=self.project_name,
            final_status=self.final_status,
            retry_count=self.retry_count,
            failure_reasons=failure_reasons,
            retry_reasons=retry_reasons,
        )


@dataclass(frozen=True, slots=True)
class ParsedPlaywrightOutput:
    """一次控制台输出的确定性解析结果。"""

    counts: ScheduledExecutionCounts
    failed_cases: tuple[ScheduledTestCaseReport, ...]
    retried_cases: tuple[ScheduledTestCaseReport, ...]
    diagnostic_reasons: tuple[str, ...]
    diagnostic_excerpt: tuple[str, ...]
    data_quality: DataQuality


def parse_playwright_output(output_lines: tuple[str, ...]) -> ParsedPlaywrightOutput:
    """解析 Playwright list/line reporter 输出，不依赖外部模型。"""

    normalized_lines = tuple(_normalize_line(line) for line in output_lines)
    counts_by_status = {status: 0 for status in _STATUS_NAMES.values()}
    cases: dict[str, _ParsedCase] = {}
    current_case: _ParsedCase | None = None
    active_summary_status: CaseStatus | None = None
    global_reasons: list[str] = []
    diagnostic_excerpt: list[str] = []

    for line in normalized_lines:
        if not line:
            continue

        summary_match = _SUMMARY_PATTERN.match(line)
        if summary_match is not None:
            status_name = " ".join(summary_match.group("status").lower().split())
            normalized_status: CaseStatus = _STATUS_NAMES[status_name]  # type: ignore[assignment]
            counts_by_status[normalized_status] = int(summary_match.group("count"))
            active_summary_status = normalized_status
            current_case = None
            continue

        parsed_identity = _parse_case_identity(line)
        if parsed_identity is not None:
            case = cases.get(parsed_identity.test_id)
            if case is None:
                case = parsed_identity
                cases[case.test_id] = case
            current_case = case
            if active_summary_status is not None:
                case.final_status = active_summary_status
                if active_summary_status == "flaky":
                    case.retry_count = max(case.retry_count, 1)
            else:
                _apply_progress_status(case, line)

            retry_match = _RETRY_PATTERN.search(line)
            if retry_match is not None:
                case.retry_count = max(
                    case.retry_count, int(retry_match.group("retry"))
                )
            continue

        retry_match = _RETRY_PATTERN.search(line)
        if retry_match is not None and current_case is not None:
            current_case.retry_count = max(
                current_case.retry_count,
                int(retry_match.group("retry")),
            )

        reason = _extract_reason(line)
        if reason is None:
            continue
        if reason not in global_reasons:
            global_reasons.append(reason)
        if len(diagnostic_excerpt) < 20 and reason not in diagnostic_excerpt:
            diagnostic_excerpt.append(reason)
        if current_case is not None:
            current_case.add_reason(reason)

    case_reports = [parsed_case.to_report() for parsed_case in cases.values()]
    failed_statuses = {"failed", "interrupted", "timed_out", "did_not_run"}
    failed_cases = tuple(
        case for case in case_reports if case.final_status in failed_statuses
    )
    retried_cases = tuple(
        case
        for case in case_reports
        if case.retry_count > 0 or case.final_status == "flaky"
    )
    counts = ScheduledExecutionCounts(
        passed=counts_by_status["passed"],
        failed=counts_by_status["failed"],
        flaky=counts_by_status["flaky"],
        skipped=counts_by_status["skipped"],
        interrupted=counts_by_status["interrupted"],
        timed_out=counts_by_status["timed_out"],
        did_not_run=counts_by_status["did_not_run"],
        total=sum(counts_by_status.values()),
    )
    if case_reports:
        data_quality = "case_details"
    elif counts.total:
        data_quality = "summary_only"
    else:
        data_quality = "exit_code_only"
    return ParsedPlaywrightOutput(
        counts=counts,
        failed_cases=failed_cases,
        retried_cases=retried_cases,
        diagnostic_reasons=tuple(global_reasons),
        diagnostic_excerpt=tuple(diagnostic_excerpt),
        data_quality=data_quality,
    )


def classify_issue(reason: str) -> str:
    """把原始错误文本归一化为稳定的问题类别。"""

    normalized = reason.lower()
    if any(token in normalized for token in ("timeout", "timed out", "exceeded")):
        return "timeout"
    if any(
        token in normalized
        for token in (
            "target page",
            "target browser",
            "target context",
            "browser has been closed",
            "page has been closed",
            "browser disconnected",
        )
    ):
        return "browser"
    if any(
        token in normalized for token in ("unauthorized", "forbidden", " 401", " 403")
    ):
        return "authentication"
    if any(
        token in normalized
        for token in (
            "net::",
            "econn",
            "enotfound",
            "socket",
            "http 5",
            " 500",
            " 502",
            " 503",
            " 504",
            " 429",
        )
    ):
        return "network"
    if any(
        token in normalized
        for token in ("expect(", "expected", "received", "assertion")
    ):
        return "assertion"
    if any(
        token in normalized
        for token in (
            "locator",
            "selector",
            "strict mode",
            "waiting for",
            "element is not",
        )
    ):
        return "selector"
    if any(
        token in normalized
        for token in (
            "cannot find module",
            "syntaxerror",
            "referenceerror",
            "config",
            "dependency",
        )
    ):
        return "configuration"
    if any(token in normalized for token in ("interrupted", "did not run")):
        return "interrupted"
    return "unknown"


def _parse_case_identity(line: str) -> _ParsedCase | None:
    """从 reporter 的用例行中解析项目、文件位置和标题。"""

    segments = re.split(r"\s+[›>]\s+", line)
    location_index = -1
    location_match: re.Match[str] | None = None
    for index, segment in enumerate(segments):
        match = _LOCATION_PATTERN.search(segment)
        if match is not None:
            location_index = index
            location_match = match
            break
    if location_match is None:
        return None

    file_path = location_match.group("file").strip()
    line_number = int(location_match.group("line"))
    column_number = int(location_match.group("column"))
    prefix = " ".join(segments[:location_index])
    project_candidates = re.findall(r"\[([^\]]+)\]", prefix)
    project_name = next(
        (
            candidate.strip()
            for candidate in reversed(project_candidates)
            if not re.fullmatch(r"\d+\s*/\s*\d+", candidate.strip())
        ),
        None,
    )
    title_segments = [
        segment.strip() for segment in segments[location_index + 1 :] if segment.strip()
    ]
    title = " › ".join(title_segments)
    title = _RETRY_PATTERN.sub("", title)
    title = _DURATION_SUFFIX_PATTERN.sub("", title).strip(" -()")
    title = _TITLE_DECORATION_SUFFIX_PATTERN.sub("", title).strip()
    if not title:
        title = PathLikeTitle.from_file(file_path)
    test_id = f"{project_name or '<default>'}|{file_path}:{line_number}:{column_number}|{title}"
    return _ParsedCase(
        test_id=test_id,
        title=title,
        file=file_path,
        line=line_number,
        column=column_number,
        project_name=project_name,
    )


class PathLikeTitle:
    """避免为一个文件名回退引入 pathlib 路径语义。"""

    @staticmethod
    def from_file(file_path: str) -> str:
        normalized = file_path.replace("\\", "/").rstrip("/")
        return normalized.rsplit("/", maxsplit=1)[-1] or "未命名用例"


def _apply_progress_status(case: _ParsedCase, line: str) -> None:
    """在最终统计段出现前，从进度符号推断临时状态。"""

    retry_match = _RETRY_PATTERN.search(line)
    if retry_match is not None:
        case.retry_count = max(case.retry_count, int(retry_match.group("retry")))
    if re.search(r"(?:✓|\bpassed\b)", line, re.IGNORECASE):
        case.final_status = "passed"
    elif re.search(r"(?:✘|×|\bfailed\b)", line, re.IGNORECASE):
        case.final_status = "failed"


def _extract_reason(line: str) -> str | None:
    """提取有诊断价值的单行文本并排除堆栈噪音。"""

    stripped = line.strip()
    if not stripped or stripped.startswith("at ") or stripped.startswith("node:"):
        return None
    if _ERROR_SIGNAL_PATTERN.search(stripped) is None:
        return None
    compact = " ".join(stripped.split())
    return compact[:500]


def _normalize_line(line: str) -> str:
    """移除 ANSI 控制符和终端回车。"""

    return _ANSI_ESCAPE_PATTERN.sub("", str(line)).replace("\r", "").rstrip()


__all__ = [
    "ISSUE_LABELS",
    "ParsedPlaywrightOutput",
    "classify_issue",
    "parse_playwright_output",
]
