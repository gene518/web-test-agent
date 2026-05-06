"""Generator runtime helper logic outside the main agent flow."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deep_agent.agent.artifacts import (
    diff_workspace_manifest,
    extract_generator_artifact_from_writes_and_snapshot,
    snapshot_workspace_manifest_async,
)


class GeneratorRuntimeHelper:
    """Tracks generator writes and verifies final workspace artifacts."""

    def __init__(
        self,
        *,
        normalize_files: Callable[[Any], list[str]],
        log_truncate: Callable[[Any], str],
        tool_output_is_error: Callable[[Any], bool],
    ) -> None:
        self._normalize_files = normalize_files
        self._log_truncate = log_truncate
        self._tool_output_is_error = tool_output_is_error

    def update_generator_write_state(
        self,
        generator_write_succeeded: bool,
        generator_write_error: str | None,
        pending_write_payloads: list[dict[str, str]],
        successful_write_payloads: list[dict[str, str]],
        event: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """根据工具事件更新 `generator_write_test` 的执行状态。"""

        if event.get("name") != "generator_write_test":
            return generator_write_succeeded, generator_write_error

        if event.get("event") == "on_tool_start":
            return generator_write_succeeded, generator_write_error

        if event.get("event") == "on_tool_error":
            if pending_write_payloads:
                pending_write_payloads.pop(0)
            return False, self._log_truncate(event.get("data", {}).get("error"))

        if event.get("event") != "on_tool_end":
            return generator_write_succeeded, generator_write_error

        pending_payload = pending_write_payloads.pop(0) if pending_write_payloads else None
        output = event.get("data", {}).get("output")
        if self._tool_output_is_error(output):
            return False, self._log_truncate(output)

        if pending_payload is None:
            return False, "`generator_write_test` 未捕获到输入 payload，无法确认写入文件。"

        successful_write_payloads.append(pending_payload)
        return True, None

    async def build_stage_artifact(
        self,
        *,
        successful_write_payloads: list[dict[str, str]],
        successful_workspace_write_paths: set[str],
        before_manifest: dict[str, Any],
        workspace_dir: Path,
        project_name: str,
        input_files: list[str],
        expected_test_scripts: list[str],
    ) -> dict[str, Any]:
        """构建 Generator 产物，并在必要时回退到最终落盘文件做校验。"""

        finalized_input_files = await asyncio.to_thread(
            self._finalize_generated_plan_files,
            workspace_dir,
            input_files,
        )
        after_manifest = await snapshot_workspace_manifest_async(workspace_dir)

        payload_error: Exception | None = None
        try:
            self._assert_expected_test_scripts_written(
                expected_test_scripts=expected_test_scripts,
                actual_test_scripts=[payload.get("fileName", "") for payload in successful_write_payloads],
            )
            return extract_generator_artifact_from_writes_and_snapshot(
                writes=successful_write_payloads,
                before_manifest=before_manifest,
                after_manifest=after_manifest,
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_files=finalized_input_files,
            )
        except Exception as exc:  # noqa: BLE001
            payload_error = exc

        fallback_writes = self._build_workspace_generator_writes(
            workspace_dir=workspace_dir,
            expected_test_scripts=expected_test_scripts,
            successful_workspace_write_paths=successful_workspace_write_paths,
            before_manifest=before_manifest,
            after_manifest=after_manifest,
        )
        try:
            return extract_generator_artifact_from_writes_and_snapshot(
                writes=fallback_writes,
                before_manifest=before_manifest,
                after_manifest=after_manifest,
                workspace_dir=workspace_dir,
                project_name=project_name,
                input_files=finalized_input_files,
            )
        except Exception as exc:  # noqa: BLE001
            if payload_error is None:
                raise
            raise RuntimeError(f"{payload_error} 回退到最终文件落盘校验后仍失败：{exc}") from exc

    def log_generator_write_state(
        self,
        *,
        agent: Any,
        event: dict[str, Any],
        generator_write_succeeded: bool,
        generator_write_error: str | None,
        trace_context: dict[str, Any],
    ) -> None:
        """记录 `generator_write_test` 的成功/失败状态，方便按 session grep。"""

        if event.get("name") != "generator_write_test" or event.get("event") not in {"on_tool_end", "on_tool_error"}:
            return

        status = "success" if generator_write_succeeded else "error"
        agent.log_tool_state(
            trace_context=trace_context,
            event_name="generator_write_test",
            status=status,
            error=generator_write_error,
        )

    @staticmethod
    def is_expected_browser_close_error(exc: Exception) -> bool:
        """判断异常是否为关闭浏览器后的预期错误。"""

        text = str(exc).lower()
        expected_fragments = (
            "target page, context or browser has been closed",
            "browsercontext.newpage",
            "browser has been closed",
            "remoteprotocolerror",
            "peer closed connection without sending complete message body",
            "incomplete chunked read",
        )
        return any(fragment in text for fragment in expected_fragments)

    def _finalize_generated_plan_files(
        self,
        workspace_dir: Path,
        input_files: list[str],
    ) -> list[str]:
        """把 aaaplanning 计划文件迁移到正式脚本目录，并清理旧 planning 目录。"""

        finalized_files: list[str] = []
        for input_file in input_files:
            relative_plan_file = self._resolve_relative_plan_file(workspace_dir, input_file)
            finalized_files.append(
                self._finalize_single_generated_plan_file(
                    workspace_dir=workspace_dir,
                    relative_plan_file=relative_plan_file,
                )
            )
        return self._dedupe_strings(finalized_files)

    def _resolve_relative_plan_file(self, workspace_dir: Path, input_file: str) -> Path:
        candidate = Path(input_file).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_dir / candidate

        resolved_workspace = workspace_dir.resolve()
        resolved_candidate = candidate.resolve()
        try:
            return resolved_candidate.relative_to(resolved_workspace)
        except ValueError as exc:
            raise RuntimeError(
                f"Generator 阶段计划文件 `{resolved_candidate}` 不在项目目录 `{resolved_workspace}` 下，无法迁移。"
            ) from exc

    def _finalize_single_generated_plan_file(
        self,
        *,
        workspace_dir: Path,
        relative_plan_file: Path,
    ) -> str:
        parts = relative_plan_file.parts
        if len(parts) != 3 or parts[0] != "test_case" or not parts[1].startswith("aaaplanning_"):
            return relative_plan_file.as_posix()

        plan_name = parts[1].removeprefix("aaaplanning_")
        expected_plan_file_name = f"aaa_{plan_name}.md"
        if not plan_name or parts[2] != expected_plan_file_name:
            return relative_plan_file.as_posix()

        source_file = workspace_dir / relative_plan_file
        target_relative_file = Path("test_case") / plan_name / expected_plan_file_name
        target_file = workspace_dir / target_relative_file
        if source_file.is_file():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
            shutil.rmtree(source_file.parent)
            return target_relative_file.as_posix()

        if target_file.is_file():
            return target_relative_file.as_posix()

        raise RuntimeError(
            f"Generator 阶段无法迁移测试计划：源文件 `{relative_plan_file.as_posix()}` 和目标文件 "
            f"`{target_relative_file.as_posix()}` 都不存在。"
        )

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            deduplicated.append(value)
        return deduplicated

    def _assert_expected_test_scripts_written(
        self,
        *,
        expected_test_scripts: list[str],
        actual_test_scripts: list[str],
    ) -> None:
        """确保本次成功写出的脚本集合完整覆盖计划要求。"""

        expected_files = self._normalize_files(expected_test_scripts)
        actual_files = self._normalize_files(actual_test_scripts)
        missing_files = [path for path in expected_files if path not in actual_files]
        if not missing_files:
            return

        missing_text = "、".join(f"`{path}`" for path in missing_files)
        raise RuntimeError(
            "Generator Agent 未完成测试计划要求的全部脚本生成。"
            f" 期望 {len(expected_files)} 个脚本，实际成功写出 {len(actual_files)} 个。"
            f" 缺失脚本：{missing_text}"
        )

    def _build_workspace_generator_writes(
        self,
        *,
        workspace_dir: Path,
        expected_test_scripts: list[str],
        successful_workspace_write_paths: set[str],
        before_manifest: dict[str, Any],
        after_manifest: dict[str, Any],
    ) -> list[dict[str, str]]:
        """基于最终 workspace 文件状态构建 Generator 产物输入。"""

        diff = diff_workspace_manifest(before_manifest, after_manifest)
        touched_paths = set(
            self._normalize_files(
                [
                    *diff["added"],
                    *diff["modified"],
                    *sorted(successful_workspace_write_paths),
                ]
            )
        )
        expected_files = self._normalize_files(expected_test_scripts)
        fallback_writes: list[dict[str, str]] = []
        missing_files: list[str] = []
        untouched_files: list[str] = []
        for relative_file in expected_files:
            file_path = workspace_dir / relative_file
            if not file_path.is_file():
                missing_files.append(relative_file)
                continue
            if relative_file not in touched_paths:
                untouched_files.append(relative_file)
                continue
            fallback_writes.append(
                {
                    "fileName": relative_file,
                    "code": file_path.read_text(encoding="utf-8"),
                }
            )

        if not missing_files and not untouched_files:
            return fallback_writes

        detail_parts: list[str] = []
        if missing_files:
            detail_parts.append("缺失脚本：" + "、".join(f"`{path}`" for path in missing_files))
        if untouched_files:
            detail_parts.append("未观测到本轮新增或更新：" + "、".join(f"`{path}`" for path in untouched_files))
        raise RuntimeError("Generator Agent 未在节点结束前完成全部脚本落盘。 " + "；".join(detail_parts))
