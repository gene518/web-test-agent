"""不发起计费请求的模型配置诊断。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from deep_agent.model.profiles import resolve_model_capabilities

if TYPE_CHECKING:
    from deep_agent.core.config import AppSettings


def collect_model_diagnostics(settings: AppSettings) -> list[dict[str, Any]]:
    """返回可安全写日志的 Master/Specialist 模型摘要。"""

    diagnostics: list[dict[str, Any]] = []
    for role in ("master", "specialist"):
        connection = settings.resolve_model_connection(role)
        capabilities = resolve_model_capabilities(connection)
        warnings: list[str] = []
        if not connection.api_key:
            warnings.append("未检测到 API Key；仅在服务端无需认证时可正常调用。")
        if connection.family == "minimax" and connection.thinking == "disabled" and not capabilities.thinking_can_disable:
            warnings.append("MiniMax M2.x 的 thinking 不能关闭，配置将按开启处理。")
        diagnostics.append(
            {
                "role": role,
                "family": connection.family,
                "channel": connection.channel,
                "protocol": connection.protocol,
                "model": connection.api_model_name,
                "structured_output_strategy": capabilities.structured_output_strategy,
                "thinking": connection.thinking,
                "max_input_tokens": capabilities.max_input_tokens,
                "max_output_tokens": capabilities.max_output_tokens,
                "has_api_key": bool(connection.api_key),
                "has_base_url": bool(connection.base_url),
                "warnings": warnings,
            }
        )
    return diagnostics


def main() -> None:
    """输出不包含密钥和提示词的本地模型诊断。"""

    from deep_agent.core.config import get_settings

    print(json.dumps(collect_model_diagnostics(get_settings()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
