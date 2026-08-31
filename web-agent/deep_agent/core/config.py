"""应用配置与环境变量解析。"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from deep_agent.model.errors import ModelConfigurationError
from deep_agent.model.settings import (
    ModelConnectionSettings,
    ModelRole,
    ResolvedModelConnection,
)
from deep_agent.core.runtime_logging import (
    configure_logging,
    configure_logging_from_env,
    get_logger,
    log_title,
    summarize_settings,
)


logger = get_logger(__name__)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _discover_default_env_file(
    project_root: Path,
    explicit_env_file: str | None,
) -> Path:
    """解析源码仓库或 Windows 便携目录中的配置文件。"""

    if explicit_env_file:
        return Path(explicit_env_file).expanduser().resolve()

    portable_env_file = project_root.parent.parent / "config" / ".env"
    if project_root.parent.name.lower() == "runtime" and portable_env_file.is_file():
        return portable_env_file.resolve()
    return (project_root / ".env").resolve()


_DEFAULT_ENV_FILE = _discover_default_env_file(
    _PROJECT_ROOT,
    os.environ.get("WEB_TEST_AGENT_ENV_FILE"),
)


def _default_relative_path_root(project_root: Path, env_file: Path) -> Path:
    """返回源码或便携布局中相对运行路径的稳定基准目录。"""

    if (
        project_root.parent.name.lower() == "runtime"
        and env_file.parent.name.lower() == "config"
    ):
        return env_file.parent.parent.resolve()
    return project_root.resolve()


def load_project_env_file(env_file: str | Path | None = None) -> None:
    """用 UTF-8 把项目 `.env` 注入当前进程环境变量。

    LangGraph CLI 在 Windows 上读取 `langgraph.json -> env` 时会走 `python-dotenv`
    的默认编码分支；系统为中文代码页时，`.env` 中的中文注释会直接触发 GBK
    解码失败。这里统一在应用入口自行按 UTF-8 加载，避免再依赖外部工具的默认编码。
    """

    resolved_env_file = _resolve_env_file_path(env_file)
    if not resolved_env_file.exists():
        return

    try:
        from dotenv import dotenv_values
    except ImportError:
        env_values = _read_fallback_dotenv_values(resolved_env_file)
    else:
        env_values = dotenv_values(resolved_env_file, encoding="utf-8-sig")

    for key, value in env_values.items():
        if not key or value is None or key in os.environ:
            continue
        os.environ[key] = value


def _resolve_env_file_path(env_file: str | Path | None) -> Path:
    """把相对 `.env` 路径统一解析到项目根目录。"""

    if env_file is None:
        return _DEFAULT_ENV_FILE

    candidate = Path(env_file)
    if candidate.is_absolute():
        return candidate
    return (_PROJECT_ROOT / candidate).resolve()


def _read_fallback_dotenv_values(env_file: Path) -> dict[str, str]:
    """在缺少 `python-dotenv` 时回退到最小可用的 UTF-8 `.env` 解析。"""

    env_values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, separator, raw_value = line.partition("=")
        if not separator:
            continue

        normalized_key = key.strip()
        if not normalized_key or any(
            character.isspace() for character in normalized_key
        ):
            continue

        normalized_value = raw_value.strip()
        if (
            len(normalized_value) >= 2
            and normalized_value[0] == normalized_value[-1]
            and normalized_value[0] in {'"', "'"}
        ):
            normalized_value = normalized_value[1:-1]
        env_values[normalized_key] = normalized_value
    return env_values


class AppSettings(BaseSettings):
    """定义项目运行所需的全部环境变量。

    配置统一放在这里，不只是为了“集中存放字段”，更是为了把模型、MCP、日志和默认目录
    这些跨模块依赖收敛成一个稳定入口。这样后续迁移到 LangGraph Dev、LangSmith 或部署环境时，
    调整成本会集中在配置层，而不是散落到多个 Agent 文件中。
    """

    # `SettingsConfigDict` 告诉 Pydantic 去哪里找 `.env` 文件，以及如何解析环境变量。
    model_config = SettingsConfigDict(
        env_file=_DEFAULT_ENV_FILE,
        env_file_encoding="utf-8-sig",
        case_sensitive=False,
        extra="ignore",
        env_nested_delimiter="__",
    )

    master_llm: ModelConnectionSettings = Field(
        default_factory=ModelConnectionSettings,
        description="Master 使用的模型家族、接入通道、模型、凭证和 thinking 配置。",
    )
    specialist_llm: ModelConnectionSettings = Field(
        default_factory=ModelConnectionSettings,
        description="Plan、Generator、Healer 共用的模型连接配置。",
    )
    max_conversation_turns: int = Field(
        default=999,
        description="Master 在长对话中允许保留的最大用户轮次，超过后会压缩历史摘要。",
    )
    llm_timeout_seconds: int = Field(
        default=60,
        description="单次模型调用的超时时间，单位为秒。",
    )
    stream_chunk_timeout_seconds: int | None = Field(
        default=None,
        description=(
            "流式模型调用在连续多久未收到新 chunk 时判定超时，单位为秒；"
            "未配置时默认与 llm_timeout_seconds 保持一致，设为 0 或负数可关闭该静默超时。"
        ),
    )
    specialist_recursion_limit: int = Field(
        default=999,
        description="Specialist Deep Agent 执行时传给 LangGraph 的递归步数上限。",
    )
    log_level: str = Field(
        default="INFO",
        description="项目运行期日志等级，例如 INFO、DEBUG、WARNING。",
    )
    agent_debug_trace: bool = Field(
        default=False,
        description="是否开启本地深度调试日志；开启后会输出节点 state、模型调用、工具调用等 grep 友好事件。",
    )
    agent_debug_full_messages: bool = Field(
        default=False,
        description="是否允许把完整 system/user/ai/tool 消息和最终提示词写入本地日志；仅建议本机调试时开启。",
    )
    agent_debug_max_chars: int = Field(
        default=4000,
        description="深度调试日志中单段文本的最大字符数，用于控制完整提示词和模型消息的日志体积。",
    )
    pwtest_headed: bool = Field(
        default=True,
        description="控制 Playwright Test MCP 是否以有头模式启动浏览器。",
    )
    playwright_bootstrap_workspace: bool = Field(
        default=True,
        description="启动 Playwright Test MCP 前是否自动为自动化项目目录补齐 npm 与 @playwright/test 依赖。",
    )
    playwright_skip_browser_download: bool = Field(
        default=True,
        description="执行 npm install 补齐 Playwright 依赖时，是否注入 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 以跳过浏览器下载。",
    )
    playwright_test_package: str = Field(
        default="@playwright/test@1.61.1",
        description="自动化项目目录缺少 Playwright Test 依赖时执行 npm install 使用的包名或版本规格。当前 Agent 支持的版本为 1.61.1。",
    )
    langsmith_api_key: str | None = Field(
        default=None,
        description="LangSmith 的 API Key，用于链路追踪、调试和观测。",
    )
    langsmith_project: str | None = Field(
        default=None,
        description="LangSmith 项目名；开启 tracing 时用于区分不同运行环境或项目。",
    )
    langsmith_tracing: bool = Field(
        default=False,
        description="是否开启 LangSmith tracing，将运行轨迹上报到 LangSmith。",
    )
    default_automation_project_root: str = Field(
        default="~/webautotest",
        description="自动化工程根目录；Plan 模式会按工程名字在此目录下创建或复用工程。",
    )
    scheduler_config_path: str | None = Field(
        default=None,
        description=(
            "定时任务配置文件路径；未配置时默认使用服务端 `web-agent/scheduler_tasks.json`。"
        ),
    )
    scheduler_poll_interval_seconds: int = Field(
        default=30,
        description="独立定时执行服务轮询配置文件并检查到点任务的时间间隔，单位为秒。",
    )
    scheduler_langgraph_url: str = Field(
        default="http://127.0.0.1:2024",
        description="Scheduler 创建只读监控对话并运行 scheduled-run 图时使用的 LangGraph API 地址。",
    )
    scheduler_langgraph_api_key: str | None = Field(
        default=None,
        description="可选的 LangGraph API Key；本地私有部署通常留空。",
    )
    scheduler_langgraph_timeout_seconds: float = Field(
        default=3600,
        ge=1,
        description="Scheduler 等待 scheduled-run 图完成的 HTTP 超时时间。",
    )
    scheduler_scheduled_run_graph_id: str = Field(
        default="web-autotest-scheduled-run",
        description="定时执行、分析与自动修复使用的独立 LangGraph 图 ID。",
    )
    scheduler_monitor_heartbeat_seconds: int = Field(
        default=30,
        ge=1,
        description="有新输出时向只读监控对话发布进度心跳的最小间隔。",
    )
    scheduler_auto_heal_enabled: bool = Field(
        default=True,
        description="是否允许 scheduled-run 对高置信测试自动化问题调用一次 Healer。",
    )
    scheduler_auto_heal_confidence_threshold: float = Field(
        default=0.8,
        ge=0,
        le=1,
        description="允许自动 Healer 的最低模型归因置信度。",
    )

    @property
    def playwright_mcp_env(self) -> dict[str, str]:
        """返回 Playwright Test MCP 所需的环境变量。

        Returns:
            dict[str, str]: 启动 Playwright MCP 子进程时要注入的环境变量。

        Raises:
            None.
        """

        return {"PWTEST_HEADED": "1" if self.pwtest_headed else "0"}

    @property
    def playwright_mcp_args(self) -> tuple[str, ...]:
        """返回默认的 Playwright Test MCP 启动参数。

        Returns:
            tuple[str, ...]: `npx playwright run-test-mcp-server` 的参数数组。

        Raises:
            None.
        """

        return ("playwright", "run-test-mcp-server")

    @property
    def resolved_stream_chunk_timeout_seconds(self) -> int | None:
        """返回生效的流式分片静默超时时间。"""

        if self.stream_chunk_timeout_seconds is None:
            return self.llm_timeout_seconds
        if self.stream_chunk_timeout_seconds <= 0:
            return None
        return self.stream_chunk_timeout_seconds

    def build_model_kwargs(self, role: ModelRole) -> dict[str, object]:
        """生成指定角色传给 `init_chat_model` 的参数。

        模型连接只来自对应角色的嵌套配置；全局配置仅提供调用超时。

        Args:
            role: 模型角色。

        Returns:
            dict[str, object]: 可以直接传给 `init_chat_model` 的关键字参数。

        Raises:
            ModelConfigurationError: 对应角色缺少必填模型配置或配置组合无效。
        """

        connection = self.resolve_model_connection(role)

        kwargs: dict[str, object] = {
            "model": connection.api_model_name,
            "model_provider": connection.protocol,
            "timeout": connection.timeout_seconds,
            "max_retries": connection.max_retries,
        }
        if connection.api_key:
            kwargs["api_key"] = connection.api_key
        if connection.base_url:
            kwargs["base_url"] = connection.base_url

        if connection.protocol == "openai":
            if connection.base_url or connection.family != "openai":
                kwargs["use_responses_api"] = False
            kwargs["stream_chunk_timeout"] = connection.stream_chunk_timeout_seconds
            extra_body = _build_openai_extra_body(connection)
            if extra_body:
                kwargs["extra_body"] = extra_body
            disabled_params = _disabled_openai_params(connection)
            if disabled_params:
                kwargs["disabled_params"] = disabled_params
        elif connection.family == "minimax":
            kwargs["max_tokens"] = 131_072

        return kwargs

    def resolve_model_connection(self, role: ModelRole) -> ResolvedModelConnection:
        """校验并解析指定角色的模型连接。"""

        if role not in {"master", "specialist"}:
            raise ValueError(f"未知模型角色：{role}")

        role_settings = self.master_llm if role == "master" else self.specialist_llm
        family = role_settings.family
        channel = role_settings.channel
        api_model_name = _normalize_optional_text(role_settings.model)
        if family is None or channel is None or api_model_name is None:
            env_prefix = f"{role.upper()}_LLM__"
            missing_names = [
                f"{env_prefix}{field_name}"
                for field_name, value in (
                    ("FAMILY", family),
                    ("CHANNEL", channel),
                    ("MODEL", api_model_name),
                )
                if value is None
            ]
            raise ModelConfigurationError(
                f"{role} 缺少必填模型配置：{', '.join(missing_names)}。",
                context={"role": role, "missing_fields": missing_names},
            )

        if ":" in api_model_name:
            env_name = f"{role.upper()}_LLM__MODEL"
            raise ModelConfigurationError(
                f"{env_name} 必须填写模型服务端的真实 ID，不能包含 provider 前缀。",
                context={"role": role, "field": env_name},
            )

        _validate_family_channel(family, channel)
        base_url = _normalize_optional_text(role_settings.base_url)
        protocol = (
            "anthropic"
            if channel in {"minimax_anthropic", "generic_anthropic"}
            else "openai"
        )

        return ResolvedModelConnection(
            role=role,
            api_model_name=api_model_name,
            family=family,
            channel=channel,
            protocol=protocol,
            api_key=_normalize_optional_text(role_settings.api_key),
            base_url=base_url,
            thinking=role_settings.thinking,
            timeout_seconds=self.llm_timeout_seconds,
            max_retries=3,
            stream_chunk_timeout_seconds=self.resolved_stream_chunk_timeout_seconds,
        )

    @property
    def resolved_default_automation_project_root(self) -> Path:
        """返回不受进程工作目录影响的默认自动化项目根目录。"""

        automation_root = Path(self.default_automation_project_root).expanduser()
        if automation_root.is_absolute():
            return automation_root.resolve()
        relative_root = _default_relative_path_root(_PROJECT_ROOT, _DEFAULT_ENV_FILE)
        return (relative_root / automation_root).resolve()

    @property
    def resolved_scheduler_config_path(self) -> Path:
        """返回以服务端根目录为基准解析后的定时任务配置文件路径。"""

        if self.scheduler_config_path:
            config_path = Path(self.scheduler_config_path).expanduser()
            if not config_path.is_absolute():
                config_path = _PROJECT_ROOT / config_path
            return config_path.resolve()
        return _PROJECT_ROOT / "scheduler_tasks.json"


def _validate_family_channel(family: str, channel: str) -> None:
    required_family = {
        "minimax_openai": "minimax",
        "minimax_anthropic": "minimax",
        "zhipu_openai": "glm",
        "openai": "openai",
        "generic_anthropic": "generic",
    }.get(channel)
    if required_family is not None and family != required_family:
        raise ModelConfigurationError(
            f"模型 family `{family}` 与接入通道 `{channel}` 不匹配；该通道要求 family `{required_family}`。",
            context={"family": family, "channel": channel},
        )


def _normalize_optional_text(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    return normalized or None


def _build_openai_extra_body(connection: ResolvedModelConnection) -> dict[str, object]:
    extra_body: dict[str, object] = {}
    thinking_enabled = connection.thinking == "enabled"
    if connection.family == "qwen":
        if connection.thinking != "auto":
            extra_body["enable_thinking"] = thinking_enabled
    elif connection.family == "minimax":
        # Keep reasoning in content so tool turns can replay it faithfully.
        extra_body["reasoning_split"] = False
        if "m3" in connection.api_model_name.lower() and connection.thinking != "auto":
            extra_body["thinking"] = {
                "type": "adaptive" if thinking_enabled else "disabled"
            }
    elif connection.family == "glm":
        if connection.thinking != "auto":
            if connection.channel == "dashscope_openai":
                extra_body["enable_thinking"] = thinking_enabled
            else:
                extra_body["thinking"] = {
                    "type": "enabled" if thinking_enabled else "disabled"
                }
        if connection.channel == "dashscope_openai" and connection.role == "specialist":
            extra_body["tool_stream"] = True
    return extra_body


def _disabled_openai_params(connection: ResolvedModelConnection) -> dict[str, None]:
    if connection.family == "minimax" and connection.channel != "minimax_anthropic":
        return {
            "parallel_tool_calls": None,
            "tool_choice": None,
            "response_format": None,
        }
    if connection.family == "glm":
        return {"parallel_tool_calls": None}
    return {}


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """返回全局单例配置对象。

    Returns:
        AppSettings: 已缓存的应用配置实例。

    Raises:
        None.
    """

    # 先把 `.env` 里的配置按 UTF-8 注入进程环境，保证后续直接读取 `os.getenv`
    # 的启动逻辑在 Windows 上也不会被默认代码页影响。
    load_project_env_file()
    # 先用环境变量中的日志等级初始化日志系统，目的是让“配置解析本身”的过程也能被观测到。
    configure_logging_from_env()
    # 这里虽然没有传入任何函数参数，但 `AppSettings()` 继承了 `BaseSettings`，
    # 会自动从当前进程环境变量和 `.env` 文件中读取配置值。
    # 再配合 `lru_cache`，整个进程里只会解析一次配置，后续调用直接复用结果。
    # 主链路：这里完成全局配置对象创建，后续 Agent、MCP 和日志系统都会复用它。
    settings = AppSettings()
    configure_logging(settings.log_level)
    logger.info(
        "%s 应用配置加载成功 settings=%s",
        log_title("初始化", "配置加载"),
        summarize_settings(settings),
    )
    from deep_agent.model.diagnostics import collect_model_diagnostics

    logger.info(
        "%s 模型适配诊断 models=%s",
        log_title("初始化", "模型诊断"),
        collect_model_diagnostics(settings),
    )
    return settings
