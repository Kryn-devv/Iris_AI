"""What IRIS does when a node reports something.

A node pushes readings on its own schedule and shouts immediately when it sees
flame or gas. Two things have to happen with that:

* **telemetry** goes on the event bus, so the UI updates live and a question
  like "is there motion?" is answered from the newest reading instead of a
  round trip to the other side of the world
* **an alert** is acted on at once — spoken out loud, with the robot's face
  showing it — because the whole point of a flame sensor is not having to ask

Alerts are rate-limited per kind. A sensor sitting right on its threshold
flickers, and a flickering flame sensor must not turn into a voice repeating
itself forever.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

from iris.app.core.bus import EventBus, Topics, default_event_bus
from iris.app.core.config import settings
from iris.app.core.logging import get_logger
from iris.app.nodes.link import NodeLink, NodeLinkHub, default_node_hub

logger = get_logger("services.node_events")

#: The same alert kind is announced at most this often.
ALERT_COOLDOWN_S = 90.0

#: What IRIS says, and how the face should look, per alert kind.
ALERT_SPEECH = {
    "flame": ("Fire detected! There is a flame near {node}.", "surprised"),
    "gas": ("Warning — gas detected near {node}.", "surprised"),
    "gas_alarm": ("Warning — gas detected near {node}.", "surprised"),
    "smoke": ("Smoke detected near {node}.", "surprised"),
    "motion": ("Someone is near {node}.", "listening"),
    "obstacle": ("Obstacle ahead.", "suspicious"),
}
DEFAULT_ALERT_SPEECH = ("{node} reported {kind}.", "suspicious")


class NodeEventService:
    """Bridges node telemetry and alerts into the rest of IRIS."""

    def __init__(
        self,
        hub: Optional[NodeLinkHub] = None,
        bus: Optional[EventBus] = None,
    ):
        self._hub = hub or default_node_hub
        self._bus = bus or default_event_bus
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_alert: Dict[str, float] = {}
        self._pending: set[asyncio.Task] = set()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        # Captured because the hub's callbacks are invoked from the socket's
        # task; scheduling work needs a loop reference that is definitely live.
        self._loop = asyncio.get_running_loop()
        self._hub.set_observers(on_telemetry=self._on_telemetry, on_alert=self._on_alert)
        self._started = True
        logger.info("Node event service started.")

    async def stop(self) -> None:
        if not self._started:
            return
        self._hub.set_observers(None, None)
        for task in list(self._pending):
            task.cancel()
        self._pending.clear()
        self._started = False

    # ------------------------------------------------------------- telemetry
    def _on_telemetry(self, link: NodeLink, readings: Dict[str, Any]) -> None:
        self._bus.publish(
            Topics.NODE_TELEMETRY,
            {"node": link.name, "kind": link.kind, "sensors": readings},
        )

    # ----------------------------------------------------------------- alerts
    def _on_alert(self, link: NodeLink, alert: Dict[str, Any]) -> None:
        self._bus.publish(Topics.NODE_ALERT, dict(alert))
        if not settings.NODE_ALERTS_SPOKEN:
            return

        kind = str(alert.get("kind") or "unknown")
        key = f"{link.name}:{kind}"
        now = time.monotonic()
        if now - self._last_alert.get(key, 0.0) < ALERT_COOLDOWN_S:
            return          # a sensor on its threshold flickers; do not repeat
        self._last_alert[key] = now

        template, emotion = ALERT_SPEECH.get(kind, DEFAULT_ALERT_SPEECH)
        sentence = template.format(node=link.name.replace("-", " "), kind=kind)
        self._speak_later(sentence, emotion)

    def _speak_later(self, sentence: str, emotion: str) -> None:
        """Speak without blocking the socket that delivered the alert."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        task = loop.create_task(self._speak(sentence, emotion))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _speak(self, sentence: str, emotion: str) -> None:
        try:
            # Imported here rather than at module scope: the voice service pulls
            # in the whole audio stack, and a headless server with no node
            # attached should not pay for it at import time.
            from iris.app.voice.service import default_voice_service

            await default_voice_service.speak(sentence)
        except Exception as exc:  # noqa: BLE001 - an alert must still be logged
            logger.warning("Could not speak the alert (%s): %s", sentence, exc)
            # speak() is what normally drives the face; announce it directly so
            # the eyes still react on a server with no audio output at all.
            self._bus.publish(
                Topics.VOICE_SPEAKING,
                {"text": sentence, "engine": "silent", "language": "en"},
            )
        try:
            from iris.app.tools.devices.face import push_face
            from iris.app.tools.devices.registry import default_device_registry

            face = default_device_registry.first_of_kind("face")
            if face is not None:
                await push_face(face, emotion=emotion, hold_ms=6000)
        except Exception as exc:  # noqa: BLE001 - no face is normal
            logger.debug("Could not set the alert face: %s", exc)


default_node_event_service = NodeEventService()
