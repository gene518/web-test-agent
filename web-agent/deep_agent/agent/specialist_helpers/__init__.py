"""Specialist 专用的 mixin 与工具函数集合。

本包把 Plan / Generator / Healer 共用的运行期能力收拢到一处：
- `SpecialistExecutionContext` / `SpecialistRuntimeConfig` 承载运行时配置。
- `SpecialistDisplayMixin` / `SpecialistWorkspaceMixin` / `SpecialistLoggingMixin`
  拆分展示、workspace 边界、日志这三块非主流程的能力。
- `input_resolution` / `browser_close` 提供跨 Specialist 的输入归一化与异常识别。

调用方是 `BaseSpecialistAgent` 及三个 Specialist 子类；通过集中在这里暴露一组稳定入口，
避免每个 Specialist 重复实现功能相同、文案略有差别的私有方法。
"""

from deep_agent.agent.specialist_helpers.browser_close import (
    EXPECTED_BROWSER_CLOSE_FRAGMENTS,
    is_expected_browser_close_error,
)
from deep_agent.agent.specialist_helpers.display import SpecialistDisplayMixin
from deep_agent.agent.specialist_helpers.input_resolution import (
    bundled_demo_template_dir,
    normalize_runtime_text,
    normalize_string_list,
    resolve_workspace_scoped_files,
)
from deep_agent.agent.specialist_helpers.logging import SpecialistLoggingMixin
from deep_agent.agent.specialist_helpers.types import SpecialistExecutionContext, SpecialistRuntimeConfig
from deep_agent.agent.specialist_helpers.workspace import (
    SpecialistWorkspaceMixin,
    display_workspace_child_path_for_agent_prompt,
    display_workspace_for_agent_prompt,
    is_windows_platform,
    virtual_workspace_root_path,
)


__all__ = [
    "EXPECTED_BROWSER_CLOSE_FRAGMENTS",
    "SpecialistDisplayMixin",
    "SpecialistExecutionContext",
    "SpecialistLoggingMixin",
    "SpecialistRuntimeConfig",
    "SpecialistWorkspaceMixin",
    "bundled_demo_template_dir",
    "display_workspace_child_path_for_agent_prompt",
    "display_workspace_for_agent_prompt",
    "is_expected_browser_close_error",
    "is_windows_platform",
    "normalize_runtime_text",
    "normalize_string_list",
    "resolve_workspace_scoped_files",
    "virtual_workspace_root_path",
]
