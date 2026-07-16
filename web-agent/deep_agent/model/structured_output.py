"""跨供应商结构化输出执行器。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Generic, Mapping, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, ValidationError

from deep_agent.model.capabilities import ModelCapabilities, StructuredOutputStrategy
from deep_agent.model.errors import ModelInvocationError, StructuredOutputError
from deep_agent.model.messages import append_system_instruction, normalize_messages
from deep_agent.model.reasoning import sanitize_reasoning_for_display
from deep_agent.model.settings import ResolvedModelConnection


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class StructuredResult(Generic[StructuredModel]):
    """保留原始响应和解析信息，避免丢失 `None` 的真正原因。"""

    raw: Any
    parsed: StructuredModel
    parsing_error: str | None
    strategy: StructuredOutputStrategy
    attempts: int


async def invoke_structured(
    *,
    model: Any,
    schema: type[StructuredModel],
    messages: list[BaseMessage],
    capabilities: ModelCapabilities,
    connection: ResolvedModelConnection,
    config: RunnableConfig | None = None,
    max_parse_attempts: int = 2,
) -> StructuredResult[StructuredModel]:
    """按能力 Profile 选择结构化输出方式，并对解析失败做一次纠正重试。"""

    strategy = capabilities.structured_output_strategy
    schema_instruction = _build_schema_instruction(schema)
    request_messages = normalize_messages(messages, capabilities)
    if strategy in {"json_mode", "prompted_json"}:
        request_messages = append_system_instruction(request_messages, schema_instruction, capabilities)

    last_raw: Any = None
    last_error: Exception | None = None
    for attempt in range(1, max_parse_attempts + 1):
        try:
            if strategy == "prompted_json":
                last_raw = await model.ainvoke(request_messages, config=config)
                parsed = _parse_message_as_schema(last_raw, schema)
                parsing_error = None
            else:
                runnable = model.with_structured_output(
                    schema,
                    method=strategy,
                    include_raw=True,
                )
                response = await runnable.ainvoke(request_messages, config=config)
                last_raw, parsed, parsing_error = _unpack_structured_response(response, schema)
                if parsed is None:
                    raise ValueError(parsing_error or "模型没有返回可解析的结构化结果。")
            return StructuredResult(
                raw=last_raw,
                parsed=parsed,
                parsing_error=parsing_error,
                strategy=strategy,
                attempts=attempt,
            )
        except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            last_error = exc
            if attempt >= max_parse_attempts:
                break
            request_messages = [
                *request_messages,
                HumanMessage(
                    content=(
                        "上一次输出未通过 JSON Schema 验证。"
                        f"错误：{_safe_error_text(exc)}。"
                        "请只返回修正后的 JSON 对象，不要解释，不要使用 Markdown 代码块。"
                    )
                ),
            ]
        except Exception as exc:  # noqa: BLE001
            raise ModelInvocationError(
                "模型请求失败，请检查模型通道、认证信息和接口参数。",
                context=_model_context(connection, strategy),
                cause=exc,
            ) from exc

    raise StructuredOutputError(
        f"模型连续 {max_parse_attempts} 次未返回符合约定的结构化结果，请检查模型结构化输出能力或切换接入通道。",
        context={
            **_model_context(connection, strategy),
            "parse_error": _safe_error_text(last_error) if last_error else "empty_result",
            "has_raw_response": last_raw is not None,
        },
        cause=last_error,
    )


def _build_schema_instruction(schema: type[BaseModel]) -> str:
    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    return (
        "## JSON 输出契约\n"
        "你必须只返回一个可被标准 JSON 解析器读取的对象，不要添加解释或 Markdown。\n"
        f"输出必须符合以下 JSON Schema：\n{schema_json}"
    )


def _unpack_structured_response(
    response: Any,
    schema: type[StructuredModel],
) -> tuple[Any, StructuredModel | None, str | None]:
    if isinstance(response, Mapping) and {"raw", "parsed"}.issubset(response):
        raw = response.get("raw")
        parsed_value = response.get("parsed")
        parsing_error = response.get("parsing_error")
        parsed = _validate_parsed_value(parsed_value, schema) if parsed_value is not None else None
        return raw, parsed, _safe_error_text(parsing_error) if parsing_error else None
    return response, _validate_parsed_value(response, schema), None


def _validate_parsed_value(value: Any, schema: type[StructuredModel]) -> StructuredModel:
    if isinstance(value, schema):
        return value
    return schema.model_validate(value)


def _parse_message_as_schema(message: Any, schema: type[StructuredModel]) -> StructuredModel:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
            if not isinstance(block, dict) or block.get("type") == "text"
        )
    if not isinstance(content, str):
        raise TypeError("模型响应内容不是字符串。")
    content = sanitize_reasoning_for_display(content)
    decoder = json.JSONDecoder()
    validation_errors: list[Exception] = []
    for index, character in enumerate(content):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
            return schema.model_validate(value)
        except (json.JSONDecodeError, ValidationError) as exc:
            validation_errors.append(exc)
    if validation_errors:
        raise ValueError(_safe_error_text(validation_errors[-1])) from validation_errors[-1]
    raise json.JSONDecodeError("响应中没有 JSON 对象", content, 0)


def _model_context(
    connection: ResolvedModelConnection,
    strategy: StructuredOutputStrategy,
) -> dict[str, Any]:
    return {
        "role": connection.role,
        "family": connection.family,
        "channel": connection.channel,
        "model": connection.api_model_name,
        "strategy": strategy,
    }


def _safe_error_text(error: Any) -> str:
    text = str(error).strip() or error.__class__.__name__
    return text if len(text) <= 500 else f"{text[:500]}... [truncated]"
