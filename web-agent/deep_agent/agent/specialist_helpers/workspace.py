"""Specialist 工作目录权限与写文件追踪辅助。

本模块统一处理三件事：把工作目录解析成 Deep Agent 可以消费的 `FilesystemPermission`；
按查询过滤配置生成禁止查询的路径清单；在事件流中跟踪 `write_file` / `edit_file` 的
开始与结束，为阶段产物抽取提供稳定的"成功写入路径集合"。调用方是 `BaseSpecialistAgent`
通过 mixin 继承。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents.middleware import FilesystemPermission

from deep_agent.config.specialist_file_filter import SpecialistFileFilter


class SpecialistWorkspaceMixin:
    """把 Specialist 的 workspace 边界与写文件追踪抽到 mixin 里。

    调用方是 `BaseSpecialistAgent`。通过 mixin 继承后，子类就能直接使用：
    - `_build_workspace_permissions`：按查询过滤配置生成 Deep Agent 文件权限。
    - `_collect_workspace_write_start` / `_collect_workspace_write_result`：在事件流里
      追踪内置文件工具的写入结果。
    - `_normalize_workspace_relative_path`：把任意路径归一化到当前 workspace 相对路径。
    """

    agent_type: str

    def _build_workspace_permissions(
        self,
        workspace_dir: Path,
        *,
        allow_workspace_writes: bool,
    ) -> list[FilesystemPermission]:
        """根据当前 Specialist 配置构建 workspace 级别的文件权限。"""

        workspace_path = workspace_dir.resolve().as_posix()
        permissions: list[FilesystemPermission] = []
        denied_read_paths = self._build_query_filter_read_paths(
            workspace_dir=workspace_dir,
            query_filter_config=self._get_runtime_config().query_filter_config,
        )
        for denied_path in denied_read_paths:
            permissions.append(FilesystemPermission(operations=["read"], paths=[denied_path], mode="deny"))

        permissions.append(
            FilesystemPermission(
                operations=["read"],
                paths=[workspace_path, f"{workspace_path}/**"],
                mode="allow",
            )
        )
        permissions.append(FilesystemPermission(operations=["read"], paths=["/**"], mode="deny"))

        if allow_workspace_writes:
            permissions.append(
                FilesystemPermission(
                    operations=["write"],
                    paths=[workspace_path, f"{workspace_path}/**"],
                    mode="allow",
                )
            )
        permissions.append(FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"))
        return permissions

    def _build_query_filter_read_paths(
        self,
        *,
        workspace_dir: Path,
        query_filter_config: SpecialistFileFilter,
    ) -> list[str]:
        """把查询过滤配置展开成 workspace 作用域下的绝对 deny 路径列表。"""

        blocked_paths: list[str] = []
        for pattern in query_filter_config.blocked_path_globs:
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, pattern))

        for extension in query_filter_config.blocked_file_extensions:
            normalized_extension = extension if extension.startswith(".") else f".{extension}"
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, f"*{normalized_extension}"))
            blocked_paths.append(self._resolve_workspace_query_glob(workspace_dir, f"**/*{normalized_extension}"))

        deduplicated_paths: list[str] = []
        seen: set[str] = set()
        for path in blocked_paths:
            if path in seen:
                continue
            seen.add(path)
            deduplicated_paths.append(path)
        return deduplicated_paths

    def _resolve_workspace_query_glob(self, workspace_dir: Path, pattern: str) -> str:
        """把相对 workspace 的查询 glob 转成绝对权限路径。"""

        normalized_pattern = pattern.strip().replace("\\", "/")
        if not normalized_pattern:
            raise ValueError("查询过滤规则不允许空路径模式。")
        if normalized_pattern.startswith("/"):
            return normalized_pattern

        normalized_pattern = normalized_pattern[2:] if normalized_pattern.startswith("./") else normalized_pattern
        workspace_path = workspace_dir.resolve().as_posix()
        if normalized_pattern in {".", ""}:
            return workspace_path
        return f"{workspace_path}/{normalized_pattern}"

    def _build_query_guard_prompt(self, runtime_config: Any) -> str:
        """构建所有 Specialist 共用的文件查询约束提示词。"""

        guidance_lines = [
            "- 如果需要查询文件，先使用 `ls` 观察候选目录结构，再把范围缩小到最小必要的单个子目录或单个文件。",
            "- 不要直接对 `project_dir`、`workspace_dir` 或其他大目录执行 `glob=\"**/*\"`、递归 `grep` 或无范围全量搜索。",
            "- `grep` 首次检索优先使用默认的 `files_with_matches`；只有缩小到少量候选文件后，才使用 `output_mode=\"content\"` 查看正文。",
        ]
        query_filter_config = runtime_config.query_filter_config
        if query_filter_config.blocked_path_globs:
            blocked_paths = ", ".join(f"`{pattern}`" for pattern in query_filter_config.blocked_path_globs)
            guidance_lines.append(f"- 禁止查询这些路径模式：{blocked_paths}")
        if query_filter_config.blocked_file_extensions:
            blocked_types = ", ".join(f"`{suffix}`" for suffix in query_filter_config.blocked_file_extensions)
            guidance_lines.append(f"- 禁止查询这些文件类型：{blocked_types}")
        return "## 文件查询约束\n" + "\n".join(guidance_lines)

    def _tool_output_is_error(self, output: Any) -> bool:
        """判断工具输出是否表示失败。"""

        status = getattr(output, "status", None)
        if status == "error":
            return True

        if isinstance(output, dict):
            if output.get("status") == "error":
                return True
            content = output.get("content")
            if isinstance(content, str) and content.lstrip().startswith("Error:"):
                return True

        content = getattr(output, "content", None)
        if isinstance(content, str) and content.lstrip().startswith("Error:"):
            return True

        return False

    def _collect_workspace_write_start(
        self,
        *,
        event: dict[str, Any],
        workspace_dir: Path | None,
        pending_write_paths: list[str],
    ) -> None:
        """在写文件工具开始时记录目标路径。"""

        if workspace_dir is None:
            return
        if event.get("name") not in {"write_file", "edit_file"} or event.get("event") != "on_tool_start":
            return

        payload = event.get("data", {}).get("input")
        if not isinstance(payload, dict):
            return

        relative_path = self._normalize_workspace_relative_path(
            workspace_dir,
            payload.get("file_path") if "file_path" in payload else payload.get("path"),
        )
        if relative_path:
            pending_write_paths.append(relative_path)

    def _collect_workspace_write_result(
        self,
        *,
        event: dict[str, Any],
        pending_write_paths: list[str],
        successful_write_paths: set[str],
    ) -> None:
        """在写文件工具结束时记录成功写入的 workspace 相对路径。"""

        if event.get("name") not in {"write_file", "edit_file"}:
            return

        if event.get("event") == "on_tool_error":
            if pending_write_paths:
                pending_write_paths.pop(0)
            return

        if event.get("event") != "on_tool_end":
            return

        relative_path = pending_write_paths.pop(0) if pending_write_paths else None
        if relative_path is None:
            return

        output = event.get("data", {}).get("output")
        if not self._tool_output_is_error(output):
            successful_write_paths.add(relative_path)

    def _normalize_workspace_relative_path(self, workspace_dir: Path, value: Any) -> str | None:
        """把绝对或相对路径归一化为 workspace 内的相对路径。"""

        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_dir / candidate

        resolved_workspace = workspace_dir.resolve()
        resolved_candidate = candidate.resolve()
        try:
            return resolved_candidate.relative_to(resolved_workspace).as_posix()
        except ValueError:
            return None
