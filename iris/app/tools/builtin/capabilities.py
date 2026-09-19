"""What she can actually do, read off the registry rather than remembered.

Asked to show everything she could do, Iris wrote a demo. Not ran one — wrote
one. A timer that was never started, a reminder that was never set, a note
that was never added, and a machine with twelve cores and sixty-four gigabytes
of memory, reported to someone sitting at a four-core MacBook Air. Every line
of it plausible, none of it true.

Part of that was a missing capability: there was no tool that answers "what can
you do", so the only material the model had for the question was its own
imagination. This is that tool. It reports the live registry — what is
registered, what is available on this machine right now, and what is not — so
the answer to the question is a fact rather than a performance.

Deliberately not a document: the grouping is coarse and the wording is plain,
because this gets read aloud as often as it gets looked at.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import ToolCategory, ToolExample, ToolParameterSchema
from iris.app.tools.base import BaseTool

#: Category -> how a person would describe that group out loud.
_GROUP_LABELS: Dict[str, str] = {
    ToolCategory.DESKTOP: "driving this computer",
    ToolCategory.SYSTEM: "checking the machine",
    ToolCategory.FILES: "files and folders",
    ToolCategory.WEB: "the open web",
    ToolCategory.CONTENT: "making documents, decks and sheets",
    ToolCategory.CODE: "writing and running code",
    ToolCategory.MEDIA: "media",
    ToolCategory.AUTOMATION: "timers, reminders and routines",
    ToolCategory.KNOWLEDGE: "looking things up",
    ToolCategory.COMMUNICATION: "messages",
    ToolCategory.CORE: "the basics",
}

#: How many tool names to name out loud per group before saying "and N more".
_NAMES_PER_GROUP = 6


class CapabilitiesTool(BaseTool):
    name = "capabilities"
    description = (
        "List what IRIS can actually do, read live from the tool registry: which tools are "
        "registered, which are available on this machine, and which are not. Use this for "
        "'what can you do', 'show me everything', 'list your tools', 'demo yourself', "
        "'test all your functions' — instead of describing capabilities from memory."
    )
    category = ToolCategory.SYSTEM
    permission_level = PermissionLevel.READ
    aliases = [
        "what can you do", "list tools", "show tools", "your abilities",
        "your functions", "demo", "abilities", "features",
    ]
    input_schema = ToolParameterSchema(
        properties={
            "group": {
                "type": "string",
                "description": (
                    "Only this category: desktop, system, files, web, content, code, "
                    "media, automation, knowledge, core."
                ),
            },
            "detail": {
                "type": "boolean",
                "description": "Include every tool name rather than a sample (default false).",
            },
        },
    )
    examples = [
        ToolExample(utterance="what can you do", arguments={}),
        ToolExample(utterance="show me everything you can do", arguments={"detail": True}),
        ToolExample(utterance="what can you do with files", arguments={"group": "files"}),
    ]

    async def _run(
        self,
        group: Optional[str] = None,
        detail: bool = False,
        **_: Any,
    ) -> Dict[str, Any]:
        registry = self.tool_registry
        if registry is None:          # not registered anywhere: use the global one
            from iris.app.tools.registry import default_tool_registry

            registry = default_tool_registry

        wanted = (group or "").strip().lower() or None

        groups: Dict[str, Dict[str, List[str]]] = {}
        unavailable: List[str] = []
        total = 0
        for tool in registry.tools():
            meta = tool.get_metadata()
            category = str(getattr(meta, "category", "") or ToolCategory.CORE).lower()
            if wanted and category != wanted:
                continue
            total += 1
            bucket = groups.setdefault(category, {"available": [], "missing": []})
            if tool.is_available():
                bucket["available"].append(meta.name)
            else:
                bucket["missing"].append(meta.name)
                unavailable.append(meta.name)

        if not groups:
            if wanted:
                known = ", ".join(sorted(_GROUP_LABELS))
                return {
                    "speech": f"I have no tools in a group called {wanted}. "
                              f"The groups I do have are {known}.",
                    "display": f"No tool group named {wanted!r}. Groups: {known}.",
                    "groups": {}, "total": 0, "available": 0, "unavailable": [],
                }
            # An empty catalogue is a real answer, not an error: it is what a
            # half-initialised process looks like, and saying so beats
            # inventing a list.
            return {
                "speech": "No tools are loaded right now, so I can't do anything yet.",
                "display": "The tool registry is empty.",
                "groups": {}, "total": 0, "available": 0, "unavailable": [],
            }

        lines: List[str] = []
        spoken_bits: List[str] = []
        for category in sorted(groups, key=lambda c: -len(groups[c]["available"])):
            names = sorted(groups[category]["available"])
            if not names:
                continue
            label = _GROUP_LABELS.get(category, category)
            shown = names if detail else names[:_NAMES_PER_GROUP]
            more = len(names) - len(shown)
            listed = ", ".join(shown) + (f", and {more} more" if more else "")
            lines.append(f"{label} ({len(names)}): {listed}")
            spoken_bits.append(f"{label}, {len(names)}")

        available_count = sum(len(g["available"]) for g in groups.values())
        headline = (
            f"{available_count} of {total} tools work on this machine right now"
            if not wanted else
            f"{available_count} of {total} {wanted} tools work here"
        )
        if unavailable:
            lines.append(
                f"Not available here ({len(unavailable)}): " + ", ".join(sorted(unavailable))
            )

        speech = headline + ". " + "; ".join(spoken_bits[:4]) + "."
        return {
            "speech": speech,
            "display": headline + ".\n" + "\n".join(lines),
            "total": total,
            "available": available_count,
            "groups": {c: g["available"] for c, g in groups.items()},
            "unavailable": sorted(unavailable),
        }


def get_tools() -> list[BaseTool]:
    return [CapabilitiesTool()]
