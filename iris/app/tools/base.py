"""Abstract base class for IRIS tools.

Every capability IRIS has — opening an app, typing text, building a deck,
searching the web — is a :class:`BaseTool`. The base class owns four
cross-cutting concerns so individual tools stay small:

* **Timeout & error containment** — a tool can never hang or crash the kernel.
* **Availability** — tools declare the optional dependencies and operating
  systems they need, and report a clear reason when unusable instead of raising
  ``ImportError`` at import time.
* **Blocking work offload** — GUI automation libraries are synchronous, so
  :meth:`BaseTool.to_thread` moves them off the event loop.
* **Result shaping** — results carry a spoken form, a display form and any
  produced file artifacts.
"""

from __future__ import annotations

import asyncio
import functools
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, ClassVar, Sequence, TypeVar

from iris.app.core.logging import get_logger
from iris.app.core.platform_info import CapabilityError, capability, current_os
from iris.app.core.security import PermissionLevel
from iris.app.schemas.tools import (
    ToolCategory,
    ToolExample,
    ToolExecutionResult,
    ToolMetadata,
    ToolParameterSchema,
)

logger = get_logger("tools.base")

T = TypeVar("T")


class ToolError(RuntimeError):
    """Raised by a tool to report a clean, user-facing failure."""

    def __init__(self, message: str, *, speech: str | None = None):
        super().__init__(message)
        self.speech = speech or message


class BaseTool(ABC):
    """Abstract interface for all executable tools in IRIS."""

    #: Unique tool identifier exposed to the model and the REST API.
    name: ClassVar[str] = ""
    #: One-line description; this is what the model reads when choosing a tool.
    description: ClassVar[str] = ""
    #: Risk classification enforced by the permission manager.
    permission_level: ClassVar[PermissionLevel] = PermissionLevel.READ
    #: Grouping used by the UI.
    category: ClassVar[str] = ToolCategory.CORE
    #: Alternative names the deterministic NLU engine may resolve to this tool.
    aliases: ClassVar[Sequence[str]] = ()
    #: Capability identifiers from ``iris.app.core.platform_info``.
    required_capabilities: ClassVar[Sequence[str]] = ()
    #: Supported operating systems ("windows", "linux", "macos"); empty = all.
    os_support: ClassVar[Sequence[str]] = ()
    #: True when the tool reaches the network.
    network: ClassVar[bool] = False
    #: True when the tool changes state on the user's machine.
    mutating: ClassVar[bool] = False
    #: Example utterances shown in the UI and help output.
    examples: ClassVar[Sequence[ToolExample]] = ()

    #: JSON schema for the tool's arguments. Instances may override.
    input_schema: ToolParameterSchema = ToolParameterSchema()

    # --------------------------------------------------------------- lifecycle
    @abstractmethod
    async def _run(self, **kwargs: Any) -> Any:
        """Core execution implementation for the tool."""

    async def execute(self, timeout: float = 20.0, **kwargs: Any) -> ToolExecutionResult:
        """Safely execute the tool with availability, timeout and error guards."""
        start_time = time.perf_counter()

        unavailable = self.unavailable_reason()
        if unavailable:
            logger.info("Tool '%s' is unavailable: %s", self.name, unavailable)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=unavailable,
                speech=f"I can't do that yet — {unavailable}",
                execution_time_seconds=time.perf_counter() - start_time,
            )

        try:
            raw = await asyncio.wait_for(self._run(**kwargs), timeout=timeout)
            return self._shape(raw, time.perf_counter() - start_time)

        except asyncio.TimeoutError:
            elapsed = time.perf_counter() - start_time
            logger.error("Tool '%s' timed out after %.1fs.", self.name, timeout)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=f"Execution timed out after {timeout} seconds.",
                speech="That took too long, so I stopped it.",
                execution_time_seconds=elapsed,
            )
        except asyncio.CancelledError:
            raise
        except ToolError as exc:
            elapsed = time.perf_counter() - start_time
            logger.info("Tool '%s' reported: %s", self.name, exc)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=str(exc),
                speech=exc.speech,
                execution_time_seconds=elapsed,
            )
        except CapabilityError as exc:
            elapsed = time.perf_counter() - start_time
            logger.info("Tool '%s' missing capability: %s", self.name, exc)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=str(exc),
                speech="A required component isn't installed for that.",
                execution_time_seconds=elapsed,
            )
        except TypeError as exc:
            # Almost always a bad argument set produced by a model.
            elapsed = time.perf_counter() - start_time
            logger.warning("Tool '%s' called with invalid arguments: %s", self.name, exc)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=f"Invalid arguments for '{self.name}': {exc}",
                speech="I had the wrong details for that action.",
                execution_time_seconds=elapsed,
            )
        except Exception as exc:  # noqa: BLE001 - tools must never escape
            elapsed = time.perf_counter() - start_time
            logger.error("Tool '%s' execution error: %s", self.name, exc, exc_info=True)
            return ToolExecutionResult(
                tool_name=self.name,
                success=False,
                error=str(exc),
                speech="That didn't work.",
                execution_time_seconds=elapsed,
            )

    def _shape(self, raw: Any, elapsed: float) -> ToolExecutionResult:
        """Normalize whatever ``_run`` returned into a ToolExecutionResult."""
        if isinstance(raw, ToolExecutionResult):
            raw.tool_name = raw.tool_name or self.name
            raw.execution_time_seconds = raw.execution_time_seconds or elapsed
            return raw

        speech: str | None = None
        display: str | None = None
        artifacts: list[str] = []
        ui: dict[str, Any] = {}

        if isinstance(raw, dict):
            speech = raw.get("speech") or raw.get("_speech")
            display = raw.get("display") or raw.get("_display")
            artifacts = list(raw.get("artifacts") or raw.get("_artifacts") or [])
            ui = dict(raw.get("ui") or raw.get("_ui") or {})

        return ToolExecutionResult(
            tool_name=self.name,
            success=True,
            result=raw,
            execution_time_seconds=elapsed,
            speech=speech,
            display=display,
            artifacts=[str(a) for a in artifacts],
            ui=ui,
        )

    # ------------------------------------------------------------ availability
    def supports_current_os(self) -> bool:
        """True when this tool runs on the host operating system."""
        return not self.os_support or current_os() in self.os_support

    def missing_capabilities(self) -> list[str]:
        """Capability names this tool needs that are not present."""
        return [name for name in self.required_capabilities if not capability(name).available]

    def unavailable_reason(self) -> str | None:
        """Human-readable reason the tool cannot run right now, else ``None``."""
        if not self.supports_current_os():
            supported = ", ".join(self.os_support)
            return f"'{self.name}' is only available on {supported} (this machine runs {current_os()})."

        missing = self.missing_capabilities()
        if missing:
            hints = []
            for name in missing:
                cap = capability(name)
                hints.append(f"{name} ({cap.install_hint})" if cap.install_hint else name)
            return f"'{self.name}' needs: {', '.join(hints)}."
        return None

    def is_available(self) -> bool:
        return self.unavailable_reason() is None

    # ----------------------------------------------------------------- helpers
    @staticmethod
    async def to_thread(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run a blocking callable in the default thread pool."""
        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))

    # ---------------------------------------------------------------- metadata
    def get_metadata(self) -> ToolMetadata:
        """Return tool metadata for discovery."""
        reason = self.unavailable_reason()
        return ToolMetadata(
            name=self.name,
            description=self.description,
            permission_level=self.permission_level,
            input_schema=self.input_schema,
            category=self.category,
            aliases=list(self.aliases),
            required_capabilities=list(self.required_capabilities),
            os_support=list(self.os_support),
            available=reason is None,
            unavailable_reason=reason,
            network=self.network,
            mutating=self.mutating,
            examples=list(self.examples),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r} category={self.category!r}>"
