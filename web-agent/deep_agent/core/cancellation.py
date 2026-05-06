"""LangGraph 取消信号识别工具。"""

from __future__ import annotations


def is_langgraph_user_cancellation(exc: BaseException) -> bool:
    """判断异常是否来自用户主动取消，供宽泛异常捕获前快速放行。"""

    try:
        from langgraph_api.errors import UserInterrupt, UserRollback
    except Exception:  # noqa: BLE001
        return exc.__class__.__name__ in {"UserInterrupt", "UserRollback"}

    return isinstance(exc, (UserInterrupt, UserRollback))
