"""Prove, on demand, that a command means the same thing every time.

A language model can word the same answer a hundred different ways. IRIS does
not put the model between your words and the robot's wheels: a command is
matched by a fixed rule and dispatched to a typed tool, with no model in the
loop. This tool demonstrates that live — run one phrase through the command
engine a hundred times and count how many distinct decisions came out. The
honest answer for a command is "one"; the honest answer for a chat question
is "that one goes to the model, so its wording can vary".
"""

from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any, Dict, Optional

from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool, ToolError

DEFAULT_PHRASE = "take a U-turn"
MAX_TIMES = 1000


class ReliabilityCheckTool(BaseTool):
    name = "reliability_check"
    description = (
        "Show that commands are deterministic: run one phrase through the command engine "
        "N times (default 100) and report how many distinct decisions came out, how long it "
        "took, and that no AI model was involved. Answers 'are you reliable', 'prove you are "
        "reliable', 'reliability test'."
    )
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    aliases = ["reliability test", "consistency check", "are you reliable", "prove you are reliable"]
    input_schema = ToolParameterSchema(
        properties={
            "phrase": {"type": "string", "description": f"The command to test (default '{DEFAULT_PHRASE}')."},
            "times": {"type": "integer", "minimum": 1, "maximum": MAX_TIMES, "description": "How many runs (default 100)."},
        },
    )
    examples = [
        ToolExample(utterance="are you reliable", arguments={}),
        ToolExample(utterance="reliability test on go back to the board",
                    arguments={"phrase": "go back to the board"}),
    ]

    async def _run(self, phrase: Optional[str] = None, times: int = 100) -> Dict[str, Any]:
        phrase = (phrase or DEFAULT_PHRASE).strip()
        try:
            times = max(1, min(MAX_TIMES, int(times)))
        except (TypeError, ValueError):
            times = 100
        from iris.app.nlu.engine import default_intent_engine

        outcomes: Counter[str] = Counter()
        decision: Optional[Dict[str, Any]] = None
        started = time.perf_counter()
        for _ in range(times):
            match = default_intent_engine.match(phrase)
            if match is None:
                outcomes["no fixed rule"] += 1
                continue
            decision = {"tool": match.tool_name, "arguments": match.arguments}
            outcomes[json.dumps(decision, sort_keys=True, default=str)] += 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        distinct = len(outcomes)

        if decision is None:
            speech = (
                f"'{phrase}' is not one of my fixed commands, so it would go to the conversation "
                "model, whose wording can differ each time. Commands — moving, turning, sensors, "
                "the camera, reminders — never touch the model: same words, same action, every time."
            )
            return {"speech": speech, "display": speech, "phrase": phrase, "times": times,
                    "distinct_decisions": distinct, "deterministic": False, "model_involved": True,
                    "elapsed_ms": round(elapsed_ms, 2)}

        args = ", ".join(f"{k} {v}" for k, v in decision["arguments"].items()) or "no arguments"
        identical = outcomes.most_common(1)[0][1]
        speech = (
            f"I ran '{phrase}' {times} times. {identical} out of {times} came out identical: "
            f"{decision['tool'].replace('_', ' ')}, {args}. No AI model was involved — this is a "
            f"fixed rule, like a reflex, and it took {elapsed_ms:.0f} milliseconds for all {times}."
        )
        if distinct != 1:
            speech = (
                f"I ran '{phrase}' {times} times and got {distinct} different decisions. "
                "That should not happen for a command — please report it."
            )
        return {
            "speech": speech,
            "display": speech,
            "phrase": phrase,
            "times": times,
            "distinct_decisions": distinct,
            "deterministic": distinct == 1,
            "model_involved": False,
            "decision": decision,
            "elapsed_ms": round(elapsed_ms, 2),
        }


def get_tools() -> list[BaseTool]:
    return [ReliabilityCheckTool()]
