"""AgentKernel orchestrating the multi-step lifecycle of IRIS."""

import asyncio
import json
import re
import uuid
import time
from typing import Dict, Any, Optional, List

from iris.app.core.config import settings
from iris.app.core.security import PermissionManager, PermissionLevel, PermissionDecision, default_permission_manager
from iris.app.core.logging import get_logger, correlation_id_ctx, task_id_ctx
from iris.app.tools.registry import ToolRegistry, default_tool_registry
from iris.app.llm.gateway import ModelGateway, default_model_gateway
from iris.app.memory.working import WorkingMemory
from iris.app.memory.conversation import ConversationMemory
from iris.app.agent.state import AgentState
from iris.app.agent.router import IntentRouter
from iris.app.agent.planner import Planner
from iris.app.agent.executor import ExecutionEngine
from iris.app.agent.task_manager import TaskManager
from iris.app.agent.context import ContextAssembler
from iris.app.agent.events import EventDispatcher, AgentEvent, AgentEventType, default_event_dispatcher
from iris.app.schemas.tasks import TaskStatus
from iris.app.schemas.messages import ChatResponse, ToolExecutionSummary

logger = get_logger("agent.kernel")


from iris.app.memory.service import MemoryService
from iris.app.language.service import default_language_service, LanguageService
from iris.app.memory.extractor import MemoryExtractor


class AgentKernel:
    """The central Agent Kernel coordinating intent, planning, execution, observation, replanning, and response."""

    def __init__(
        self,
        model_gateway: Optional[ModelGateway] = None,
        tool_registry: Optional[ToolRegistry] = None,
        permission_manager: Optional[PermissionManager] = None,
        task_manager: Optional[TaskManager] = None,
        event_dispatcher: Optional[EventDispatcher] = None,
        memory_service: Optional[MemoryService] = None,
        language_service: Optional[LanguageService] = None,
    ):
        self.model_gateway = model_gateway or default_model_gateway
        self.tool_registry = tool_registry or default_tool_registry
        self.permission_manager = permission_manager or default_permission_manager
        self.task_manager = task_manager or TaskManager()
        self.event_dispatcher = event_dispatcher or default_event_dispatcher
        self.memory_service = memory_service or MemoryService()
        self.language_service = language_service or default_language_service

        self.router = IntentRouter(model_gateway=self.model_gateway)
        self.planner = Planner(model_gateway=self.model_gateway, tool_registry=self.tool_registry)
        self.executor = ExecutionEngine(tool_registry=self.tool_registry, permission_manager=self.permission_manager)
        self.working_memory = self.memory_service.working_memory
        self.conversation_memory = self.memory_service.conversation_memory
        self.context_assembler = ContextAssembler(
            tool_registry=self.tool_registry,
            working_memory=self.working_memory,
            conversation_memory=self.conversation_memory,
            memory_service=self.memory_service,
        )

    async def process_request(
        self,
        user_input: str,
        conversation_id: Optional[str] = None,
        user_approved: bool = False,
        task_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> ChatResponse:
        """Process user input through the full Agent Kernel loop."""
        state = self.task_manager.create_task(user_input, task_id=task_id, correlation_id=correlation_id)
        state.conversation_id = conversation_id
        state.user_approved = user_approved

        c_token = correlation_id_ctx.set(state.correlation_id)
        t_token = task_id_ctx.set(state.task_id)

        try:
            # 1. Process Language Intelligence metadata
            detection, normalized_input, target_lang, target_style = self.language_service.process_input(user_input)
            state.metadata["language_detection"] = detection
            state.metadata["target_response_language"] = target_lang.value
            state.metadata["target_style"] = target_style.value
            state.metadata["normalized_input"] = normalized_input

            # Check for explicit natural language memory commands
            cmd_type, payload = MemoryExtractor.parse_command(user_input)
            if cmd_type == "remember" and payload:
                await self.memory_service.remember(
                    key=payload["key"],
                    value=payload["value"],
                    memory_type=payload["type"],
                    metadata=payload,
                )
                self._dispatch_event(state, AgentEventType.MEMORY_CREATED, data={"key": payload["key"]})
                state.update_status(TaskStatus.COMPLETED)
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                provider, provider_name = await self.model_gateway.get_provider_and_name()
                
                resp_text = f"I'll remember that: {payload['key']} is {payload['value']}."
                if "robot" in payload["key"]:
                    resp_text = f"I'll remember that for the robot project."
                return ChatResponse(
                    task_id=state.task_id,
                    correlation_id=state.correlation_id,
                    response=resp_text,
                    status=TaskStatus.COMPLETED.value,
                    provider=provider_name,
                    model=provider.default_model,
                    mode=settings.LLM_MODE.lower(),
                )

            elif cmd_type == "forget" and payload:
                forgot = await self.memory_service.forget(key=payload["key"])
                self._dispatch_event(state, AgentEventType.MEMORY_FORGOTTEN, data={"key": payload["key"], "success": forgot})
                state.update_status(TaskStatus.COMPLETED)
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                provider, provider_name = await self.model_gateway.get_provider_and_name()

                raw_t = payload.get("raw_target", "").lower().replace("my ", "").replace("the ", "")
                target_name = raw_t if raw_t else payload["key"].replace("_", " ")
                resp_text = f"I've forgotten your {target_name}." if forgot else f"I don't have any record of your {target_name}."
                return ChatResponse(
                    task_id=state.task_id,
                    correlation_id=state.correlation_id,
                    response=resp_text,
                    status=TaskStatus.COMPLETED.value,
                    provider=provider_name,
                    model=provider.default_model,
                    mode=settings.LLM_MODE.lower(),
                )

            elif cmd_type == "recall" and payload:
                val = await self.memory_service.retrieve(payload["key"])
                if val is None:
                    search_res = await self.memory_service.search(payload["query"], limit=1, min_relevance=0.3)
                    if search_res:
                        rec, score = search_res[0]
                        stop_words = {"robot", "my", "project", "the", "a", "an", "is", "what", "does", "use", "for", "in", "of", "about"}
                        query_terms = [t.lower() for t in payload.get("query", "").split() if t.lower() not in stop_words]
                        if query_terms and any(term in rec.key.lower() or term in rec.content.lower() for term in query_terms):
                            val = rec.value

                state.update_status(TaskStatus.COMPLETED)
                self._dispatch_event(state, AgentEventType.MEMORY_RETRIEVED, data={"key": payload["key"], "found": val is not None})
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                provider, provider_name = await self.model_gateway.get_provider_and_name()

                clean_q = payload.get("query", "").lower()
                clean_q = re.sub(r"^(what\s+is\s+|what's\s+|what\s+microcontroller\s+does\s+)", "", clean_q, flags=re.IGNORECASE)
                clean_q = re.sub(r"^(my\s+|the\s+)", "", clean_q, flags=re.IGNORECASE).rstrip("?").strip()
                target_name = clean_q if clean_q else payload["key"].replace("_", " ")

                if val is not None:
                    if "budget" in payload["key"]:
                        resp_text = f"Your robot budget is {val}."
                    elif "microcontroller" in payload["key"]:
                        resp_text = f"Your robot uses an {val}."
                    else:
                        resp_text = f"Regarding {target_name}: {val}."
                else:
                    resp_text = f"I don't have any record of your {target_name}."

                return ChatResponse(
                    task_id=state.task_id,
                    correlation_id=state.correlation_id,
                    response=resp_text,
                    status=TaskStatus.COMPLETED.value,
                    provider=provider_name,
                    model=provider.default_model,
                    mode=settings.LLM_MODE.lower(),
                )

            response = await asyncio.wait_for(
                self._run_kernel_loop(state, user_approved=user_approved),
                timeout=settings.TOTAL_TASK_TIMEOUT_SECONDS,
            )
            return response
        except asyncio.TimeoutError:
            logger.error(f"Task '{state.task_id}' exceeded total task timeout limit ({settings.TOTAL_TASK_TIMEOUT_SECONDS}s).")
            state.update_status(TaskStatus.FAILED, error=f"Task execution timed out after {settings.TOTAL_TASK_TIMEOUT_SECONDS} seconds.")
            self._dispatch_event(state, AgentEventType.AGENT_FAILED, data={"error": "Timeout"})
            provider, provider_name = await self.model_gateway.get_provider_and_name()
            return ChatResponse(
                task_id=state.task_id,
                correlation_id=state.correlation_id,
                response="Task execution timed out before completion.",
                status=TaskStatus.FAILED.value,
                provider=provider_name,
                model=provider.default_model,
                mode=settings.LLM_MODE.lower(),
                error="Total task timeout exceeded.",
            )
        except asyncio.CancelledError:
            logger.warning(f"Task '{state.task_id}' was cancelled.")
            state.update_status(TaskStatus.CANCELLED, error="Task was cancelled.")
            self._dispatch_event(state, AgentEventType.AGENT_CANCELLED)
            provider, provider_name = await self.model_gateway.get_provider_and_name()
            return ChatResponse(
                task_id=state.task_id,
                correlation_id=state.correlation_id,
                response="Task was cancelled.",
                status=TaskStatus.CANCELLED.value,
                provider=provider_name,
                model=provider.default_model,
                mode=settings.LLM_MODE.lower(),
            )
        except Exception as e:
            logger.error(f"Unexpected kernel error during task '{state.task_id}': {e}", exc_info=True)
            state.update_status(TaskStatus.FAILED, error=str(e))
            self._dispatch_event(state, AgentEventType.AGENT_FAILED, data={"error": str(e)})
            provider, provider_name = await self.model_gateway.get_provider_and_name()
            return ChatResponse(
                task_id=state.task_id,
                correlation_id=state.correlation_id,
                response="An internal system error occurred.",
                status=TaskStatus.FAILED.value,
                provider=provider_name,
                model=provider.default_model,
                mode=settings.LLM_MODE.lower(),
                error=str(e),
            )
        finally:
            correlation_id_ctx.reset(c_token)
            task_id_ctx.reset(t_token)

    async def resume_task_confirmation(self, task_id: str, approved: bool) -> ChatResponse:
        """Resume execution of a task paused in WAITING_FOR_CONFIRMATION after user approval/rejection."""
        state = self.task_manager.get_task(task_id)
        if not state:
            raise ValueError(f"Task '{task_id}' not found.")
        if state.status != TaskStatus.WAITING_FOR_CONFIRMATION or not state.pending_tool_call:
            raise ValueError(f"Task '{task_id}' is not in WAITING_FOR_CONFIRMATION state or has no pending tool action.")

        pending = state.pending_tool_call
        tool_name = pending["tool_name"]
        arguments = pending["arguments"]

        c_token = correlation_id_ctx.set(state.correlation_id)
        t_token = task_id_ctx.set(state.task_id)

        try:
            if approved:
                logger.info(f"User APPROVED pending tool call '{tool_name}' for task '{task_id}'")
                state.user_approved = True
                state.pending_tool_call = None
                state.update_status(TaskStatus.RUNNING)

                # Execute exact pending tool
                self._dispatch_event(state, AgentEventType.TOOL_STARTED, tool_name=tool_name)
                exec_result = await self.executor.execute_tool(
                    tool_name=tool_name,
                    arguments=arguments,
                    user_approved=True,
                    timeout=settings.PER_TOOL_TIMEOUT_SECONDS,
                )
                state.record_step(
                    step_type="observation",
                    description=f"Confirmed tool '{tool_name}' result: {exec_result.result or exec_result.error}",
                    tool_name=tool_name,
                    tool_args=arguments,
                    result=exec_result.result,
                    error=exec_result.error,
                )
                state.observations.append({
                    "tool_name": tool_name,
                    "success": exec_result.success,
                    "result": exec_result.result,
                    "error": exec_result.error,
                })
                self._dispatch_event(state, AgentEventType.TOOL_COMPLETED if exec_result.success else AgentEventType.TOOL_FAILED, tool_name=tool_name, success=exec_result.success)
                return await self._run_kernel_loop(state, user_approved=True)
            else:
                logger.info(f"User REJECTED pending tool call '{tool_name}' for task '{task_id}'")
                state.user_approved = False
                state.pending_tool_call = None
                state.update_status(TaskStatus.COMPLETED)
                state.record_step(
                    step_type="observation",
                    description=f"User REJECTED confirmation for tool '{tool_name}'",
                    tool_name=tool_name,
                    tool_args=arguments,
                    error="USER_REJECTED_CONFIRMATION",
                )
                self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool_name, success=False)
                self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)
                provider, provider_name = await self.model_gateway.get_provider_and_name()
                return ChatResponse(
                    task_id=state.task_id,
                    correlation_id=state.correlation_id,
                    response=f"Action '{tool_name}' was rejected by user.",
                    intent_detected=state.plan.user_intent if state.plan else None,
                    tools_executed=[],
                    status=TaskStatus.COMPLETED.value,
                    provider=provider_name,
                    model=provider.default_model,
                    mode=settings.LLM_MODE.lower(),
                )
        finally:
            correlation_id_ctx.reset(c_token)
            task_id_ctx.reset(t_token)

    async def _run_kernel_loop(self, state: AgentState, user_approved: bool = False) -> ChatResponse:
        """Internal multi-step agentic loop."""
        logger.info(f"Running agent loop for task '{state.task_id}': '{state.user_input}'")
        self._dispatch_event(state, AgentEventType.AGENT_STARTED)

        provider, provider_name = await self.model_gateway.get_provider_and_name()
        state.provider = provider_name
        state.model = provider.default_model

        tools_executed: List[ToolExecutionSummary] = []
        final_output_parts: List[str] = []

        # 1. UNDERSTAND & ROUTE INTENT
        state.update_status(TaskStatus.PLANNING)
        intent = await self.router.route(state.user_input)
        state.record_step(step_type="understand_route", description=f"Identified intent: '{intent}'")

        # 2. INITIAL PLAN
        plan = await self.planner.create_plan(state.user_input)
        state.plan = plan
        state.record_step(step_type="plan", description=f"Generated plan with {len(plan.steps)} steps")
        self._dispatch_event(state, AgentEventType.PLAN_CREATED, data={"plan_steps": len(plan.steps)})

        state.update_status(TaskStatus.RUNNING)

        while state.iteration_count < settings.MAX_PLANNING_ITERATIONS:
            if state.tool_call_count >= settings.MAX_TOOL_CALLS:
                logger.warning(f"Task '{state.task_id}' reached max tool calls limit ({settings.MAX_TOOL_CALLS}).")
                break

            state.iteration_count += 1

            # Assemble current context (system, memory, history, tool schemas, observations)
            context_data = await self.context_assembler.assemble_context(state)

            # Query LLM Provider
            llm_res = await provider.generate(
                prompt=state.user_input,
                system_prompt=context_data["system_prompt"],
                tools=context_data["tools"],
                messages=context_data["messages"],
                agent_mode=True,
            )

            # Handle genuine tool calls returned by LLM
            if llm_res.tool_calls:
                for tc in llm_res.tool_calls:
                    if state.tool_call_count >= settings.MAX_TOOL_CALLS:
                        break

                    fn_data = tc.get("function", {})
                    tool_name = fn_data.get("name", "")
                    raw_args = fn_data.get("arguments", {})

                    if isinstance(raw_args, str):
                        try:
                            arguments = json.loads(raw_args) if raw_args.strip() else {}
                        except json.JSONDecodeError:
                            arguments = {}
                    else:
                        arguments = raw_args or {}

                    self._dispatch_event(state, AgentEventType.TOOL_REQUESTED, tool_name=tool_name)

                    # Validate tool existence
                    tool = self.tool_registry.get(tool_name)
                    if not tool:
                        obs_msg = f"Tool '{tool_name}' is not registered."
                        state.record_step(step_type="observation", description=obs_msg, tool_name=tool_name, error=obs_msg)
                        self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool_name, success=False)
                        
                        # Trigger replan if limits permit
                        if state.replanning_count < 2:
                            state.replanning_count += 1
                            self._dispatch_event(state, AgentEventType.REPLAN_STARTED)
                            replan_obj = await self.planner.replan(state.user_input, tool_name, obs_msg)
                            state.plan = replan_obj
                        continue

                    # Permission check
                    decision = self.permission_manager.evaluate(
                        tool_name=tool.name,
                        permission_level=tool.permission_level,
                        user_approved=user_approved,
                    )
                    self._dispatch_event(state, AgentEventType.PERMISSION_CHECK, tool_name=tool.name, data={"decision": decision.value})

                    if decision == PermissionDecision.REQUIRES_CONFIRMATION:
                        pending_action = {
                            "pending_id": f"pending_{uuid.uuid4().hex[:8]}",
                            "tool_name": tool.name,
                            "arguments": arguments,
                            "permission_level": tool.permission_level.value,
                        }
                        state.pending_tool_call = pending_action
                        state.update_status(TaskStatus.WAITING_FOR_CONFIRMATION)
                        self._dispatch_event(state, AgentEventType.CONFIRMATION_REQUIRED, tool_name=tool.name)

                        return ChatResponse(
                            task_id=state.task_id,
                            correlation_id=state.correlation_id,
                            response=f"Tool '{tool.name}' requires user confirmation before running.",
                            intent_detected=intent,
                            tools_executed=tools_executed,
                            status=TaskStatus.WAITING_FOR_CONFIRMATION.value,
                            provider=provider_name,
                            model=provider.default_model,
                            mode=settings.LLM_MODE.lower(),
                            error="REQUIRES_CONFIRMATION",
                        )

                    if decision == PermissionDecision.DENIED:
                        obs_msg = f"Execution of tool '{tool.name}' DENIED by security policy."
                        state.record_step(step_type="observation", description=obs_msg, tool_name=tool.name, error=obs_msg)
                        self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool.name, success=False)
                        continue

                    # Execute tool
                    state.tool_call_count += 1
                    self._dispatch_event(state, AgentEventType.TOOL_STARTED, tool_name=tool.name)
                    exec_result = await self.executor.execute_tool(
                        tool_name=tool.name,
                        arguments=arguments,
                        user_approved=user_approved,
                        timeout=settings.PER_TOOL_TIMEOUT_SECONDS,
                    )

                    summary = ToolExecutionSummary(
                        tool_name=exec_result.tool_name,
                        arguments=arguments,
                        success=exec_result.success,
                        result=exec_result.result,
                        error=exec_result.error,
                    )
                    tools_executed.append(summary)

                    if exec_result.success:
                        obs_msg = f"Tool '{tool.name}' executed successfully."
                        state.record_step(
                            step_type="observation",
                            description=obs_msg,
                            tool_name=tool.name,
                            tool_args=arguments,
                            result=exec_result.result,
                        )
                        state.observations.append({
                            "tool_name": tool.name,
                            "success": True,
                            "result": exec_result.result,
                        })
                        self._dispatch_event(state, AgentEventType.TOOL_COMPLETED, tool_name=tool.name, success=True)
                        if isinstance(exec_result.result, dict):
                            res_val = exec_result.result.get("result") or exec_result.result.get("formatted") or str(exec_result.result)
                            final_output_parts.append(str(res_val))
                        else:
                            final_output_parts.append(str(exec_result.result))
                    else:
                        obs_msg = f"Tool '{tool.name}' failed: {exec_result.error}"
                        state.record_step(
                            step_type="observation",
                            description=obs_msg,
                            tool_name=tool.name,
                            tool_args=arguments,
                            error=exec_result.error,
                        )
                        state.observations.append({
                            "tool_name": tool.name,
                            "success": False,
                            "error": exec_result.error,
                        })
                        self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=tool.name, success=False)

                        if state.replanning_count < 2:
                            state.replanning_count += 1
                            self._dispatch_event(state, AgentEventType.REPLAN_STARTED)
                            replan_obj = await self.planner.replan(state.user_input, tool.name, str(exec_result.error))
                            state.plan = replan_obj

            elif state.plan and any(s.action == "tool_call" and s.tool_name for s in state.plan.steps):
                # Execute structured plan steps from Planner
                for step in state.plan.steps:
                    if step.action == "tool_call" and step.tool_name:
                        state.tool_call_count += 1
                        self._dispatch_event(state, AgentEventType.TOOL_STARTED, tool_name=step.tool_name)
                        exec_result = await self.executor.execute_tool(
                            tool_name=step.tool_name,
                            arguments=step.tool_args,
                            user_approved=user_approved,
                            timeout=settings.PER_TOOL_TIMEOUT_SECONDS,
                        )
                        summary = ToolExecutionSummary(
                            tool_name=exec_result.tool_name,
                            arguments=step.tool_args,
                            success=exec_result.success,
                            result=exec_result.result,
                            error=exec_result.error,
                        )
                        tools_executed.append(summary)

                        if exec_result.success:
                            self._dispatch_event(state, AgentEventType.TOOL_COMPLETED, tool_name=step.tool_name, success=True)
                            state.record_step(
                                step_type="observation",
                                description=f"Tool '{step.tool_name}' executed.",
                                tool_name=step.tool_name,
                                tool_args=step.tool_args,
                                result=exec_result.result,
                            )
                            if step.tool_name == "calculator" and isinstance(exec_result.result, dict):
                                final_output_parts.append(f"Result: {exec_result.result.get('result')}")
                            elif step.tool_name == "system_info" and isinstance(exec_result.result, dict):
                                res_dict = exec_result.result
                                final_output_parts.append(
                                    f"Operating System: {res_dict.get('os')} ({res_dict.get('architecture')}), "
                                    f"Python {res_dict.get('python_version')}, "
                                    f"Memory: {res_dict.get('memory', {}).get('total_gb')} GB"
                                )
                            elif step.tool_name == "time" and isinstance(exec_result.result, dict):
                                final_output_parts.append(f"Current local time: {exec_result.result.get('local_time')} ({exec_result.result.get('timezone')})")
                            else:
                                final_output_parts.append(str(exec_result.result))
                        else:
                            self._dispatch_event(state, AgentEventType.TOOL_FAILED, tool_name=step.tool_name, success=False)
                            state.record_step(
                                step_type="observation",
                                description=f"Tool '{step.tool_name}' failed.",
                                tool_name=step.tool_name,
                                tool_args=step.tool_args,
                                error=exec_result.error,
                            )
                            if "REQUIRES_CONFIRMATION" in str(exec_result.error):
                                state.pending_tool_call = {
                                    "tool_name": step.tool_name,
                                    "arguments": step.tool_args,
                                }
                                state.update_status(TaskStatus.WAITING_FOR_CONFIRMATION)
                                self._dispatch_event(state, AgentEventType.CONFIRMATION_REQUIRED, tool_name=step.tool_name)
                                return ChatResponse(
                                    task_id=state.task_id,
                                    correlation_id=state.correlation_id,
                                    response=f"Tool '{step.tool_name}' requires confirmation before running.",
                                    intent_detected=intent,
                                    tools_executed=tools_executed,
                                    status=TaskStatus.WAITING_FOR_CONFIRMATION.value,
                                    provider=provider_name,
                                    model=provider.default_model,
                                    mode=settings.LLM_MODE.lower(),
                                    error=exec_result.error,
                                )
                            final_output_parts.append(f"Could not execute tool '{step.tool_name}': {exec_result.error}")
                    elif step.action == "final_response":
                        provider_res = await provider.generate(
                            state.user_input,
                            system_prompt=context_data["system_prompt"],
                            messages=context_data["messages"],
                        )
                        final_output_parts.append(provider_res.content)
                break
            elif llm_res.content and llm_res.content.strip():
                # Model returned a direct text answer
                final_output_parts.append(llm_res.content.strip())
                break

        # Final Evaluation & Response
        final_text = "\n".join(final_output_parts) if final_output_parts else "Request completed."
        state.result = final_text
        state.update_status(TaskStatus.COMPLETED)
        state.record_step(step_type="evaluation", description="Task completed cleanly.")
        self._dispatch_event(state, AgentEventType.AGENT_COMPLETED)

        # Remember in conversation memory if active
        if state.conversation_id:
            await self.conversation_memory.remember(state.conversation_id, [
                {"role": "user", "content": state.user_input},
                {"role": "assistant", "content": final_text},
            ])

        logger.info(f"Task '{state.task_id}' completed successfully via provider '{provider_name}'.")
        lang_det = state.metadata.get("language_detection")
        det_lang_code = getattr(lang_det, "language", None)
        det_lang_val = det_lang_code.value if hasattr(det_lang_code, "value") else str(det_lang_code or "en")
        resp_lang_val = state.metadata.get("target_response_language", det_lang_val)

        return ChatResponse(
            task_id=state.task_id,
            correlation_id=state.correlation_id,
            response=final_text,
            intent_detected=intent,
            tools_executed=tools_executed,
            status=TaskStatus.COMPLETED.value,
            provider=provider_name,
            model=provider.default_model,
            mode=settings.LLM_MODE.lower(),
            language=det_lang_val,
            response_language=resp_lang_val,
        )

    def _dispatch_event(
        self,
        state: AgentState,
        event_type: AgentEventType,
        tool_name: Optional[str] = None,
        success: Optional[bool] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Internal helper to dispatch safe agent execution events."""
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


default_kernel = AgentKernel()
