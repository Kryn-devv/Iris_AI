"""AgentKernel: the orchestrating brain of IRIS.

Every user message flows through a layered pipeline, fastest layer first:

1. **Wake-word strip** — "hey iris, …" prefixes are removed.
2. **Memory commands** — "remember/forget/recall …" natural-language memory.
3. **Small talk** — greetings and pleasantries answer instantly, offline.
4. **Deterministic NLU** — the intent engine maps commands ("open youtube",
   "volume up", "remind me in 10 minutes …") straight to tools with zero
   model calls. Content intents ("make a ppt about …") optionally enrich
   their arguments with LLM-generated material when a provider is available.
5. **LLM agent loop** — everything else goes to the model gateway with the
   full tool catalogue for multi-step function calling; the gateway falls
   back through free providers and finally the offline reasoner.

Every layer publishes progress on the event bus so the UI, voice pipeline and
bridges render live activity. All tool execution funnels through the
permission manager — including confirmation round-trips.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from iris.app.agent.context import ContextAssembler
from iris.app.agent.events import AgentEvent, AgentEventType, EventDispatcher, default_event_dispatcher
from iris.app.agent.executor import ExecutionEngine
from iris.app.agent.prompts import (
    CONTENT_CODE_PROMPT,
    CONTENT_DOCUMENT_PROMPT,
    CONTENT_SLIDES_PROMPT,
    CONTENT_SPREADSHEET_PROMPT,
    get_system_prompt,
)
from iris.app.agent.smalltalk import match_smalltalk
from iris.app.agent.state import AgentState
from iris.app.agent.task_manager import TaskManager
from iris.app.core.bus import Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import correlation_id_ctx, get_logger, task_id_ctx
from iris.app.core.security import (
    PermissionDecision,
    PermissionManager,
    default_permission_manager,
)
from iris.app.language.service import LanguageService, default_language_service
from iris.app.llm.cloud import extract_json_object
from iris.app.llm.gateway import ModelGateway, default_model_gateway
from iris.app.memory.extractor import MemoryExtractor
from iris.app.memory.service import MemoryService
from iris.app.nlu.engine import IntentEngine, IntentMatch, default_intent_engine
from iris.app.schemas.messages import ChatResponse, ToolExecutionSummary
from iris.app.schemas.tasks import TaskStatus
from iris.app.schemas.tools import ToolExecutionResult
from iris.app.tools.adapter import ToolSchemaAdapter
from iris.app.tools.registry import ToolRegistry, default_tool_registry
from iris.app.voice.service import strip_wake_word

logger = get_logger("agent.kernel")


class AgentKernel:
    """The central kernel coordinating understanding, planning and execution."""

    def __init__(
        self,
        model_gateway: Optional[ModelGateway] = None,
        tool_registry: Optional[ToolRegistry] = None,
        permission_manager: Optional[PermissionManager] = None,
        task_manager: Optional[TaskManager] = None,
        event_dispatcher: Optional[EventDispatcher] = None,
        memory_service: Optional[MemoryService] = None,
        language_service: Optional[LanguageService] = None,
        intent_engine: Optional[IntentEngine] = None,
    ):
        self.model_gateway = model_gateway or default_model_gateway
        self.tool_registry = tool_registry or default_tool_registry
        self.permission_manager = permission_manager or default_permission_manager
        self.task_manager = task_manager or TaskManager()
        self.event_dispatcher = event_dispatcher or default_event_dispatcher
        self.memory_service = memory_service or MemoryService()
        self.language_service = language_service or default_language_service
        self.intent_engine = intent_engine or default_intent_engine

        self.executor = ExecutionEngine(
            tool_registry=self.tool_registry, permission_manager=self.permission_manager
        )
        self.working_memory = self.memory_service.working_memory
        self.conversation_memory = self.memory_service.conversation_memory
        self.context_assembler = ContextAssembler(
            tool_registry=self.tool_registry,
            working_memory=self.working_memory,
            conversation_memory=self.conversation_memory,
            memory_service=self.memory_service,
        )

    # ======================================================================
    # Public API
    # ======================================================================

    async def process_request(
        self,
        user_input: str,
        conversation_id: Optional[str] = None,
        user_approved: bool = False,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        channel: str = "web",
    ) -> ChatResponse:
        """Process user input through the layered kernel pipeline."""
        _, user_input_clean = strip_wake_word(user_input)
        user_input_clean = user_input_clean or user_input

        state = self.task_manager.create_task(
            user_input_clean, task_id=task_id, correlation_id=correlation_id
        )
        state.conversation_id = conversation_id
        state.user_approved = user_approved
        state.metadata["channel"] = channel

        c_token = correlation_id_ctx.set(state.correlation_id)
        t_token = task_id_ctx.set(state.task_id)
        default_event_bus.publish(
            Topics.AGENT_STARTED,
            {"task_id": state.task_id, "input": user_input_clean, "channel": channel},
        )

        try:
            # Language metadata (used by prompts and the response envelope).
            detection, normalized_input, target_lang, target_style = self.language_service.process_input(
                user_input_clean
            )
            state.metadata["language_detection"] = detection
            state.metadata["target_response_language"] = target_lang.value
            state.metadata["target_style"] = target_style.value
            state.metadata["normalized_input"] = normalized_input

            # Layer 2: natural-language memory commands.
            memory_response = await self._try_memory_commands(state, user_input_clean)
            if memory_response is not None:
                return self._finish(state, memory_response)

            # Layer 3: instant small talk.
            smalltalk = match_smalltalk(user_input_clean)
            if smalltalk is not None:
                state.update_status(TaskStatus.COMPLETED)
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                return self._finish(
                    state,
                    self._response(state, smalltalk, handler="smalltalk", intent="smalltalk"),
                )

            # Layer 4: deterministic command NLU.
            if settings.NLU_ENABLED:
                match = self.intent_engine.match(user_input_clean)
                if match is not None and match.confidence >= settings.NLU_MIN_CONFIDENCE:
                    if self.tool_registry.is_registered(match.tool_name):
                        response = await asyncio.wait_for(
                            self._dispatch_intent(state, match, user_approved),
                            timeout=settings.TOTAL_TASK_TIMEOUT_SECONDS,
                        )
                        return self._finish(state, response)
                    logger.info(
                        "NLU matched unregistered tool '%s'; falling through to agent loop.",
                        match.tool_name,
                    )

            # Layer 5: full agent loop.
            response = await asyncio.wait_for(
                self._run_agent_loop(state, user_approved=user_approved),
                timeout=settings.TOTAL_TASK_TIMEOUT_SECONDS,
            )
            return self._finish(state, response)

        except asyncio.TimeoutError:
            logger.error("Task '%s' exceeded total timeout.", state.task_id)
            state.update_status(TaskStatus.FAILED, error="Timeout")
            self._dispatch_event(state, AgentEventType.AGENT_FAILED, data={"error": "Timeout"})
            return self._finish(
                state,
                self._response(
                    state,
                    "That took too long and I stopped it. Try breaking the request into smaller steps.",
                    status=TaskStatus.FAILED,
                    error="Total task timeout exceeded.",
                ),
            )
        except asyncio.CancelledError:
            state.update_status(TaskStatus.CANCELLED, error="Task was cancelled.")
            self._dispatch_event(state, AgentEventType.AGENT_CANCELLED)
            return self._finish(
                state,
                self._response(state, "Task was cancelled.", status=TaskStatus.CANCELLED),
            )
        except Exception as exc:  # noqa: BLE001 - kernel must always answer
            logger.error("Kernel error in task '%s': %s", state.task_id, exc, exc_info=True)
            state.update_status(TaskStatus.FAILED, error=str(exc))
            self._dispatch_event(state, AgentEventType.AGENT_FAILED, data={"error": str(exc)})
            return self._finish(
                state,
                self._response(
                    state,
                    "Something went wrong on my side while handling that.",
                    status=TaskStatus.FAILED,
                    error=str(exc),
                ),
            )
        finally:
            correlation_id_ctx.reset(c_token)
            task_id_ctx.reset(t_token)

    async def resume_task_confirmation(self, task_id: str, approved: bool) -> ChatResponse:
        """Resume a task paused in WAITING_FOR_CONFIRMATION."""
        state = self.task_manager.get_task(task_id)
        if not state:
            raise ValueError(f"Task '{task_id}' not found.")
        if state.status != TaskStatus.WAITING_FOR_CONFIRMATION or not state.pending_tool_call:
            raise ValueError(f"Task '{task_id}' has no pending confirmation.")

        pending = state.pending_tool_call
        tool_name = pending["tool_name"]
        arguments = pending.get("arguments", {})

        c_token = correlation_id_ctx.set(state.correlation_id)
        t_token = task_id_ctx.set(state.task_id)
        try:
            state.pending_tool_call = None
            if not approved:
                state.user_approved = False
                state.update_status(TaskStatus.COMPLETED)
                state.record_step(
                    step_type="observation",
                    description=f"User rejected confirmation for '{tool_name}'.",
                    tool_name=tool_name,
                    tool_args=arguments,
                    error="USER_REJECTED_CONFIRMATION",
                )
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                return self._finish(
                    state,
                    self._response(
                        state,
                        f"Okay, I won't run '{tool_name}'.",
                        handler="confirmation",
                        speech="Okay, cancelled.",
                    ),
                )

            state.user_approved = True
            state.update_status(TaskStatus.RUNNING)
            summary, exec_result = await self._execute_tool(state, tool_name, arguments, True)
            text = exec_result.spoken_or_display() or f"Done — '{tool_name}' completed."
            if not exec_result.success:
                text = exec_result.error or f"'{tool_name}' failed."
            state.update_status(TaskStatus.COMPLETED if exec_result.success else TaskStatus.FAILED)
            self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
            return self._finish(
                state,
                self._response(
                    state,
                    text,
                    handler="confirmation",
                    tools=[summary],
                    speech=exec_result.speech,
                    artifacts=exec_result.artifacts,
                    ui=exec_result.ui,
                    status=TaskStatus.COMPLETED if exec_result.success else TaskStatus.FAILED,
                    error=None if exec_result.success else exec_result.error,
                ),
            )
        finally:
            correlation_id_ctx.reset(c_token)
            task_id_ctx.reset(t_token)

    # ======================================================================
    # Layer 2 — memory commands
    # ======================================================================

    async def _try_memory_commands(self, state: AgentState, user_input: str) -> Optional[ChatResponse]:
        cmd_type, payload = MemoryExtractor.parse_command(user_input)
        if not cmd_type or not payload:
            return None

        if cmd_type == "remember":
            await self.memory_service.remember(
                key=payload["key"], value=payload["value"], memory_type=payload["type"], metadata=payload
            )
            self._dispatch_event(state, AgentEventType.MEMORY_CREATED, data={"key": payload["key"]})
            state.update_status(TaskStatus.COMPLETED)
            self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
            text = f"I'll remember that: {payload['key'].replace('_', ' ')} is {payload['value']}."
            if "robot" in payload["key"]:
                text = "I'll remember that for the robot project."
            return self._response(state, text, handler="memory", intent="memory.remember")

        if cmd_type == "forget":
            forgot = await self.memory_service.forget(key=payload["key"])
            self._dispatch_event(
                state, AgentEventType.MEMORY_FORGOTTEN, data={"key": payload["key"], "success": forgot}
            )
            state.update_status(TaskStatus.COMPLETED)
            self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
            raw_t = payload.get("raw_target", "").lower().replace("my ", "").replace("the ", "")
            target_name = raw_t if raw_t else payload["key"].replace("_", " ")
            text = (
                f"I've forgotten your {target_name}."
                if forgot
                else f"I don't have any record of your {target_name}."
            )
            return self._response(state, text, handler="memory", intent="memory.forget")

        if cmd_type == "recall":
            val = await self.memory_service.retrieve(payload["key"])
            if val is None:
                search_res = await self.memory_service.search(payload["query"], limit=1, min_relevance=0.3)
                if search_res:
                    rec, _score = search_res[0]
                    stop_words = {
                        "robot", "my", "project", "the", "a", "an", "is", "what",
                        "does", "use", "for", "in", "of", "about",
                    }
                    query_terms = [
                        t.lower() for t in payload.get("query", "").split() if t.lower() not in stop_words
                    ]
                    if query_terms and any(
                        term in rec.key.lower() or term in rec.content.lower() for term in query_terms
                    ):
                        val = rec.value

            state.update_status(TaskStatus.COMPLETED)
            self._dispatch_event(
                state, AgentEventType.MEMORY_RETRIEVED, data={"key": payload["key"], "found": val is not None}
            )
            self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)

            import re as _re

            clean_q = payload.get("query", "").lower()
            clean_q = _re.sub(
                r"^(what\s+is\s+|what's\s+|what\s+microcontroller\s+does\s+)", "", clean_q, flags=_re.IGNORECASE
            )
            clean_q = _re.sub(r"^(my\s+|the\s+)", "", clean_q, flags=_re.IGNORECASE).rstrip("?").strip()
            target_name = clean_q if clean_q else payload["key"].replace("_", " ")

            if val is not None:
                if "budget" in payload["key"]:
                    text = f"Your robot budget is {val}."
                elif "microcontroller" in payload["key"]:
                    text = f"Your robot uses an {val}."
                else:
                    text = f"Regarding {target_name}: {val}."
            else:
                text = f"I don't have any record of your {target_name}."
            return self._response(state, text, handler="memory", intent="memory.recall")

        return None

    # ======================================================================
    # Layer 4 — deterministic intent dispatch
    # ======================================================================

    async def _dispatch_intent(
        self, state: AgentState, match: IntentMatch, user_approved: bool
    ) -> ChatResponse:
        """Execute a single tool resolved by the NLU engine."""
        state.update_status(TaskStatus.RUNNING)
        state.record_step(
            step_type="understand_route",
            description=f"NLU matched '{match.rule_name}' -> {match.tool_name}",
        )
        default_event_bus.publish(
            Topics.AGENT_PLAN,
            {"task_id": state.task_id, "handler": "nlu", "tool": match.tool_name, "intent": match.intent},
        )

        arguments = dict(match.arguments)
        generation_note = ""
        if match.needs_generation:
            arguments, generation_note = await self._enrich_content_arguments(match, arguments)

        summary, exec_result = await self._execute_tool(
            state, match.tool_name, arguments, user_approved
        )

        if exec_result.error == "__REQUIRES_CONFIRMATION__":
            return self._confirmation_response(state, match.tool_name, arguments, match.intent)

        state.update_status(TaskStatus.COMPLETED if exec_result.success else TaskStatus.FAILED)
        self._dispatch_event(
            state,
            AgentEventType.AGENT_COMPLETED if exec_result.success else AgentEventType.AGENT_FAILED,
        )

        text = exec_result.spoken_or_display()
        if not text:
            text = "Done." if exec_result.success else "That didn't work."
        if generation_note:
            text = f"{text}\n\n{generation_note}" if exec_result.success else text

        await self._remember_turn(state, text)
        return self._response(
            state,
            text,
            handler="nlu",
            intent=match.tool_name,
            tools=[summary],
            speech=exec_result.speech,
            artifacts=exec_result.artifacts,
            ui=exec_result.ui,
            status=TaskStatus.COMPLETED if exec_result.success else TaskStatus.FAILED,
            error=None if exec_result.success else exec_result.error,
        )

    async def _enrich_content_arguments(
        self, match: IntentMatch, arguments: Dict[str, Any]
    ) -> tuple[Dict[str, Any], str]:
        """Generate rich content for content-producing intents when a model is available.

        Falls back to the tools' own deterministic templates when offline.
        """
        if not self.model_gateway.has_cloud or settings.LLM_MODE in ("off", "mock"):
            if match.tool_name == "write_code":
                return self._offline_code_arguments(arguments), (
                    "_Generated from an offline template — connect a free model key for full code generation._"
                )
            if match.tool_name == "write_document":
                topic = arguments.get("title", "Untitled")
                arguments["content"] = (
                    f"# {topic.title()}\n\n"
                    f"- Overview of {topic}\n- Key points\n- Details\n- Summary\n\n"
                    f"(Outline generated offline — connect a free model key for full writing.)"
                )
                return arguments, ""
            return arguments, ""

        try:
            if match.tool_name == "create_presentation":
                topic = arguments.get("topic") or arguments.get("title", "")
                res = await self.model_gateway.generate(
                    CONTENT_SLIDES_PROMPT.format(topic=topic), capability="REASONING"
                )
                data = extract_json_object(res.content)
                if data and isinstance(data.get("slides"), list) and data["slides"]:
                    arguments["title"] = data.get("title") or arguments.get("title", topic)
                    arguments["slides"] = data["slides"]
            elif match.tool_name == "write_document":
                topic = arguments.get("title", "")
                res = await self.model_gateway.generate(
                    CONTENT_DOCUMENT_PROMPT.format(topic=topic), capability="REASONING"
                )
                if res.content.strip():
                    arguments["content"] = res.content.strip()
            elif match.tool_name == "write_code":
                task = arguments.pop("task", "")
                language = arguments.get("language", "python")
                res = await self.model_gateway.generate(
                    CONTENT_CODE_PROMPT.format(task=task, language=language), capability="CODING"
                )
                data = extract_json_object(res.content)
                if data and data.get("code"):
                    arguments["filename"] = data.get("filename") or "generated_script.py"
                    arguments["code"] = data["code"]
                else:
                    arguments["task"] = task
                    arguments = self._offline_code_arguments(arguments)
            elif match.tool_name == "create_spreadsheet":
                topic = arguments.get("title", "")
                res = await self.model_gateway.generate(
                    CONTENT_SPREADSHEET_PROMPT.format(topic=topic), capability="REASONING"
                )
                data = extract_json_object(res.content)
                if data and data.get("headers"):
                    arguments["title"] = data.get("title") or topic
                    arguments["headers"] = data["headers"]
                    arguments["rows"] = data.get("rows", [])
        except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
            logger.warning("Content enrichment failed (%s); using deterministic fallback.", exc)
            if match.tool_name == "write_code":
                arguments = self._offline_code_arguments(arguments)
        return arguments, ""

    @staticmethod
    def _offline_code_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Build a runnable starter file when no model is available."""
        task = arguments.pop("task", "the requested task")
        language = (arguments.get("language") or "python").lower()
        ext = {"python": "py", "javascript": "js", "js": "js", "html": "html", "bash": "sh"}.get(language, "py")
        slug = "".join(c if c.isalnum() else "_" for c in task.lower())[:40].strip("_") or "script"
        comment = "#" if ext in ("py", "sh") else "//"
        arguments["filename"] = f"{slug}.{ext}"
        arguments["code"] = (
            f"{comment} Task: {task}\n"
            f"{comment} Starter generated offline by IRIS — flesh out the TODOs.\n\n"
            + ("def main():\n    # TODO: implement\n    pass\n\n\nif __name__ == \"__main__\":\n    main()\n"
               if ext == "py" else f"{comment} TODO: implement\n")
        )
        return arguments

    # ======================================================================
    # Layer 5 — LLM agent loop
    # ======================================================================

    async def _run_agent_loop(self, state: AgentState, user_approved: bool = False) -> ChatResponse:
        """Multi-step function-calling loop through the model gateway."""
        logger.info("Agent loop for task '%s': %r", state.task_id, state.user_input)
        self._dispatch_event(state, AgentEventType.AGENT_STARTED)
        state.update_status(TaskStatus.PLANNING)

        tools_executed: List[ToolExecutionSummary] = []
        artifacts: List[str] = []
        final_text = ""
        speech: Optional[str] = None
        provider_name = None
        model_name = None

        # Conversation scratchpad for this loop (OpenAI message format).
        context_data = await self.context_assembler.assemble_context(state)
        messages: List[Dict[str, Any]] = list(context_data["messages"])
        available_tools = [
            ToolSchemaAdapter.from_tool(t) for t in self.tool_registry.tools(available_only=True)
        ]

        state.update_status(TaskStatus.RUNNING)
        for iteration in range(settings.MAX_PLANNING_ITERATIONS):
            state.iteration_count = iteration + 1
            default_event_bus.publish(
                Topics.AGENT_THINKING, {"task_id": state.task_id, "iteration": iteration + 1}
            )

            llm_res = await self.model_gateway.generate(
                prompt="" if iteration > 0 else state.user_input,
                system_prompt=context_data["system_prompt"],
                tools=available_tools or None,
                messages=messages if iteration > 0 else messages[:-1] if messages else None,
                agent_mode=True,
            )
            provider_name = llm_res.provider_name
            model_name = llm_res.model_name
            state.provider = provider_name
            state.model = model_name

            if llm_res.tool_calls:
                # Record the assistant turn that requested the calls.
                messages.append(
                    {
                        "role": "assistant",
                        "content": llm_res.content or None,
                        "tool_calls": [
                            {
                                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                "type": "function",
                                "function": tc.get("function", {}),
                            }
                            for tc in llm_res.tool_calls
                        ],
                    }
                )
                for tc in llm_res.tool_calls:
                    if state.tool_call_count >= settings.MAX_TOOL_CALLS:
                        logger.warning("Task '%s' hit MAX_TOOL_CALLS.", state.task_id)
                        break
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    raw_args = fn.get("arguments", {})
                    if isinstance(raw_args, str):
                        try:
                            arguments = json.loads(raw_args) if raw_args.strip() else {}
                        except json.JSONDecodeError:
                            arguments = {}
                    else:
                        arguments = raw_args or {}

                    call_id = tc.get("id") or f"call_{uuid.uuid4().hex[:8]}"
                    summary, exec_result = await self._execute_tool(
                        state, tool_name, arguments, user_approved
                    )
                    if exec_result.error == "__REQUIRES_CONFIRMATION__":
                        return self._confirmation_response(state, tool_name, arguments, "agent")

                    tools_executed.append(summary)
                    artifacts.extend(exec_result.artifacts)
                    if exec_result.speech:
                        speech = exec_result.speech
                    observation = (
                        json.dumps(exec_result.result, default=str)[:4000]
                        if exec_result.success
                        else f"ERROR: {exec_result.error}"
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": observation,
                        }
                    )
                continue  # let the model observe results and continue

            if llm_res.content and llm_res.content.strip():
                final_text = llm_res.content.strip()
                break

            # Neither tool calls nor content: stop looping.
            break

        if not final_text:
            if tools_executed:
                last = tools_executed[-1]
                final_text = (
                    speech
                    or (f"Done — ran {', '.join(t.tool_name for t in tools_executed)}."
                        if last.success
                        else f"I ran into a problem: {last.error}")
                )
            else:
                final_text = (
                    "I couldn't work that one out. Try rephrasing, or say \"help\" to see what I can do."
                )

        state.result = final_text
        state.update_status(TaskStatus.COMPLETED)
        state.record_step(step_type="evaluation", description="Agent loop completed.")
        self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
        await self._remember_turn(state, final_text)

        return self._response(
            state,
            final_text,
            handler="agent",
            intent="agent",
            tools=tools_executed,
            speech=speech,
            artifacts=artifacts,
            provider=provider_name,
            model=model_name,
        )

    # ======================================================================
    # Shared helpers
    # ======================================================================

    async def _execute_tool(
        self,
        state: AgentState,
        tool_name: str,
        arguments: Dict[str, Any],
        user_approved: bool,
    ) -> tuple[ToolExecutionSummary, ToolExecutionResult]:
        """Permission-checked tool execution with events and step records."""
        tool = self.tool_registry.get(tool_name)
        self._dispatch_event(state, AgentEventType.TOOL_REQUESTED, tool_name=tool_name)

        if tool is None:
            error = f"Tool '{tool_name}' is not registered."
            result = ToolExecutionResult(tool_name=tool_name, success=False, error=error)
            summary = ToolExecutionSummary(
                tool_name=tool_name, arguments=arguments, success=False, error=error
            )
            state.record_step(step_type="observation", description=error, tool_name=tool_name, error=error)
            self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool_name, success=False)
            return summary, result

        verdict = self.permission_manager.evaluate_detailed(
            tool_name=tool.name,
            permission_level=tool.permission_level,
            user_approved=user_approved or state.user_approved,
        )
        self._dispatch_event(
            state,
            AgentEventType.PERMISSION_CHECK,
            tool_name=tool.name,
            data={"decision": verdict.decision.value},
        )

        if verdict.decision == PermissionDecision.DENIED:
            error = verdict.reason or f"'{tool.name}' was denied by security policy."
            result = ToolExecutionResult(tool_name=tool.name, success=False, error=error)
            summary = ToolExecutionSummary(
                tool_name=tool.name, arguments=arguments, success=False, error=error
            )
            state.record_step(step_type="observation", description=error, tool_name=tool.name, error=error)
            self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool.name, success=False)
            return summary, result

        if verdict.decision == PermissionDecision.REQUIRES_CONFIRMATION:
            result = ToolExecutionResult(
                tool_name=tool.name, success=False, error="__REQUIRES_CONFIRMATION__"
            )
            summary = ToolExecutionSummary(
                tool_name=tool.name,
                arguments=arguments,
                success=False,
                error="REQUIRES_CONFIRMATION",
            )
            return summary, result

        state.tool_call_count += 1
        self._dispatch_event(state, AgentEventType.TOOL_STARTED, tool_name=tool.name)
        default_event_bus.publish(
            Topics.TOOL_STARTED,
            {"task_id": state.task_id, "tool": tool.name, "arguments": _safe_args(arguments)},
        )

        exec_result = await tool.execute(timeout=settings.PER_TOOL_TIMEOUT_SECONDS, **arguments)

        summary = ToolExecutionSummary(
            tool_name=exec_result.tool_name,
            arguments=arguments,
            success=exec_result.success,
            result=exec_result.result,
            error=exec_result.error,
        )
        state.record_step(
            step_type="observation",
            description=(
                f"Tool '{tool.name}' {'succeeded' if exec_result.success else 'failed'}."
            ),
            tool_name=tool.name,
            tool_args=arguments,
            result=exec_result.result if exec_result.success else None,
            error=exec_result.error,
        )
        state.observations.append(
            {
                "tool_name": tool.name,
                "success": exec_result.success,
                "result": exec_result.result if exec_result.success else None,
                "error": exec_result.error,
            }
        )
        self._dispatch_event(
            state,
            AgentEventType.TOOL_COMPLETED if exec_result.success else AgentEventType.TOOL_FAILED,
            tool_name=tool.name,
            success=exec_result.success,
        )
        default_event_bus.publish(
            Topics.TOOL_COMPLETED if exec_result.success else Topics.TOOL_FAILED,
            {
                "task_id": state.task_id,
                "tool": tool.name,
                "success": exec_result.success,
                "summary": exec_result.spoken_or_display()[:200],
            },
        )
        return summary, exec_result

    def _confirmation_response(
        self, state: AgentState, tool_name: str, arguments: Dict[str, Any], intent: str
    ) -> ChatResponse:
        pending = {
            "pending_id": f"pending_{uuid.uuid4().hex[:8]}",
            "tool_name": tool_name,
            "arguments": arguments,
        }
        state.pending_tool_call = pending
        state.update_status(TaskStatus.WAITING_FOR_CONFIRMATION)
        self._dispatch_event(state, AgentEventType.CONFIRMATION_REQUIRED, tool_name=tool_name)
        default_event_bus.publish(
            Topics.TOOL_CONFIRM,
            {"task_id": state.task_id, "tool": tool_name, "arguments": _safe_args(arguments)},
        )
        return self._response(
            state,
            f"'{tool_name}' needs your confirmation before I run it. Approve or reject in the panel.",
            handler="confirmation",
            intent=intent,
            status=TaskStatus.WAITING_FOR_CONFIRMATION,
            speech=f"Do you want me to run {tool_name.replace('_', ' ')}?",
            pending_action=pending,
            error="REQUIRES_CONFIRMATION",
        )

    async def _remember_turn(self, state: AgentState, final_text: str) -> None:
        if state.conversation_id:
            try:
                await self.conversation_memory.remember(
                    state.conversation_id,
                    [
                        {"role": "user", "content": state.user_input},
                        {"role": "assistant", "content": final_text},
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Conversation memory store failed: %s", exc)

    def _response(
        self,
        state: AgentState,
        text: str,
        *,
        handler: str = "agent",
        intent: Optional[str] = None,
        tools: Optional[List[ToolExecutionSummary]] = None,
        speech: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
        ui: Optional[Dict[str, Any]] = None,
        status: TaskStatus = TaskStatus.COMPLETED,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        pending_action: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> ChatResponse:
        lang_det = state.metadata.get("language_detection")
        det_lang_code = getattr(lang_det, "language", None)
        det_lang_val = det_lang_code.value if hasattr(det_lang_code, "value") else str(det_lang_code or "en")
        resp_lang_val = state.metadata.get("target_response_language", det_lang_val)

        return ChatResponse(
            task_id=state.task_id,
            correlation_id=state.correlation_id,
            response=text,
            speech=speech or None,
            intent_detected=intent,
            handler=handler,
            tools_executed=tools or [],
            artifacts=artifacts or [],
            ui=ui or {},
            status=status.value,
            provider=provider or state.provider or "iris",
            model=model or state.model or settings.DEFAULT_MODEL,
            mode=settings.LLM_MODE,
            language=det_lang_val,
            response_language=resp_lang_val,
            pending_action=pending_action,
            error=error,
        )

    def _finish(self, state: AgentState, response: ChatResponse) -> ChatResponse:
        default_event_bus.publish(
            Topics.AGENT_COMPLETED if response.status != TaskStatus.FAILED.value else Topics.AGENT_FAILED,
            {
                "task_id": state.task_id,
                "handler": response.handler,
                "status": response.status,
                "response": response.response[:400],
                "speech": response.speech,
                "artifacts": response.artifacts,
            },
        )
        return response

    def _dispatch_event(
        self,
        state: AgentState,
        event_type: AgentEventType,
        tool_name: Optional[str] = None,
        success: Optional[bool] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = AgentEvent(
            event_id=f"evt_{uuid.uuid4().hex[:8]}",
            task_id=state.task_id,
            correlation_id=state.correlation_id,
            event_type=event_type,
            tool_name=tool_name,
            success=success,
            data=data or {},
        )
        self.event_dispatcher.dispatch(event)


def _safe_args(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Truncate long argument values for event payloads."""
    safe: Dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        text = str(value)
        safe[key] = text[:120] + "…" if len(text) > 120 else value
    return safe


default_kernel = AgentKernel()
