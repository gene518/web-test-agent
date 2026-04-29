"""Master Agent 提示词导出。"""

from deep_agent.agent.master.prompts.ask_params_context import build_master_ask_params_context_prompt
from deep_agent.agent.master.prompts.general import GENERAL_ASSISTANT_SYSTEM_PROMPT
from deep_agent.agent.master.prompts.router import MASTER_ROUTER_SYSTEM_PROMPT

__all__ = [
    "GENERAL_ASSISTANT_SYSTEM_PROMPT",
    "MASTER_ROUTER_SYSTEM_PROMPT",
    "build_master_ask_params_context_prompt",
]
