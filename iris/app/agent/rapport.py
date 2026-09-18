"""What Iris knows about the shape of the conversation she is in.

Two different things make an assistant feel like it is *present*, and neither
is the model:

**Situation.** A person answers the fourth command in twenty seconds
differently from the first thing you have said to them in an hour. They notice
when you ask for the same thing twice. They know it is 2am. None of that needs
intelligence — it needs a clock and a short memory, which is all this module
is. :class:`Rapport` hands those facts to :class:`~iris.app.agent.persona.Voice`
so the wording moves with the moment.

**Continuity.** Only the last dozen exchanges ride along on a model call —
more than that burns tokens and, on a free tier, trips the rate limit. So
everything older is folded into one rolling line of summary, refreshed in the
background and handed to the model as something she simply knows. That is the
difference between remembering this conversation and remembering the last four
things said in it.

State lives in memory, keyed by conversation, bounded, and is never load-
bearing: if any of it is missing the assistant still works, it just sounds a
little flatter.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, List, Optional

from iris.app.core.config import settings
from iris.app.core.logging import get_logger

logger = get_logger("agent.rapport")

#: Conversations tracked at once. Old ones fall off the end.
MAX_CONVERSATIONS = 64
#: Tool names kept per conversation, for "again?" and for the model's context.
RECENT_TOOLS = 8
#: A burst is this many commands inside this many seconds.
BURST_COMMANDS = 3
BURST_WINDOW_S = 75.0

SUMMARY_PROMPT = (
    "Below is the earlier part of a conversation between an assistant and the "
    "person it works for. In at most 45 words, note only what the assistant "
    "would need to remember later: what they were doing, decisions made, facts "
    "about them, anything unresolved. Write it as plain notes, no preamble.\n\n"
    "{transcript}"
)


@dataclass
class Moment:
    """The situation one reply is landing in."""

    #: Nothing has been said for a long while (or this is the first thing).
    returning: bool = False
    #: The same tool as the turn immediately before.
    repeated: bool = False
    #: Commands coming fast — match the pace and get out of the way.
    terse: bool = False
    #: Minutes since the last exchange, ``None`` on the first.
    gap_minutes: Optional[float] = None
    #: Minutes this conversation has been going.
    session_minutes: float = 0.0
    #: Local hour, 0-23.
    hour: int = 12
    turns: int = 0


@dataclass
class _Thread:
    """Everything remembered about one conversation."""

    started_at: float
    last_at: float
    turns: int = 0
    tools: Deque[str] = field(default_factory=lambda: deque(maxlen=RECENT_TOOLS))
    #: Monotonic stamps of recent command turns, for burst detection.
    command_stamps: Deque[float] = field(default_factory=lambda: deque(maxlen=BURST_COMMANDS))
    #: Rolling notes about everything older than the live history window.
    summary: str = ""
    #: How many exchanges the summary already covers, so work is not redone.
    summarized_upto: int = 0
    summarizing: bool = False


class RapportTracker:
    """Per-conversation situation and continuity."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall_clock: Optional[Callable[[], Any]] = None):
        self._clock = clock
        self._wall = wall_clock or _local_hour
        self._threads: "OrderedDict[str, _Thread]" = OrderedDict()
        self._tasks: set = set()

    # ------------------------------------------------------------- reading
    def moment(self, conversation_id: Optional[str]) -> Moment:
        """How the next reply should feel, given what has happened so far."""
        now = self._clock()
        hour = self._wall()
        thread = self._threads.get(conversation_id) if conversation_id else None
        if thread is None:
            # First contact, not a reunion. Greeting someone who has said
            # nothing yet with "welcome back" is a stranger claiming to know
            # them; the model's own opening line handles hello.
            return Moment(returning=False, hour=hour, turns=0)

        gap_s = now - thread.last_at
        gap_minutes = gap_s / 60.0
        returning = gap_minutes >= float(getattr(settings, "RAPPORT_RETURN_GAP_MIN", 25))

        stamps = [s for s in thread.command_stamps if now - s <= BURST_WINDOW_S]
        terse = len(stamps) >= BURST_COMMANDS and not returning

        return Moment(
            returning=returning,
            repeated=False,      # filled in by note_turn's caller via repeats()
            terse=terse,
            gap_minutes=gap_minutes,
            session_minutes=(now - thread.started_at) / 60.0,
            hour=hour,
            turns=thread.turns,
        )

    def repeats(self, conversation_id: Optional[str], tool_name: Optional[str]) -> bool:
        """True when this tool is the same one as the previous turn."""
        thread = self._threads.get(conversation_id) if conversation_id else None
        if thread is None or not tool_name or not thread.tools:
            return False
        return thread.tools[-1] == tool_name

    # ------------------------------------------------------------- writing
    def note_turn(
        self,
        conversation_id: Optional[str],
        *,
        tool_name: Optional[str] = None,
        was_command: bool = False,
    ) -> None:
        """Record that an exchange just happened.

        A turn with no conversation id is not tracked at all. Scheduled
        commands and the robot's own microphone arrive without one, and
        bucketing them together made a background timer firing look like the
        person asking for the same thing twice — "Again —" about a tool they
        never ran, terser answers they did not earn, and a three-hour absence
        that never registered because a cron job kept the thread warm.
        """
        if not conversation_id:
            return
        key = conversation_id
        now = self._clock()
        thread = self._threads.get(key)
        if thread is None:
            thread = _Thread(started_at=now, last_at=now)
            self._threads[key] = thread
            while len(self._threads) > MAX_CONVERSATIONS:
                self._threads.popitem(last=False)
        else:
            self._threads.move_to_end(key)
        thread.turns += 1
        thread.last_at = now
        if tool_name:
            thread.tools.append(tool_name)
        if was_command:
            thread.command_stamps.append(now)

    def forget(self, conversation_id: Optional[str]) -> None:
        if conversation_id:
            self._threads.pop(conversation_id, None)

    def clear(self) -> None:
        self._threads.clear()

    # ------------------------------------------------------------- context
    def context_block(self, conversation_id: Optional[str]) -> str:
        """What the model should know about the shape of this conversation.

        Deliberately terse and free of instructions — it is background
        awareness, not an order. The character brief already says what to do
        with it.
        """
        thread = self._threads.get(conversation_id) if conversation_id else None
        if thread is None:
            return ""
        lines: List[str] = []
        if thread.summary:
            lines.append(f"Earlier in this conversation: {thread.summary}")

        gap_minutes = (self._clock() - thread.last_at) / 60.0
        if gap_minutes >= float(getattr(settings, "RAPPORT_RETURN_GAP_MIN", 25)):
            # Stamped here, not only in note_turn. Several reply paths — a
            # confirmation prompt, a rejected confirmation, a timeout — never
            # reach note_turn, so last_at stayed three hours old and she said
            # "you've been away, say hello properly" on every single turn after
            # that. Greeting someone once is warm; greeting them every sentence
            # is a fault.
            thread.last_at = self._clock()
            # Stated as the situation, not an order — but stated plainly enough
            # that the model does the obvious thing with it. "They have been
            # away 40 minutes" on its own got answered with the same flat
            # "Sure, opening YouTube" as always; a person who has not seen you
            # since lunch says something about that first.
            lines.append(
                f"They have been away about {_duration(gap_minutes)} — this is "
                "the first thing said since, so it is your turn to say hello "
                "properly and ask how they are."
            )

        session_minutes = (self._clock() - thread.started_at) / 60.0
        if session_minutes >= 90:
            lines.append(
                f"You have been at this together for {_duration(session_minutes)} — "
                "worth noticing if they sound tired."
            )
        if thread.tools:
            recent = ", ".join(dict.fromkeys(list(thread.tools)[-4:]))
            lines.append(f"Recently used: {recent}.")
        return "\n".join(lines)

    # ------------------------------------------------------------ summary
    def maybe_summarize(
        self,
        conversation_id: Optional[str],
        history: List[dict],
        gateway: Any,
    ) -> None:
        """Fold everything older than the live window into the rolling notes.

        Fire-and-forget on purpose: this must never make a reply wait. The
        result lands in time for the *next* turn, which is soon enough for
        something that is only about an hour ago.
        """
        if not getattr(settings, "ROLLING_SUMMARY_ENABLED", True) or gateway is None:
            return
        # The offline engine answers anything, including this. Its canned reply
        # ("Plan created for intent 'system_info'…") stored as conversation
        # memory would then be handed to the *next* real model as fact — the
        # one thing the character brief forbids outright. A free tier that
        # rate-limits mid-conversation makes this the common case, not a corner.
        if not _cloud_is_usable(gateway):
            return
        key = conversation_id or ""
        thread = self._threads.get(key) if conversation_id else None
        if thread is None or thread.summarizing:
            return

        window = 2 * max(1, int(getattr(settings, "HISTORY_MAX_TURNS", 12)))
        older = history[: max(0, len(history) - window)]
        # A history that shrank (cleared, or a different conversation reusing
        # the id) would otherwise stall summarizing forever against a high-water
        # mark that can no longer be reached.
        if len(older) < thread.summarized_upto:
            thread.summarized_upto = 0
        # Only worth a call once a real chunk has aged out since last time.
        if len(older) < thread.summarized_upto + 6:
            return

        # Look for the loop before building the coroutine: a synchronous caller
        # would otherwise leave an un-awaited coroutine behind, and latching
        # ``summarizing`` shut here would mean never remembering anything older
        # than the window again this session.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            logger.debug("Rolling summary not scheduled: %s", exc)
            return
        thread.summarizing = True
        task = loop.create_task(self._summarize(key, older, gateway))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _summarize(self, key: str, older: List[dict], gateway: Any) -> None:
        thread = self._threads.get(key)
        try:
            transcript = _transcript(older, limit=60)
            if not transcript.strip():
                return
            # Feed the last notes back in so the summary genuinely rolls: only
            # the most recent slice of the transcript is carried each time, and
            # without this, anything older than that slice would be forgotten
            # the moment it fell out.
            previous = (thread.summary if thread is not None else "") or ""
            prompt = SUMMARY_PROMPT.format(transcript=transcript)
            if previous:
                prompt = (
                    f"Notes you already wrote about the start of this "
                    f"conversation:\n{previous}\n\n{prompt}\n\n"
                    "Merge both into one set of notes, still at most 45 words."
                )
            response = await asyncio.wait_for(
                gateway.generate(
                    prompt,
                    system_prompt="You write terse, factual notes. No preamble, no lists.",
                    max_tokens=120,
                    temperature=0.2,
                ),
                timeout=float(getattr(settings, "ROLLING_SUMMARY_TIMEOUT_S", 25)),
            )
            # Belt and braces: the chain can fall back to the offline engine
            # between the check above and the answer arriving.
            if (getattr(response, "provider_name", "") or "").lower() == "mock":
                logger.debug("Rolling summary discarded: answered by the offline engine")
                return
            notes = (getattr(response, "content", "") or "").strip()
            if notes and thread is not None:
                thread.summary = notes[:600]
                thread.summarized_upto = len(older)
                logger.debug("Rolling summary refreshed for %s (%d msgs)", key, len(older))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - memory is a nicety, never a failure
            logger.debug("Rolling summary skipped: %s", exc)
        finally:
            if thread is not None:
                thread.summarizing = False


def _cloud_is_usable(gateway: Any) -> bool:
    """True when a real model would answer, rather than the offline stand-in."""
    try:
        return bool(gateway.can_answer)
    except Exception:  # noqa: BLE001 - a gateway that cannot answer that is not usable
        return False


def _transcript(messages: List[dict], limit: int = 60) -> str:
    """Flatten history into something a model can summarize."""
    lines: List[str] = []
    for message in messages[-limit:]:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        lines.append(f"{'Them' if role == 'user' else 'You'}: {text[:400]}")
    return "\n".join(lines)


def _duration(minutes: float) -> str:
    """Minutes as a person would say them."""
    if minutes < 90:
        return f"{int(round(minutes))} minutes"
    hours = minutes / 60.0
    if hours < 24:
        rounded = round(hours * 2) / 2
        text = f"{rounded:g}"
        return f"{text} hour{'s' if rounded != 1 else ''}"
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''}"


def _local_hour() -> int:
    import datetime

    return datetime.datetime.now().hour


#: Process-wide tracker. Tests build their own with a fake clock.
default_rapport = RapportTracker()
