"""Tool discovery and registration.

Imports every tool module, calls its ``get_tools()`` factory, and registers the
results. Each module is imported inside its own try/except: one broken or
half-installed tool module must never prevent IRIS from booting — it is logged
and skipped instead.
"""

from __future__ import annotations

import importlib
from typing import Iterable

from iris.app.core.logging import get_logger
from iris.app.tools.base import BaseTool
from iris.app.tools.registry import ToolRegistry

logger = get_logger("tools.loader")

#: Modules exposing ``get_tools() -> list[BaseTool]``.
TOOL_MODULES: tuple[str, ...] = (
    # Desktop automation
    "iris.app.tools.desktop.apps",
    "iris.app.tools.desktop.websites",
    "iris.app.tools.desktop.input_control",
    "iris.app.tools.desktop.clipboard",
    "iris.app.tools.desktop.windows_mgmt",
    "iris.app.tools.desktop.screenshot",
    "iris.app.tools.desktop.notify",
    "iris.app.tools.desktop.media",
    "iris.app.tools.desktop.power",
    # Web & knowledge
    "iris.app.tools.web.search",
    "iris.app.tools.web.fetch",
    "iris.app.tools.web.wiki",
    "iris.app.tools.web.weather",
    "iris.app.tools.web.news",
    # Content creation
    "iris.app.tools.content.presentation",
    "iris.app.tools.content.documents",
    "iris.app.tools.content.spreadsheet",
    "iris.app.tools.content.code_writer",
    # Files
    "iris.app.tools.files.file_manager",
    # System
    "iris.app.tools.system.processes",
    "iris.app.tools.system.shell",
    "iris.app.tools.system.network",
    # Automation
    "iris.app.tools.automation.reminders",
    # Desktop shell
    "iris.app.desktop.tools",
    # Voice
    "iris.app.voice.tools",
)

#: Legacy builtin modules exposing tool classes directly.
LEGACY_BUILTIN_CLASSES: tuple[tuple[str, str], ...] = (
    ("iris.app.tools.builtin.calculator", "CalculatorTool"),
    ("iris.app.tools.builtin.system_info", "SystemInfoTool"),
    ("iris.app.tools.builtin.time", "TimeTool"),
    ("iris.app.tools.builtin.string_utils", "StringUtilsTool"),
    ("iris.app.tools.builtin.unit_converter", "UnitConverterTool"),
)


def load_all_tools(registry: ToolRegistry, *, quiet: bool = False) -> int:
    """Import and register every known tool. Returns the number registered."""
    count = 0

    for module_name, class_name in LEGACY_BUILTIN_CLASSES:
        try:
            module = importlib.import_module(module_name)
            tool_cls = getattr(module, class_name)
            registry.register(tool_cls(), quiet=quiet)
            count += 1
        except Exception as exc:  # noqa: BLE001 - boot must survive a bad tool
            logger.error("Failed to load builtin %s.%s: %s", module_name, class_name, exc)

    for module_name in TOOL_MODULES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to import tool module %s: %s", module_name, exc)
            continue
        factory = getattr(module, "get_tools", None)
        if factory is None:
            logger.warning("Tool module %s has no get_tools(); skipped.", module_name)
            continue
        try:
            tools: Iterable[BaseTool] = factory()
        except Exception as exc:  # noqa: BLE001
            logger.error("get_tools() failed in %s: %s", module_name, exc)
            continue
        count += registry.register_many(tools, quiet=quiet)

    logger.info("Tool loading complete: %d tools registered.", count)
    return count
