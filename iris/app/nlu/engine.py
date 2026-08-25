"""Deterministic intent engine: instant command understanding without any LLM.

"Open YouTube", "volume up", "take a screenshot", "remind me in 10 minutes to
stretch" — commands like these should execute in milliseconds, offline, every
time, with zero API calls. The intent engine is an ordered catalogue of
compiled regex rules with typed slot extractors. When a rule matches above the
confidence threshold the kernel dispatches the mapped tool directly; anything
the catalogue doesn't recognize falls through to the LLM agent loop (or the
offline reasoner when no model is configured).

The engine is pure and dependency-free so it is trivially unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Pattern

from iris.app.core.logging import get_logger
from iris.app.nlu.rules import RULES, Rule, normalize_command

logger = get_logger("nlu.engine")


@dataclass
class IntentMatch:
    """A resolved intent ready for direct tool dispatch."""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    intent: str = ""
    rule_name: str = ""
    #: When True the kernel should still compose the final answer with a model
    #: (e.g. content-generation intents where the tool needs generated input).
    needs_generation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "confidence": self.confidence,
            "intent": self.intent,
            "rule_name": self.rule_name,
            "needs_generation": self.needs_generation,
        }


class IntentEngine:
    """Ordered-rule matcher producing :class:`IntentMatch` objects."""

    def __init__(self, rules: Optional[List[Rule]] = None):
        self.rules: List[Rule] = list(rules) if rules is not None else list(RULES)

    def match(self, text: str) -> Optional[IntentMatch]:
        """Return the best intent match for ``text``, or ``None``."""
        cleaned = normalize_command(text)
        if not cleaned:
            return None

        for rule in self.rules:
            m = rule.pattern.search(cleaned)
            if not m:
                continue
            try:
                arguments = rule.build(m, cleaned)
            except ValueError:
                # A slot extractor rejected the surface match; keep scanning.
                continue
            if arguments is None:
                continue
            # Rules with tool "__dynamic__" decide the target tool in their
            # builder and pass it back via the "__tool__" argument.
            tool_name = rule.tool
            dynamic = arguments.pop("__tool__", None)
            if dynamic:
                tool_name = dynamic
            elif tool_name == "__dynamic__":
                continue
            match = IntentMatch(
                tool_name=tool_name,
                arguments=arguments,
                confidence=rule.confidence,
                intent=rule.intent,
                rule_name=rule.name,
                needs_generation=rule.needs_generation,
            )
            logger.debug("NLU matched %r -> %s%s", cleaned, rule.tool, match.arguments)
            return match
        return None

    def explain(self, text: str) -> Dict[str, Any]:
        """Debug helper: what would the engine do with this text?"""
        match = self.match(text)
        return {
            "input": text,
            "normalized": normalize_command(text),
            "match": match.to_dict() if match else None,
        }


default_intent_engine = IntentEngine()
