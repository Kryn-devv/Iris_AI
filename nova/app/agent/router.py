"""Intent Router for classifying user requests."""

from typing import Dict, Any, Tuple
from nova.app.llm.gateway import ModelGateway
from nova.app.core.logging import get_logger

logger = get_logger("agent.router")


class IntentRouter:
    """Classifies user intent and routes to planner/tools."""

    def __init__(self, model_gateway: ModelGateway):
        self.model_gateway = model_gateway

    async def route(self, user_input: str) -> str:
        """Determine domain intent for user request."""
        provider, provider_name = await self.model_gateway.get_provider_and_name()
        res = await provider.generate(f"Route intent for: {user_input}")
        intent = res.raw_response.get("plan", {}).get("user_intent", "unsupported") if res.raw_response else "unsupported"
        logger.info(f"Routed user input to intent: '{intent}' via provider '{provider_name}'")
        return intent
