"""Tool registry for dynamic tool registration, discovery and lookup."""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from iris.app.core.logging import get_logger
from iris.app.schemas.tools import ToolMetadata
from iris.app.tools.base import BaseTool

logger = get_logger("tools.registry")


class ToolRegistry:
    """Registry maintaining every tool available to IRIS."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}
        self._aliases: Dict[str, str] = {}

    # ------------------------------------------------------------ registration
    def register(self, tool: BaseTool, *, quiet: bool = False) -> BaseTool:
        """Register a tool, replacing any previous tool with the same name."""
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} must define a non-empty 'name'.")

        if tool.name in self._tools and not quiet:
            logger.warning("Overwriting existing registered tool: %s", tool.name)

        self._tools[tool.name] = tool
        for alias in tool.aliases:
            key = alias.strip().lower()
            if key and key not in self._tools:
                self._aliases[key] = tool.name

        if not quiet:
            reason = tool.unavailable_reason()
            if reason:
                logger.info("Registered tool: %s (unavailable: %s)", tool.name, reason)
            else:
                logger.info(
                    "Registered tool: %s [%s] (permission: %s)",
                    tool.name, tool.category, tool.permission_level.value,
                )
        return tool

    def register_many(self, tools: Iterable[BaseTool], *, quiet: bool = False) -> int:
        """Register an iterable of tools and return how many were added."""
        count = 0
        for tool in tools:
            try:
                self.register(tool, quiet=quiet)
                count += 1
            except Exception as exc:  # noqa: BLE001 - a bad tool must not abort boot
                logger.error("Failed to register tool %r: %s", tool, exc)
        return count

    def unregister(self, name: str) -> bool:
        """Remove a tool and its aliases."""
        if name not in self._tools:
            return False
        del self._tools[name]
        for alias, target in list(self._aliases.items()):
            if target == name:
                del self._aliases[alias]
        return True

    # ----------------------------------------------------------------- lookup
    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a tool by exact name, then by alias, case-insensitively."""
        if not name:
            return None
        if name in self._tools:
            return self._tools[name]
        key = name.strip().lower()
        if key in self._tools:
            return self._tools[key]
        target = self._aliases.get(key)
        return self._tools.get(target) if target else None

    def is_registered(self, name: str) -> bool:
        return self.get(name) is not None

    def resolve_name(self, name: str) -> Optional[str]:
        """Canonical tool name for an alias or differently-cased name."""
        tool = self.get(name)
        return tool.name if tool else None

    # ---------------------------------------------------------------- listings
    def tools(self, *, available_only: bool = False) -> List[BaseTool]:
        items = list(self._tools.values())
        if available_only:
            items = [t for t in items if t.is_available()]
        return items

    def list_tools(self, *, available_only: bool = False) -> List[ToolMetadata]:
        """Metadata for all registered tools."""
        return [t.get_metadata() for t in self.tools(available_only=available_only)]

    def names(self) -> List[str]:
        return sorted(self._tools)

    def by_category(self, category: str, *, available_only: bool = False) -> List[BaseTool]:
        return [t for t in self.tools(available_only=available_only) if t.category == category]

    def categories(self) -> Dict[str, int]:
        """Category -> tool count."""
        counts: Dict[str, int] = {}
        for tool in self._tools.values():
            counts[tool.category] = counts.get(tool.category, 0) + 1
        return dict(sorted(counts.items()))

    def search(self, query: str, limit: int = 10) -> List[BaseTool]:
        """Rank tools by a simple substring score over name, aliases and text."""
        needle = (query or "").strip().lower()
        if not needle:
            return []

        scored: list[tuple[int, BaseTool]] = []
        for tool in self._tools.values():
            score = 0
            if needle == tool.name.lower():
                score += 100
            elif needle in tool.name.lower():
                score += 60
            if any(needle == a.lower() for a in tool.aliases):
                score += 80
            elif any(needle in a.lower() for a in tool.aliases):
                score += 40
            if needle in tool.description.lower():
                score += 20
            for word in needle.split():
                if word in tool.description.lower():
                    score += 5
            if score:
                scored.append((score, tool))

        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [tool for _, tool in scored[:limit]]

    def stats(self) -> Dict[str, object]:
        """Summary used by the status endpoint and the UI."""
        all_tools = self.tools()
        available = [t for t in all_tools if t.is_available()]
        return {
            "total": len(all_tools),
            "available": len(available),
            "unavailable": len(all_tools) - len(available),
            "categories": self.categories(),
            "unavailable_tools": {
                t.name: t.unavailable_reason() for t in all_tools if not t.is_available()
            },
        }

    def clear(self) -> None:
        self._tools.clear()
        self._aliases.clear()

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.is_registered(name)


default_tool_registry = ToolRegistry()
