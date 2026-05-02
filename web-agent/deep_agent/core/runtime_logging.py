"""项目运行期日志工具。"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from typing import Any


_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_NOISY_EXTERNAL_LOGGER_LEVELS = {
    "watchfiles.main": logging.WARNING,
}


def configure_logging(level: str | None = None) -> None:
    """配置项目统一日志格式与等级。

    Args:
        level: 日志等级，例如 `INFO`、`DEBUG`。

    Returns:
        None.

    Raises:
        None.
    """

    resolved_level = _resolve_log_level(level)
    root_logger = logging.getLogger()

    if not root_logger.handlers:
        logging.basicConfig(level=resolved_level, format=_LOG_FORMAT)

    root_logger.setLevel(resolved_level)
    _configure_json_log_args()
    _configure_noisy_external_loggers()


def configure_logging_from_env() -> None:
    """从环境变量中读取日志等级并初始化日志系统。"""

    configure_logging(os.getenv("LOG_LEVEL", "INFO"))


def get_logger(name: str) -> logging.Logger:
    """返回模块级日志对象。"""

    return logging.getLogger(name)


def _configure_noisy_external_loggers() -> None:
    """降低第三方开发工具的噪音日志，不影响项目自己的 INFO 日志。"""

    for logger_name, logger_level in _NOISY_EXTERNAL_LOGGER_LEVELS.items():
        logging.getLogger(logger_name).setLevel(logger_level)


def _configure_json_log_args() -> None:
    """把项目日志中的结构化参数统一格式化为 JSON 字符串。"""

    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_deep_agent_json_args", False):
        return

    def json_log_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current_factory(*args, **kwargs)
        if str(record.name or "").startswith("deep_agent"):
            record.args = _json_format_log_args(record.args)
        return record

    json_log_record_factory._deep_agent_json_args = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(json_log_record_factory)


def _json_format_log_args(args: Any) -> Any:
    """转换 logging `%s` 参数中的结构化对象。"""

    if not args:
        return args
    if isinstance(args, tuple):
        return tuple(_json_format_log_arg(arg) for arg in args)
    if isinstance(args, Mapping):
        return args
    return _json_format_log_arg(args)


def _json_format_log_arg(arg: Any) -> Any:
    """必要时把单个日志参数转成可复制解析的 JSON。"""

    if _looks_like_message(arg) or _looks_like_structured_value(arg):
        return _json_dumps_for_log(arg)
    return arg


def _looks_like_structured_value(value: Any) -> bool:
    """判断值是否应该按 JSON 打印。"""

    if isinstance(value, Mapping):
        return True
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return True
    return callable(getattr(value, "model_dump", None))


def _json_dumps_for_log(value: Any) -> str:
    """把日志对象转成单行 JSON 字符串。"""

    serialized = _serialize_for_log(
        value,
        max_text_length=12000,
        include_full_messages=True,
    )
    try:
        return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return json.dumps(str(serialized), ensure_ascii=False, separators=(",", ":"))


def log_title(stage: str, step: str, node_name: str | None = None) -> str:
    """生成统一的日志标题。

    Args:
        stage: 当前阶段，例如 `初始化`、`执行`、`路由`、`工具`。
        step: 当前环节，例如 `配置加载`、`节点入参`、`MCP连接`。
        node_name: 可选 LangGraph 节点名，存在时写入标题前缀。

    Returns:
        str: 标准化日志标题，例如 `【初始化@@配置加载】` 或 `【master_node@@执行@@节点入参】`。

    Raises:
        None.
    """

    if node_name:
        return f"【{node_name}@@{stage}@@{step}】"
    return f"【{stage}@@{step}】"


def summarize_settings(settings: Any) -> dict[str, Any]:
    """提取可安全打印的配置摘要。"""

    return {
        "master_model": getattr(settings, "master_model", None),
        "specialist_model": getattr(settings, "specialist_model", None),
        "default_automation_project_root": str(
            getattr(settings, "resolved_default_automation_project_root", getattr(settings, "default_automation_project_root", None))
        ),
        "llm_timeout_seconds": getattr(settings, "llm_timeout_seconds", None),
        "stream_chunk_timeout_seconds": getattr(settings, "resolved_stream_chunk_timeout_seconds", None),
        "specialist_recursion_limit": getattr(settings, "specialist_recursion_limit", None),
        "max_conversation_turns": getattr(settings, "max_conversation_turns", None),
        "pwtest_headed": getattr(settings, "pwtest_headed", None),
        "playwright_bootstrap_workspace": getattr(settings, "playwright_bootstrap_workspace", None),
        "playwright_skip_browser_download": getattr(settings, "playwright_skip_browser_download", None),
        "playwright_test_package": getattr(settings, "playwright_test_package", None),
        "langsmith_project": getattr(settings, "langsmith_project", None),
        "langsmith_tracing": getattr(settings, "langsmith_tracing", None),
        "log_level": getattr(settings, "log_level", None),
        "agent_debug_trace": getattr(settings, "agent_debug_trace", None),
        "agent_debug_full_messages": getattr(settings, "agent_debug_full_messages", None),
        "agent_debug_max_chars": getattr(settings, "agent_debug_max_chars", None),
        "has_openai_api_key": bool(getattr(settings, "openai_api_key", None)),
        "has_openai_base_url": bool(getattr(settings, "openai_base_url", None)),
    }


def summarize_model_kwargs(model_kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """提取可安全打印的模型初始化参数摘要。"""

    return {
        "model": model_kwargs.get("model"),
        "model_provider": model_kwargs.get("model_provider"),
        "timeout": model_kwargs.get("timeout"),
        "stream_chunk_timeout": model_kwargs.get("stream_chunk_timeout"),
        "max_retries": model_kwargs.get("max_retries"),
        "use_responses_api": model_kwargs.get("use_responses_api"),
        "has_api_key": bool(model_kwargs.get("api_key")),
        "has_base_url": bool(model_kwargs.get("base_url")),
    }


def summarize_messages(messages: Sequence[Any], max_items: int = 3, max_text_length: int = 80) -> list[dict[str, Any]]:
    """把消息列表压缩成便于日志打印的简短摘要。"""

    summarized: list[dict[str, Any]] = []

    for index, message in enumerate(messages[:max_items], start=1):
        if isinstance(message, Mapping):
            content = message.get("content", "")
            message_type = message.get("type") or message.get("role") or message.__class__.__name__
        else:
            content = getattr(message, "content", "")
            message_type = message.__class__.__name__
        if not isinstance(content, str):
            content = str(content)
        if len(content) > max_text_length:
            content = f"{content[:max_text_length]}..."

        summarized.append(
            {
                "index": index,
                "type": str(message_type),
                "content": content,
            }
        )

    if len(messages) > max_items:
        summarized.append({"remaining_count": len(messages) - max_items})

    return summarized


def summarize_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """把工作流状态压缩成适合日志输出的结构。"""

    return {
        "agent_type": state.get("agent_type"),
        "next_action": state.get("next_action"),
        "requested_pipeline": state.get("requested_pipeline"),
        "pipeline_cursor": state.get("pipeline_cursor"),
        "missing_params": state.get("missing_params"),
        "extracted_params_keys": sorted(state.get("extracted_params", {}).keys()),
        "latest_artifact_stages": sorted((state.get("latest_artifacts") or {}).keys()),
        "artifact_history_count": len(state.get("artifact_history", [])),
        "pending_stage_summaries_count": len(state.get("pending_stage_summaries", [])),
        "messages": summarize_messages(state.get("messages", [])),
    }


def debug_trace_enabled(settings: Any | None = None) -> bool:
    """判断是否开启本地深度调试日志。"""

    configured_value = getattr(settings, "agent_debug_trace", None)
    if configured_value is not None:
        return bool(configured_value)

    return _read_bool_env("AGENT_DEBUG_TRACE", default=False)


def debug_full_messages_enabled(settings: Any | None = None) -> bool:
    """判断是否允许把完整模型消息和提示词写入本地日志。"""

    configured_value = getattr(settings, "agent_debug_full_messages", None)
    if configured_value is not None:
        return bool(configured_value)

    return _read_bool_env("AGENT_DEBUG_FULL_MESSAGES", default=False)


def debug_max_chars(settings: Any | None = None) -> int:
    """返回调试日志中单段文本允许打印的最大字符数。"""

    configured_value = getattr(settings, "agent_debug_max_chars", None)
    if configured_value is None:
        configured_value = os.getenv("AGENT_DEBUG_MAX_CHARS", "4000")

    try:
        return max(1, int(configured_value))
    except (TypeError, ValueError):
        return 4000


def build_trace_context(
    config: Mapping[str, Any] | None = None,
    *,
    node_name: str | None = None,
    event_name: str | None = None,
) -> dict[str, str | None]:
    """从 LangGraph/LangChain config 中提取便于 grep 的 trace 标识。

    LangGraph 的会话概念主要落在 `thread_id` 上，所以这里把没有显式
    `session_id` 的请求统一回退到 `thread_id`，方便后续按一次对话查询日志。
    """

    safe_config = config or {}
    configurable = _as_mapping(safe_config.get("configurable"))
    metadata = _as_mapping(safe_config.get("metadata"))

    thread_id = _first_text(
        configurable.get("thread_id"),
        metadata.get("thread_id"),
        safe_config.get("thread_id"),
    )
    session_id = _first_text(
        configurable.get("session_id"),
        metadata.get("session_id"),
        thread_id,
    )
    run_id = _first_text(
        configurable.get("run_id"),
        metadata.get("run_id"),
        metadata.get("langgraph_run_id"),
        safe_config.get("run_id"),
    )

    return {
        "session_id": session_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "node_name": node_name,
        "event_name": event_name,
    }


def with_trace_context(
    config: Mapping[str, Any] | None,
    trace_context: Mapping[str, Any],
    *,
    recursion_limit: int | None = None,
) -> dict[str, Any]:
    """把 trace 标识和运行时执行参数合并进子 Runnable config。"""

    merged_config: dict[str, Any] = dict(config or {})
    metadata = dict(_as_mapping(merged_config.get("metadata")))
    configurable = dict(_as_mapping(merged_config.get("configurable")))

    for key in ("session_id", "thread_id", "run_id"):
        value = trace_context.get(key)
        if value is None:
            continue
        metadata.setdefault(key, value)
        configurable.setdefault(key, value)

    merged_config["metadata"] = metadata
    merged_config["configurable"] = configurable
    if recursion_limit is not None:
        merged_config.setdefault("recursion_limit", recursion_limit)
    return merged_config


def format_state_for_log(state: Mapping[str, Any], settings: Any | None = None) -> dict[str, Any]:
    """根据调试开关返回摘要 state 或完整可 grep state。"""

    if not debug_trace_enabled(settings):
        return summarize_state(state)

    return serialize_state(
        state,
        include_full_messages=debug_full_messages_enabled(settings),
        max_text_length=debug_max_chars(settings),
    )


def format_messages_for_log(messages: Sequence[Any], settings: Any | None = None) -> list[dict[str, Any]]:
    """根据调试开关返回摘要消息或完整消息结构。"""

    if not debug_trace_enabled(settings) or not debug_full_messages_enabled(settings):
        return summarize_messages(messages)

    return serialize_messages(messages, max_text_length=debug_max_chars(settings))


def format_value_for_log(value: Any, settings: Any | None = None) -> Any:
    """把任意对象转换成适合写入日志的结构。"""

    return _serialize_for_log(
        value,
        max_text_length=debug_max_chars(settings) if debug_trace_enabled(settings) else 240,
        include_full_messages=debug_full_messages_enabled(settings),
    )


def log_debug_event(
    logger_obj: logging.Logger,
    settings: Any | None,
    title: str,
    event_name: str,
    trace_context: Mapping[str, Any],
    **payload: Any,
) -> None:
    """仅在调试开关开启时打印结构化调试事件。"""

    if not debug_trace_enabled(settings):
        return

    logger_obj.info("%s event=%s trace=%s payload=%s",
        _title_with_node(title, trace_context.get("node_name")), event_name, dict(trace_context), _serialize_for_log(payload, max_text_length=debug_max_chars(settings), include_full_messages=debug_full_messages_enabled(settings)),)


def _title_with_node(title: str, node_name: Any | None) -> str:
    """把节点名补进现有日志标题前缀。"""

    if not node_name or not title.endswith("】"):
        return title
    node_text = str(node_name)
    if title.startswith(f"【{node_text}@@"):
        return title
    return f"【{node_text}@@{title[1:]}"


def serialize_state(
    state: Mapping[str, Any],
    *,
    include_full_messages: bool,
    max_text_length: int,
) -> dict[str, Any]:
    """把 LangGraph state 转成稳定的日志结构。"""

    serialized: dict[str, Any] = {}
    for key, value in state.items():
        if key == "messages" and isinstance(value, Sequence) and not isinstance(value, str):
            serialized[key] = (
                serialize_messages(value, max_text_length=max_text_length)
                if include_full_messages
                else summarize_messages(value)
            )
            continue

        serialized[key] = _serialize_for_log(
            value,
            max_text_length=max_text_length,
            include_full_messages=include_full_messages,
        )

    return serialized


def serialize_messages(messages: Sequence[Any], *, max_text_length: int) -> list[dict[str, Any]]:
    """序列化消息列表，保留 System/Human/AI/Tool 等消息类型。"""

    return [serialize_message(message, max_text_length=max_text_length) for message in messages]


def serialize_message(message: Any, *, max_text_length: int) -> dict[str, Any]:
    """序列化单条 LangChain 消息或兼容字典。"""

    if isinstance(message, Mapping):
        message_type = message.get("type") or message.get("role") or message.__class__.__name__
        serialized = {
            "type": str(message_type),
            "content": _serialize_for_log(
                message.get("content"),
                max_text_length=max_text_length,
                include_full_messages=True,
            ),
        }
        for key in _MESSAGE_EXTRA_KEYS:
            if key in message:
                serialized[key] = _serialize_for_log(
                    message[key],
                    max_text_length=max_text_length,
                    include_full_messages=True,
                )
        return serialized

    serialized = {
        "type": message.__class__.__name__,
        "message_type": getattr(message, "type", None),
        "content": _serialize_for_log(
            getattr(message, "content", ""),
            max_text_length=max_text_length,
            include_full_messages=True,
        ),
    }
    for key in _MESSAGE_EXTRA_KEYS:
        if hasattr(message, key):
            serialized[key] = _serialize_for_log(
                getattr(message, key),
                max_text_length=max_text_length,
                include_full_messages=True,
            )
    return serialized


def serialize_tools_for_log(tools: Sequence[Any], *, max_text_length: int = 4000) -> list[dict[str, Any]]:
    """把 LangChain Tool 列表转成可读日志结构。"""

    serialized_tools: list[dict[str, Any]] = []
    for tool in tools:
        serialized_tools.append(
            {
                "name": getattr(tool, "name", None),
                "description": _truncate_text(str(getattr(tool, "description", "")), max_text_length),
                "args": _serialize_for_log(
                    getattr(tool, "args", None),
                    max_text_length=max_text_length,
                    include_full_messages=True,
                ),
            }
        )
    return serialized_tools


def _resolve_log_level(level: str | None) -> int:
    """把字符串日志等级转换成 logging 常量。"""

    if not level:
        return logging.INFO

    return getattr(logging, level.upper(), logging.INFO)


_MESSAGE_EXTRA_KEYS = (
    "id",
    "name",
    "tool_call_id",
    "tool_calls",
    "invalid_tool_calls",
    "additional_kwargs",
    "response_metadata",
    "usage_metadata",
    "artifact",
    "status",
)


def _serialize_for_log(
    value: Any,
    *,
    max_text_length: int,
    include_full_messages: bool,
) -> Any:
    """递归转换日志对象，避免直接打印不可读的 Python repr。"""

    if isinstance(value, os.PathLike):
        return os.fspath(value)

    if callable(getattr(value, "model_dump", None)):
        return _serialize_for_log(
            value.model_dump(),
            max_text_length=max_text_length,
            include_full_messages=include_full_messages,
        )

    if _looks_like_message(value):
        return (
            serialize_message(value, max_text_length=max_text_length)
            if include_full_messages
            else summarize_messages([value])[0]
        )

    if isinstance(value, Mapping):
        return {
            str(key): _serialize_for_log(
                item,
                max_text_length=max_text_length,
                include_full_messages=include_full_messages,
            )
            for key, item in value.items()
        }

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if value and all(_looks_like_message(item) for item in value):
            return (
                serialize_messages(value, max_text_length=max_text_length)
                if include_full_messages
                else summarize_messages(value)
            )
        return [
            _serialize_for_log(
                item,
                max_text_length=max_text_length,
                include_full_messages=include_full_messages,
            )
            for item in value
        ]

    if isinstance(value, str):
        return _truncate_text(value, max_text_length)

    if isinstance(value, (int, float, bool)) or value is None:
        return value

    return _truncate_text(repr(value), max_text_length)


def _truncate_text(text: str, max_text_length: int) -> str:
    """按字符数截断文本，保留 grep 友好的前缀。"""

    if len(text) <= max_text_length:
        return text
    return f"{text[:max_text_length]}..."


def _looks_like_message(value: Any) -> bool:
    """宽松判断对象是否像 LangChain 消息。"""

    if isinstance(value, Mapping):
        return "content" in value and ("type" in value or "role" in value)

    return hasattr(value, "content") and hasattr(value, "type")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """把可能为空或非法的配置段安全转成 Mapping。"""

    if isinstance(value, Mapping):
        return value
    return {}


def _first_text(*values: Any) -> str | None:
    """返回第一个非空值的字符串形式。"""

    for value in values:
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return None


def _read_bool_env(name: str, *, default: bool) -> bool:
    """读取常见布尔环境变量写法。"""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
