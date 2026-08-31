"""为 Playwright 与 npm 子进程构造最小运行环境。"""

from __future__ import annotations

import os
from collections.abc import Mapping


# 这些变量只提供操作系统运行时、临时目录、缓存与本地化信息。不要把应用进程的
# 整个环境传给由自动化项目控制的 Node/npm 子进程。
_BASE_ALLOWED_ENV_NAMES = frozenset(
    {
        "APPDATA",
        "CI",
        "COLORTERM",
        "COMSPEC",
        "COREPACK_HOME",
        "FORCE_COLOR",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "LOCALAPPDATA",
        "LOGNAME",
        "NO_COLOR",
        "NPM_CONFIG_AUDIT",
        "NPM_CONFIG_CACHE",
        "NPM_CONFIG_FUND",
        "NPM_CONFIG_LOGLEVEL",
        "NPM_CONFIG_OFFLINE",
        "NPM_CONFIG_PREFER_OFFLINE",
        "NPM_CONFIG_PREFIX",
        "NPM_CONFIG_REGISTRY",
        "NPM_CONFIG_UPDATE_NOTIFIER",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "SHELL",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "TZ",
        "USER",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        # npm 在 POSIX 上也会读取小写变量名，因此保留官方支持的对应变体。
        "npm_config_audit",
        "npm_config_cache",
        "npm_config_fund",
        "npm_config_loglevel",
        "npm_config_offline",
        "npm_config_prefer_offline",
        "npm_config_prefix",
        "npm_config_registry",
        "npm_config_update_notifier",
    }
)

# 这些变量由运行时持有，用于 Node、Playwright、测试和 Scheduler 配置。这里必须
# 保持显式列举，不能放开 ``PLAYWRIGHT_*`` 或 ``PWTEST_*`` 前缀，否则未来可能有
# 凭据形态的变量绕过隔离边界。
_RUNTIME_ALLOWED_ENV_NAMES = frozenset(
    {
        "NODE_COMPILE_CACHE",
        "NODE_DISABLE_COLORS",
        "NODE_DISABLE_COMPILE_CACHE",
        "NODE_EXTRA_CA_CERTS",
        "NODE_NO_WARNINGS",
        "NODE_OPTIONS",
        "NODE_PATH",
        "NODE_V8_COVERAGE",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST",
        "PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT",
        "PLAYWRIGHT_DOWNLOAD_HOST",
        "PLAYWRIGHT_EXECUTABLE_PATH",
        "PLAYWRIGHT_FIREFOX_DOWNLOAD_HOST",
        "PLAYWRIGHT_HOST_PLATFORM_OVERRIDE",
        "PLAYWRIGHT_HTML_OPEN",
        "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD",
        "PLAYWRIGHT_SKIP_BROWSER_GC",
        "PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS",
        "PLAYWRIGHT_WEBKIT_DOWNLOAD_HOST",
        "PWTEST_HEADED",
        "PW_SCHEDULE_PROJECT_NAME",
        "PW_SCHEDULE_TASK_ID",
        "PW_SCHEDULED_FOR",
        "PW_TEST_REPORT_NAME",
    }
)
_APP_RUNTIME_ENV_NAMES = frozenset(
    {
        "WEB_TEST_AGENT_NODE_EXECUTABLE",
        "WEB_TEST_AGENT_PLAYWRIGHT_CLI",
        "WEB_TEST_AGENT_PLAYWRIGHT_MODULES",
    }
)
_CREDENTIAL_MARKERS = (
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "PASSWD",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)
_MODEL_PREFIXES = ("MASTER_LLM__", "SPECIALIST_LLM__")


def build_playwright_child_environment(
    overrides: Mapping[str, str] | None = None,
    *,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """为 Playwright 或 npm 子进程构造明确的最小环境变量集合。

    自动化工程属于用户可控代码，直接继承父进程环境会暴露模型、更新器、SCM 与
    部署凭据。本函数从空环境开始，只复制白名单变量，再写入已知安全的单次调用
    配置，例如 ``PWTEST_HEADED``。

    Args:
        overrides: 通过校验后写入的测试或 Scheduler 单次配置。
        source: 可注入的父进程环境，供确定性测试使用。

    Raises:
        ValueError: 传入的覆盖变量不在子进程白名单中。
    """

    inherited = os.environ if source is None else source
    environment = {
        key: value
        for key, value in inherited.items()
        if _is_allowed_key(key) and isinstance(value, str)
    }
    for key, value in (overrides or {}).items():
        if not _is_allowed_key(key):
            raise ValueError(
                f"Playwright 子进程环境变量不在白名单中：{key}"
            )
        environment[key] = str(value)
    return environment


def _is_allowed_key(key: str) -> bool:
    normalized = key.upper()
    if normalized.startswith(_MODEL_PREFIXES) or _is_credential_key(normalized):
        return False
    return (
        key in _BASE_ALLOWED_ENV_NAMES
        or key in _RUNTIME_ALLOWED_ENV_NAMES
        or key in _APP_RUNTIME_ENV_NAMES
    )


def _is_credential_key(normalized_key: str) -> bool:
    return any(marker in normalized_key for marker in _CREDENTIAL_MARKERS)


__all__ = ["build_playwright_child_environment"]
