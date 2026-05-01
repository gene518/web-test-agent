"""Master Agent 提示词导出。"""

from deep_agent.agent.master.prompts.complete_params import build_master_complete_params_prompt
from deep_agent.agent.master.prompts.general_test import GENERAL_TEST_SYSTEM_PROMPT
from deep_agent.agent.master.prompts.intent_judge import INTENT_JUDGE_SYSTEM_PROMPT

__all__ = [
    "GENERAL_TEST_SYSTEM_PROMPT",
    "INTENT_JUDGE_SYSTEM_PROMPT",
    "build_master_complete_params_prompt",
]
