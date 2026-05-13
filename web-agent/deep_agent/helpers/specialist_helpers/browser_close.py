"""Playwright 浏览器关闭后的预期异常识别。

Plan / Generator / Healer 三个 Specialist 在保存完成或调试通过后会主动关掉浏览器，
之后底层 httpx / Playwright MCP 会抛出一类"浏览器已关闭"相关异常。它们不是真正的
执行失败，而是收尾阶段的预期副作用。本模块把这一判定集中起来，避免在每个 Specialist
里重复维护同样的字符串 fragments 列表，也方便在接入新 MCP 或客户端时统一升级策略。

设计要点：
- 优先按异常类型命中（如 `httpx.RemoteProtocolError`、`anyio.EndOfStream` 等），因为
  异常类型对 Playwright/httpx 升级更稳定。
- 类型命中不到时再退化到字符串 fragment 匹配，用 `.lower()` 做大小写不敏感检测。
- 字符串 fragments 集中在一处，后续维护（新增/删除）只改这一份。
"""

from __future__ import annotations

from typing import Tuple, Type


# 收尾阶段可预期的异常消息 fragments；使用小写形式匹配 `str(exc).lower()`。
EXPECTED_BROWSER_CLOSE_FRAGMENTS: tuple[str, ...] = (
    "target page, context or browser has been closed",
    "browsercontext.newpage",
    "browser has been closed",
    "remoteprotocolerror",
    "peer closed connection without sending complete message body",
    "incomplete chunked read",
)


def _collect_expected_browser_close_error_types() -> Tuple[Type[BaseException], ...]:
    """收集一组在运行期可识别的"预期关闭"异常类型。

    异常类型匹配比字符串匹配更稳定；这里按"能导入就加入"的方式避免强依赖。
    httpx / anyio 在不同版本下的命名可能略有差异，缺失时不影响 fallback 行为。
    """

    collected: list[type[BaseException]] = []

    try:
        import httpx

        collected.append(httpx.RemoteProtocolError)
    except Exception:  # noqa: BLE001
        pass

    try:
        import anyio

        collected.append(anyio.EndOfStream)
    except Exception:  # noqa: BLE001
        pass

    return tuple(collected)


_EXPECTED_BROWSER_CLOSE_ERROR_TYPES = _collect_expected_browser_close_error_types()


def is_expected_browser_close_error(exc: BaseException) -> bool:
    """判断异常是否为 Playwright 浏览器在收尾阶段关闭引发的预期错误。

    调用方：Plan / Generator / Healer 在执行结束后的 `except` 分支。
    判定方式：先看异常类型，再回退到错误消息 fragments 匹配。任意一条命中即认为是预期。
    """

    if _EXPECTED_BROWSER_CLOSE_ERROR_TYPES and isinstance(exc, _EXPECTED_BROWSER_CLOSE_ERROR_TYPES):
        return True

    text = str(exc).lower()
    return any(fragment in text for fragment in EXPECTED_BROWSER_CLOSE_FRAGMENTS)


__all__ = [
    "EXPECTED_BROWSER_CLOSE_FRAGMENTS",
    "is_expected_browser_close_error",
]
