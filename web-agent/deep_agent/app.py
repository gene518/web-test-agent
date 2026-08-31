"""对外导出可被 LangGraph CLI 直接加载的图对象。

这个模块的目的，是把应用启动时必须完成的初始化顺序固定下来：
先准备日志和配置，再构建图对象，最后暴露给 LangGraph CLI 直接加载。
"""

from deep_agent.core.config import get_settings, load_project_env_file
from deep_agent.core.runtime_logging import (
    configure_logging_from_env,
    get_logger,
    log_title,
)
from deep_agent.core.local_runtime_cleanup import cancel_stale_inmemory_runs_on_start
from deep_agent.http_artifacts import build_artifact_http_app
from deep_agent.scheduled_run_workflow import build_scheduled_run_workflow
from deep_agent.thread_title_workflow import build_thread_title_workflow
from deep_agent.web_autotest_agent_workflow import build_web_autotest_agent_workflow

# 先把 `.env` 按 UTF-8 注入进程环境，避免 Windows 默认代码页把中文注释读坏。
load_project_env_file()
configure_logging_from_env()
logger = get_logger(__name__)

# 在模块导入阶段先初始化配置和日志，再构建并编译图对象，LangGraph CLI 会直接读取这个变量。
get_settings()
cancel_stale_inmemory_runs_on_start()
logger.info(
    "%s 开始构建 LangGraph 图对象。",
    log_title("初始化", "应用启动"),
)
# 这里完成全局工作流图构建，命令行入口对外暴露的就是这个编译后的图对象。
agent_graph = build_web_autotest_agent_workflow()
title_graph = build_thread_title_workflow()
scheduled_run_graph = build_scheduled_run_workflow()
http_app = build_artifact_http_app()
logger.info(
    "%s LangGraph 图对象与 H5 只读产物路由构建完成。",
    log_title("初始化", "应用启动"),
)
