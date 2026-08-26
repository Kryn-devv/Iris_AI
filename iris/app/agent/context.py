"""Context Assembler for building structured system, task, tool, and memory context for LLM decisions."""

from typing import Dict, Any, Optional, List
from iris.app.agent.prompts import get_system_prompt
from iris.app.agent.state import AgentState
from iris.app.tools.registry import ToolRegistry
from iris.app.tools.adapter import ToolSchemaAdapter
from iris.app.memory.working import WorkingMemory
from iris.app.memory.conversation import ConversationMemory
from iris.app.memory.service import MemoryService
from iris.app.core.logging import get_logger

logger = get_logger("agent.context")


class ContextAssembler:
    """Assembles prompt and message context for OpenAI-compatible chat completion."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        working_memory: Optional[WorkingMemory] = None,
        conversation_memory: Optional[ConversationMemory] = None,
        memory_service: Optional[MemoryService] = None,
    ):
        self.tool_registry = tool_registry
        self.working_memory = working_memory
        self.conversation_memory = conversation_memory
        self.memory_service = memory_service

    async def assemble_context(
        self,
        state: AgentState,
        custom_system_context: str = "",
    ) -> Dict[str, Any]:
        """Build structured messages payload and tool schemas for LLM provider."""
        memory_context_parts = []
        if custom_system_context:
            memory_context_parts.append(custom_system_context)

        # Retrieve relevant memories via MemoryService if available
        if self.memory_service:
            search_results = await self.memory_service.search(
                query=state.user_input,
                limit=5,
                min_relevance=0.1,
            )
            if search_results:
                mem_lines = []
                for record, score in search_results:
                    mem_lines.append(f"- [{record.type.value.upper()}] {record.key}: {record.value or record.content}")
                memory_context_parts.append("\n[RELEVANT MEMORY]\n" + "\n".join(mem_lines))

            # Retrieve project context
            project_records = await self.memory_service.project_memory.get_project_records("default")
            if project_records:
                proj_lines = [f"- {r.key}: {r.value}" for r in project_records]
                memory_context_parts.append("\n[PROJECT CONTEXT]\n" + "\n".join(proj_lines))

        # Retrieve language metadata if attached to state
        lang_det = state.metadata.get("language_detection")
        if lang_det:
            lang_code = getattr(lang_det, "language", None)
            lang_val = lang_code.value if hasattr(lang_code, "value") else str(lang_code or "en")
            resp_lang = state.metadata.get("target_response_language", lang_val)
            style_val = getattr(getattr(lang_det, "style", None), "value", "ENGLISH")
            has_explicit = "true" if getattr(lang_det, "explicit_request", None) else "false"

            directives = {
                "hi": (
                    "IMPORTANT: The user is speaking Hindi. Reply ENTIRELY in Hindi "
                    "(Devanagari script). Keep technical terms in English where natural."
                ),
                "hinglish": (
                    "IMPORTANT: The user is speaking Hinglish (Hindi-English mix in Latin "
                    "script). Reply in the SAME casual Hinglish style — Hindi grammar with "
                    "everyday English words, written in Latin script. Example: 'Haan bilkul, "
                    "maine file bana di hai. Check karo Downloads folder mein.'"
                ),
            }
            directive = directives.get(
                str(resp_lang).lower(),
                "Reply in clear, natural English." if str(resp_lang).lower() == "en" else "",
            )
            lang_block = (
                f"\n[LANGUAGE CONTEXT]\n"
                f"detected={lang_val}\n"
                f"response_language={resp_lang}\n"
                f"style={style_val.lower()}\n"
                f"explicit_request={has_explicit}\n"
                f"{directive}"
            )
            memory_context_parts.append(lang_block)

        full_custom_context = "\n\n".join(memory_context_parts)

        # 1. System Prompt
        sys_prompt = get_system_prompt(custom_context=full_custom_context)

        # 2. Tool Definitions
        registered_tools = self.tool_registry.list_tools()
        tool_schemas = ToolSchemaAdapter.convert_many(registered_tools) if registered_tools else []

        # 3. Message History
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": sys_prompt}
        ]

        # Append conversation history if conversation_id exists
        if self.conversation_memory and state.conversation_id:
            conv_history = await self.conversation_memory.retrieve(state.conversation_id)
            if conv_history:
                messages.extend(conv_history)

        # Append user input
        messages.append({"role": "user", "content": state.user_input})

        # Append prior step records and observations from active task execution
        for record in state.steps:
            if record.step_type == "tool_call" and record.tool_name:
                messages.append({
                    "role": "assistant",
                    "content": f"Invoking tool '{record.tool_name}' with args {record.tool_args or {}}",
                })
            elif record.step_type == "observation":
                result_str = f"Result: {record.result}" if record.result is not None else f"Error: {record.error}"
                messages.append({
                    "role": "tool",
                    "content": f"Observation from '{record.tool_name or 'tool'}': {result_str}",
                })

        logger.debug(f"Assembled context for task '{state.task_id}' with {len(messages)} messages and {len(tool_schemas)} tools")
        return {
            "system_prompt": sys_prompt,
            "messages": messages,
            "tools": tool_schemas,
        }
