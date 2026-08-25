"""Voice-related tools."""

from __future__ import annotations

from typing import Any

from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError
from iris.app.voice.service import default_voice_service


class SpeakTool(BaseTool):
    """Say a sentence out loud."""

    name = "speak"
    description = "Speak the given text aloud through text-to-speech."
    permission_level = PermissionLevel.LOW_RISK_ACTION
    category = ToolCategory.MEDIA
    aliases = ("say", "talk", "read_aloud", "text_to_speech")
    examples = (ToolExample(utterance="say hello world", arguments={"text": "hello world"}),)
    input_schema = ToolParameterSchema(
        properties={"text": {"type": "string", "description": "What to say."}},
        required=["text"],
    )

    async def _run(self, text: str, **_: Any) -> dict[str, Any]:
        if not (text or "").strip():
            raise ToolError("There's nothing to say.")
        outcome = await default_voice_service.speak(text)
        return {
            "spoken": outcome["spoken"],
            "engine": outcome["engine"],
            # Deliberately no extra 'speech': the text itself was the speech.
            "display": f"🔊 {outcome['text']}",
        }


def get_tools() -> list[BaseTool]:
    return [SpeakTool()]
