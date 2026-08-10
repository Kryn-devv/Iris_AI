"""Mock LLM Provider for offline demonstration and testing."""

import re
import json
from typing import Dict, Any, Optional, AsyncGenerator, Type, TypeVar
from pydantic import BaseModel

from nova.app.llm.base import LLMProvider, LLMResponse, LLMHealthStatus
from nova.app.schemas.agent import AgentPlan, PlanStep
from nova.app.core.logging import get_logger

logger = get_logger("mock_llm")

T = TypeVar("T", bound=BaseModel)


class MockLLMProvider(LLMProvider):
    """Mock LLM Provider that deterministically interprets demo prompts without external APIs."""

    def __init__(self, default_model: str = "mock-model"):
        super().__init__(provider_name="mock", default_model=default_model)

    async def health_check(self) -> bool:
        return True

    async def health_check_detailed(self) -> LLMHealthStatus:
        return LLMHealthStatus(
            provider=self.provider_name,
            available=True,
            base_url="in-process://mock",
            model=self.default_model,
            latency_ms=0.1,
        )

    def _extract_calculator_expression(self, prompt: str) -> str:
        """Extract a complete mathematical expression from user input without truncating chained operations."""
        text = prompt.strip()

        # Remove prefix phrases like "what is", "calculate", "compute"
        prefix_pattern = r"^(?:what\s+is|what's|calculate|compute|eval|evaluate|please\s+calculate|please\s+compute|please\s+eval|result\s+of|value\s+of|solve)\s*"
        cleaned = re.sub(prefix_pattern, "", text, flags=re.IGNORECASE).strip().rstrip("?").rstrip(".")

        # Normalize verbal word operators to math symbols
        cleaned = re.sub(r"\bmultiplied\s+by\b", "*", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bdivided\s+by\b", "/", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bplus\b", "+", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bminus\b", "-", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\btimes\b", "*", cleaned, flags=re.IGNORECASE)

        # Extract candidates containing digits, operators, spaces, parentheses
        math_matches = [m.group(0).strip() for m in re.finditer(r"([\d\.\s\+\-\*\/\%\(\)\*\*]+)", cleaned) if m.group(0).strip()]
        valid_candidates = []
        for m in math_matches:
            if re.search(r"\d", m) and re.search(r"[\+\-\*\/\%]", m):
                valid_candidates.append(m.strip().rstrip("?").rstrip("."))

        if valid_candidates:
            return max(valid_candidates, key=len)

        return cleaned if cleaned else prompt

    def _parse_intent_and_plan(self, prompt: str) -> AgentPlan:
        """Parse input prompt and return an AgentPlan."""
        text = prompt.lower().strip()

        # 1. Calculator Intent
        if any(term in text for term in ["calculate", "compute", "multiplied", "plus", "minus", "divided", "times", "sum", "math"]) or re.search(r"\d+\s*[\+\-\*\/\%]", text):
            expr = self._extract_calculator_expression(prompt)

            return AgentPlan(
                user_intent="calculator",
                steps=[
                    PlanStep(
                        step_id=1,
                        action="tool_call",
                        tool_name="calculator",
                        tool_args={"expression": expr},
                        rationale="User requested a mathematical calculation."
                    )
                ]
            )

        # 2. System Info Intent
        if any(term in text for term in ["operating system", "os", "system info", "system information", "cpu", "memory", "specs"]):
            return AgentPlan(
                user_intent="system_info",
                steps=[
                    PlanStep(
                        step_id=1,
                        action="tool_call",
                        tool_name="system_info",
                        tool_args={},
                        rationale="User requested system information."
                    )
                ]
            )

        # 3. Time Intent
        if any(term in text for term in ["time", "clock", "date", "what time"]):
            return AgentPlan(
                user_intent="time",
                steps=[
                    PlanStep(
                        step_id=1,
                        action="tool_call",
                        tool_name="time",
                        tool_args={},
                        rationale="User requested current local system time."
                    )
                ]
            )

        # 4. Fallback / Unsupported query
        return AgentPlan(
            user_intent="unsupported",
            steps=[
                PlanStep(
                    step_id=1,
                    action="final_response",
                    tool_name=None,
                    tool_args={},
                    rationale="Request is not handled by Phase 1/2 mock intents and real LLM provider is not currently connected."
                )
            ]
        )

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[list] = None,
        **kwargs: Any,
    ) -> LLMResponse:
        text = prompt.lower().strip()
        history_msgs = kwargs.get("messages", [])

        # Check for multi-step / simulated tool call test cases when tools or agent loop requests it
        if tools or kwargs.get("agent_mode"):
            # Multi-step calculation simulation: "20 + 30", then "* 5"
            if "20 + 30" in text or "multiply the result by 5" in text:
                obs_text = str(history_msgs)
                if "50" in obs_text and ("150" in obs_text or "250" in obs_text or "calculator" in obs_text):
                    if "250" in obs_text:
                        return LLMResponse(
                            content="The final result of (20 + 30) * 5 is 250.",
                            provider_name=self.provider_name,
                            model_name=model or self.default_model,
                        )
                    return LLMResponse(
                        content="",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                        tool_calls=[{
                            "id": "call_step2",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": json.dumps({"expression": "50 * 5"})}
                        }]
                    )
                else:
                    return LLMResponse(
                        content="",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                        tool_calls=[{
                            "id": "call_step1",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": json.dumps({"expression": "20 + 30"})}
                        }]
                    )

            # Replanning test case: "test tool failure replan"
            if "replan" in text or "failure recovery" in text:
                obs_text = str(history_msgs)
                if "failed" in obs_text or "not registered" in obs_text:
                    if "calculator" in obs_text or "10" in obs_text:
                        return LLMResponse(
                            content="Recovered after tool failure using fallback calculation.",
                            provider_name=self.provider_name,
                            model_name=model or self.default_model,
                        )
                    return LLMResponse(
                        content="",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                        tool_calls=[{
                            "id": "call_replan",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": json.dumps({"expression": "10 + 10"})}
                        }]
                    )
                else:
                    return LLMResponse(
                        content="",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                        tool_calls=[{
                            "id": "call_invalid",
                            "type": "function",
                            "function": {"name": "non_existent_tool", "arguments": json.dumps({})}
                        }]
                    )

            # Memory follow-up query test cases
            all_msg_text = str(history_msgs) + str(system_prompt or "")
            if "budget" in text:
                if "15,000" in all_msg_text or "15000" in all_msg_text or "robot_budget" in all_msg_text:
                    return LLMResponse(
                        content="Your robot budget is ₹15,000.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                elif "20,000" in all_msg_text or "20000" in all_msg_text:
                    return LLMResponse(
                        content="Your robot budget is ₹20,000.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                else:
                    return LLMResponse(
                        content="I don't have any record of your robot budget.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )

            if "microcontroller" in text or "controller" in text or "esp32" in text:
                if "esp32" in all_msg_text.lower() or "microcontroller" in all_msg_text.lower():
                    return LLMResponse(
                        content="Your robot uses an ESP32.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                else:
                    return LLMResponse(
                        content="I don't have any record of your robot microcontroller.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )

            # Confirmation test case: "delete sensitive file" or "confirm action"
            if "delete" in text or "confirm" in text or "confirmation" in text:
                obs_text = str(history_msgs)
                if "success" in obs_text or "executed" in obs_text or "confirmed" in obs_text:
                    return LLMResponse(
                        content="Action completed successfully after confirmation.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                if "rejected" in obs_text or "denied" in obs_text:
                    return LLMResponse(
                        content="Action was cancelled due to user rejection.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                return LLMResponse(
                    content="",
                    provider_name=self.provider_name,
                    model_name=model or self.default_model,
                    tool_calls=[{
                        "id": "call_confirm",
                        "type": "function",
                        "function": {"name": "delete_file", "arguments": json.dumps({"filepath": "/tmp/test.txt"})}
                    }]
                )

            # Single tool calls for standard queries if tool_calls expected
            if any(term in text for term in ["calculator", "multiplied", "plus", "minus", "divided", "times", "calculate", "compute"]) or re.search(r"\d+\s*[\+\-\*\/\%]", text):
                expr = self._extract_calculator_expression(prompt)

                obs_text = str(history_msgs)
                if "result" in obs_text or "formatted" in obs_text or "1175" in obs_text or "90" in obs_text or "12558" in obs_text:
                    return LLMResponse(
                        content="Calculation result processed successfully.",
                        provider_name=self.provider_name,
                        model_name=model or self.default_model,
                    )
                return LLMResponse(
                    content="",
                    provider_name=self.provider_name,
                    model_name=model or self.default_model,
                    tool_calls=[{
                        "id": "call_calc_single",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": json.dumps({"expression": expr})}
                    }]
                )

        plan = self._parse_intent_and_plan(prompt)

        # Check language tags in system_prompt or messages
        sys_str = (system_prompt or "") + str(history_msgs)
        is_hindi = "response_language=hi" in sys_str or len(re.findall(r"[\u0900-\u097F]", text)) > 0 or "in hindi" in text or "hindi mein" in text
        is_hinglish = "response_language=hinglish" in sys_str or "hinglish" in text or any(w in text for w in ["bhai", "kya", "haal", "samjha", "batao", "kaise"])

        if plan.user_intent == "unsupported":
            if "recursion" in text or "explain" in text:
                if is_hindi:
                    content = "रिकर्शन (Recursion) एक ऐसी प्रक्रिया है जिसमें कोई फ़ंक्शन खुद को ही कॉल करता है।"
                elif is_hinglish:
                    content = "Recursion ek aisa concept hai jahan ek function khud ko hi call karta hai repeatedly."
                else:
                    content = "Recursion is a programming technique where a function calls itself until a base condition is met."
            elif "namaste" in text or "नमस्ते" in text:
                content = "नमस्ते! मैं NOVA हूँ, आपकी सहायता के लिए तैयार हूँ।"
            elif is_hinglish or "bhai" in text:
                content = "Bas sab mast bhai! NOVA ekdam ready hai tumhari help ke liye."
            elif is_hindi:
                content = "जी, मैंने आपका संदेश समझ लिया है।"
            elif "hello" in text or "hi" in text:
                content = "Hello! I'm NOVA, your personal AI agent foundation."
            else:
                content = (
                    "I am NOVA (Phase 1 Kernel Foundation). "
                    "A real LLM provider (Ollama / Local LLM / Cloud API) is not connected yet. "
                    "Currently, I support deterministic demo commands: calculator, system info, and time."
                )
        else:
            content = f"Plan created for intent '{plan.user_intent}' with tool '{plan.steps[0].tool_name}'."

        return LLMResponse(
            content=content,
            provider_name=self.provider_name,
            model_name=model or self.default_model,
            raw_response={"plan": plan.model_dump()},
        )

    async def generate_structured(
        self,
        prompt: str,
        response_model: Type[T],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> T:
        if response_model == AgentPlan:
            return self._parse_intent_and_plan(prompt)  # type: ignore

        dummy_data = {"response": f"Mock response for prompt: {prompt}"}
        return response_model.model_validate(dummy_data)

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        res = await self.generate(prompt, system_prompt, model, **kwargs)
        for word in res.content.split(" "):
            yield word + " "
