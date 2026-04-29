"""各 Specialist 的文件查询限制配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpecialistQueryFilterConfig:
    """描述单个 Specialist 的文件查询限制规则。

    `blocked_path_globs` 用于屏蔽明显无关或高风险的大目录；
    `blocked_file_extensions` 用于屏蔽容易把上下文打爆的文件类型。
    两者都会由 Agent 在运行时转换成 workspace 作用域内的读权限 deny 规则。
    """

    blocked_path_globs: tuple[str, ...] = ()
    blocked_file_extensions: tuple[str, ...] = ()


SPECIALIST_BLOCKED_QUERY_PATH_GLOBS: tuple[str, ...] = (
    "node_modules",
    "node_modules/**",
    "**/node_modules",
    "**/node_modules/**",
    "test-results",
    "test-results/**",
    "**/test-results",
    "**/test-results/**",
)

# 注意：`.playwright-mcp/**` 故意不在这里屏蔽；该目录是 Playwright MCP 的文本化调试产物，
# Healer 等阶段需要按需查询其中的 `.yml` / `.log` 文件。
SPECIALIST_BLOCKED_QUERY_FILE_EXTENSIONS: tuple[str, ...] = (
    ".trace",
    ".zip",
    ".network",
    ".webm",
)


PLAN_QUERY_FILTER_CONFIG = SpecialistQueryFilterConfig(
    blocked_path_globs=SPECIALIST_BLOCKED_QUERY_PATH_GLOBS,
    blocked_file_extensions=SPECIALIST_BLOCKED_QUERY_FILE_EXTENSIONS,
)

GENERATOR_QUERY_FILTER_CONFIG = SpecialistQueryFilterConfig(
    blocked_path_globs=SPECIALIST_BLOCKED_QUERY_PATH_GLOBS,
    blocked_file_extensions=SPECIALIST_BLOCKED_QUERY_FILE_EXTENSIONS,
)

HEALER_QUERY_FILTER_CONFIG = SpecialistQueryFilterConfig(
    blocked_path_globs=SPECIALIST_BLOCKED_QUERY_PATH_GLOBS,
    blocked_file_extensions=SPECIALIST_BLOCKED_QUERY_FILE_EXTENSIONS,
)


__all__ = [
    "SpecialistQueryFilterConfig",
    "GENERATOR_QUERY_FILTER_CONFIG",
    "HEALER_QUERY_FILTER_CONFIG",
    "PLAN_QUERY_FILTER_CONFIG",
    "SPECIALIST_BLOCKED_QUERY_FILE_EXTENSIONS",
    "SPECIALIST_BLOCKED_QUERY_PATH_GLOBS",
]
