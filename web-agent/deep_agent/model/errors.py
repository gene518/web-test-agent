"""模型适配层对外暴露的安全错误类型。"""

from __future__ import annotations

from typing import Any


class ModelAdapterError(RuntimeError):
    """携带稳定错误码、但不向用户泄漏请求正文或凭证的模型错误。"""

    code = "model_adapter_error"

    def __init__(self, message: str, *, context: dict[str, Any] | None = None, cause: Exception | None = None) -> None:
        super().__init__(f"[{self.code}] {message}")
        self.safe_message = message
        self.context = dict(context or {})
        self.__cause__ = cause

    def diagnostic_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.safe_message, **self.context}


class ModelConfigurationError(ModelAdapterError):
    code = "model_configuration_error"


class StructuredOutputError(ModelAdapterError):
    code = "structured_output_error"


class ToolProtocolError(ModelAdapterError):
    code = "tool_protocol_error"


class ModelInvocationError(ModelAdapterError):
    code = "model_invocation_error"
