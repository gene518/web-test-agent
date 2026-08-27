"""跨供应商模型适配公共入口。"""

from .errors import ModelAdapterError, ModelConfigurationError, ModelInvocationError, StructuredOutputError, ToolProtocolError
from .factory import ProviderCompatibleChatModel, adapt_chat_model
from .messages import append_system_instruction, normalize_messages
from .profiles import resolve_model_capabilities
from .settings import ModelConnectionSettings, ResolvedModelConnection
from .structured_output import StructuredResult, invoke_structured
from .tools import adapt_tool_binding, validate_tool_set

__all__ = [
    "ModelAdapterError",
    "ModelConfigurationError",
    "ModelInvocationError",
    "StructuredOutputError",
    "ToolProtocolError",
    "ModelConnectionSettings",
    "ProviderCompatibleChatModel",
    "ResolvedModelConnection",
    "StructuredResult",
    "adapt_chat_model",
    "adapt_tool_binding",
    "append_system_instruction",
    "invoke_structured",
    "normalize_messages",
    "resolve_model_capabilities",
    "validate_tool_set",
]
