"""对外导出可被 LangGraph CLI 直接加载的图对象。

这个模块的目的，是把应用启动时必须完成的初始化顺序固定下来：
先准备日志和配置，再构建图对象，最后暴露给 LangGraph CLI 直接加载。
"""

from deep_agent.core.config import get_settings
from deep_agent.workflow import build_workflow
from deep_agent.core.runtime_logging import configure_logging_from_env, get_logger, log_title

configure_logging_from_env()
logger = get_logger(__name__)

# 在模块导入阶段先初始化配置和日志，再构建并编译图对象，LangGraph CLI 会直接读取这个变量。
get_settings()
logger.info("%s 开始构建 LangGraph 图对象。",
    log_title("初始化", "应用启动"),)
# TODO(重点流程): 这里完成全局工作流图构建，CLI 对外暴露的入口就是这个编译后的 graph。
agent_graph = build_workflow()
logger.info("%s LangGraph 图对象构建完成。",
    log_title("初始化", "应用启动"),)
