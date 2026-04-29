"""应用配置与环境变量解析。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from deep_agent.core.runtime_logging import (
    configure_logging,
    configure_logging_from_env,
    get_logger,
    log_title,
    summarize_settings,
)


logger = get_logger(__name__)


class AppSettings(BaseSettings):
    """定义项目运行所需的全部环境变量。

    配置统一放在这里，不只是为了“集中存放字段”，更是为了把模型、MCP、日志和默认目录
    这些跨模块依赖收敛成一个稳定入口。这样后续迁移到 LangGraph Dev、LangSmith 或部署环境时，
    调整成本会集中在配置层，而不是散落到多个 Agent 文件中。
    """

    # `SettingsConfigDict` 告诉 Pydantic 去哪里找 `.env` 文件，以及如何解析环境变量。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    master_model: str = Field(
        default="openai:gpt-4.1",
        description="Master Agent 默认使用的聊天模型，建议使用支持结构化输出的模型。",
    )
    specialist_model: str = Field(
        default="openai:gpt-5.4",
        description="Plan、Generator、Healer 等 Specialist Agent 默认使用的模型。",
    )
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI 兼容模型服务的 API Key；使用 OpenAI 模型时通常必填。",
    )
    openai_base_url: str | None = Field(
        default=None,
        description="OpenAI 兼容接口的基础地址，可用于代理网关或私有中转服务。",
    )
    max_conversation_turns: int = Field(
        default=20,
        description="Master 在缺参追问流程中允许的最大用户轮次，避免无限循环。",
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
    playwright_test_package: str = Field(
        default="@playwright/test",
        description="自动化项目目录缺少 Playwright Test 依赖时执行 npm install 使用的包名或版本规格。",
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

    def build_model_kwargs(self, model_name: str) -> dict[str, object]:
        """根据模型名称生成 `init_chat_model` 所需参数。

        这里把 OpenAI 的可选代理地址与超时配置收敛到一起，避免每个 Agent
        都复制一份模型初始化逻辑。

        Args:
            model_name: 目标模型名，推荐使用 `provider:model` 格式。

        Returns:
            dict[str, object]: 可以直接传给 `init_chat_model` 的关键字参数。

        Raises:
            None.
        """

        # 先准备所有模型都共用的基础参数，目的是把“每个 Agent 都会重复写的初始化参数”收敛掉。
        kwargs: dict[str, object] = {
            "model": model_name,
            "timeout": self.llm_timeout_seconds,
            "max_retries": 3,
        }

        # 如果用户只写了裸模型名，例如 `glm-4.5-air`，LangChain 无法自动判断 provider。
        # 由于当前项目只暴露了 OpenAI 兼容接口配置，所以这里把“无 provider 前缀的模型”
        # 统一按 OpenAI 兼容 provider 处理，便于接入智谱、代理网关等 OpenAI 风格接口。
        if ":" not in model_name:
            kwargs["model_provider"] = "openai"

        # 只有 OpenAI 兼容模型才需要额外拼接 API Key 和 base URL，
        # 目的是让非 OpenAI provider 不被无关参数污染。
        if model_name.startswith("openai:") or ":" not in model_name:
            if self.openai_api_key:
                kwargs["api_key"] = self.openai_api_key
            normalized_base_url = self._normalized_openai_base_url()
            if normalized_base_url:
                kwargs["base_url"] = normalized_base_url
                # 大多数 OpenAI 兼容平台只实现 Chat Completions，不支持 OpenAI Responses API。
                # 明确关闭后可以避免 gpt-5/codex 等模型名或 Deep Agents 默认策略把请求打到
                # `{base_url}/responses`，导致兼容平台返回 404。
                kwargs["use_responses_api"] = False
            # 显式覆盖 LangChain OpenAI 默认的 120s 流式静默超时，避免它先于总超时触发。
            kwargs["stream_chunk_timeout"] = self.resolved_stream_chunk_timeout_seconds

        return kwargs

    def _normalized_openai_base_url(self) -> str | None:
        """返回清理后的 OpenAI 兼容接口地址。"""

        if not self.openai_base_url:
            return None

        base_url = self.openai_base_url.strip()
        return base_url or None

    @property
    def resolved_default_automation_project_root(self) -> Path:
        """返回展开后的默认自动化项目根目录。"""

        return Path(self.default_automation_project_root).expanduser()


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """返回全局单例配置对象。

    Returns:
        AppSettings: 已缓存的应用配置实例。

    Raises:
        None.
    """

    # 先用环境变量中的日志等级初始化日志系统，目的是让“配置解析本身”的过程也能被观测到。
    configure_logging_from_env()
    # 这里虽然没有传入任何函数参数，但 `AppSettings()` 继承了 `BaseSettings`，
    # 会自动从当前进程环境变量和 `.env` 文件中读取配置值。
    # 再配合 `lru_cache`，整个进程里只会解析一次配置，后续调用直接复用结果。
    # TODO(重点流程): 这里完成全局配置对象创建，后续 Agent、MCP 和日志系统都会复用它。
    settings = AppSettings()
    configure_logging(settings.log_level)
    logger.info("%s 应用配置加载成功 settings=%s",
        log_title("初始化", "配置加载"), summarize_settings(settings),)
    return settings
