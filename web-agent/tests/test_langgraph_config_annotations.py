from __future__ import annotations

import inspect

from langchain_core.runnables import RunnableConfig

from deep_agent.agent.base_agent import BaseSpecialistAgent
from deep_agent.agent.finalizer import FinalizeStageNode
from deep_agent.agent.master.nodes.resolve_stage_files_node import ResolveStageFilesNode
from deep_agent.agent.scheduler.scheduler_agent import SchedulerAgent
from deep_agent.thread_title_workflow import ThreadTitleNode


def test_graph_node_config_annotations_are_runtime_types() -> None:
    expected_annotation = RunnableConfig | None
    node_callables = [
        BaseSpecialistAgent.execute,
        SchedulerAgent.execute,
        FinalizeStageNode.execute,
        ResolveStageFilesNode.execute,
        ThreadTitleNode.execute,
    ]

    for node_callable in node_callables:
        annotation = inspect.signature(node_callable).parameters["config"].annotation
        assert annotation == expected_annotation, node_callable.__qualname__
