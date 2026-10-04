from functools import lru_cache

from enterprise_context.agents.runs import AgentRunRepository
from enterprise_context.agents.workflow import AgentWorkflow
from enterprise_context.config import get_settings
from enterprise_context.context_engine.dependencies import get_context_engine
from enterprise_context.tools.dependencies import get_tool_registry


@lru_cache(maxsize=1)
def get_agent_workflow() -> AgentWorkflow:
    return AgentWorkflow(get_context_engine(), get_tool_registry())


@lru_cache(maxsize=1)
def get_agent_runs() -> AgentRunRepository:
    return AgentRunRepository(get_settings().database_url)
