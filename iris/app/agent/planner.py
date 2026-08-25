"""Planner for constructing action execution plans."""

from typing import Optional, Any
from iris.app.schemas.agent import AgentPlan
from iris.app.llm.gateway import ModelGateway
from iris.app.core.logging import get_logger

logger = get_logger("agent.planner")


class Planner:
    """Produces structured step-by-step plan and dynamic replanning based on user intent and observations."""

    def __init__(self, model_gateway: ModelGateway, tool_registry: Optional[Any] = None):
        self.model_gateway = model_gateway
        self.tool_registry = tool_registry

    async def create_plan(self, user_input: str) -> AgentPlan:
        """Construct plan for execution."""
        provider, provider_name = await self.model_gateway.get_provider_and_name()
        plan = await provider.generate_structured(user_input, response_model=AgentPlan)
        logger.info(f"Generated plan with {len(plan.steps)} steps for intent '{plan.user_intent}' via provider '{provider_name}'")
        return plan

    async def replan(self, user_input: str, failed_tool: str, error: str) -> AgentPlan:
        """Construct alternative plan after a tool execution failure."""
        provider, provider_name = await self.model_gateway.get_provider_and_name()
        replan_prompt = (
            f"Original user request: {user_input}\n"
            f"Tool '{failed_tool}' failed with error: {error}\n"
            f"Construct an alternative execution plan to complete the task or report error cleanly."
        )
        plan = await provider.generate_structured(replan_prompt, response_model=AgentPlan)
        logger.info(f"Generated REPLAN with {len(plan.steps)} steps after failure of tool '{failed_tool}' via provider '{provider_name}'")
        return plan
