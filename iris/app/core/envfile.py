"""Editing ``.env`` from inside the running app, without losing anything.

Pasting an API key into a settings box beats opening a text editor, but a
program rewriting a human's config file has to be careful about three things:

**Keep what it did not come for.** Comments, ordering, unrelated keys and the
user's own formatting all survive. Only the named keys change, in place, so a
file that was read and understood by a person still reads that way afterwards.

**Never half-write it.** The file is written to a temporary neighbour and then
moved over the original, so a crash mid-write leaves the old file intact rather
than an empty one — losing someone's whole configuration to a failed save would
be a far worse bug than the one this module exists to fix.

**Keep the secrets secret.** The file holds API keys, so it is created 0600
(owner only) and nothing here ever logs a value.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from iris.app.core import paths
from iris.app.core.logging import get_logger

logger = get_logger("core.envfile")

#: A settable line: KEY=value, optionally exported, optionally commented out.
_LINE = re.compile(r"^(?P<lead>\s*)(?P<hash>#\s*)?(?P<export>export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<value>.*)$")

#: Values needing quotes: anything with whitespace, quotes or shell characters.
_NEEDS_QUOTES = re.compile(r"[\s\"'#$`\\]")


#: Marks the block this module appends to, so it is written only once.
_ADDED_HEADER = "# Added by IRIS"


class EnvWriteError(RuntimeError):
    """The file could not be written — the caller should say so, not pretend."""


def target_path() -> Path:
    """The ``.env`` we edit: the first that exists, else the project's own.

    Settings read several candidates; writing to the one already in use avoids
    creating a second file that silently loses to the first.
    """
    existing = paths.existing_env_files()
    if existing:
        return Path(existing[0])
    return paths.project_root() / ".env"


def quote(value: str) -> str:
    """Render a value so the parser reads back exactly what was meant."""
    text = str(value if value is not None else "")
    # A newline would end the assignment and turn the rest into another key —
    # the one way a pasted value could inject configuration of its own.
    text = text.replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return ""
    if _NEEDS_QUOTES.search(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def render(lines: List[str], values: Mapping[str, Optional[str]]) -> List[str]:
    """Apply ``values`` to existing lines, returning the new file.

    A key already present is replaced where it sits — including one that was
    commented out, which is how every key in ``.env.example`` starts life, so
    uncommenting it in place is exactly what a person would do by hand. A key
    that appears nowhere is appended. ``None`` removes a key.
    """
    remaining = dict(values)
    out: List[str] = []

    for line in lines:
        match = _LINE.match(line)
        key = match.group("key") if match else None
        if key is None or key not in remaining:
            out.append(line)
            continue
        new_value = remaining.pop(key)
        if new_value is None:
            continue                       # dropped entirely
        out.append(f"{key}={quote(new_value)}")

    additions = [(k, v) for k, v in remaining.items() if v is not None]
    if additions:
        if out and out[-1].strip():
            out.append("")
        # One header for the whole section, however many times this runs —
        # a file stamped "# Added by IRIS" five times looks like damage.
        if not any(line.strip() == _ADDED_HEADER for line in out):
            out.append(_ADDED_HEADER)
        out.extend(f"{k}={quote(v)}" for k, v in additions)
    return out


def update_env(values: Mapping[str, Optional[str]], path: Optional[Path] = None) -> Path:
    """Set (or remove) keys in ``.env`` and return the file written.

    Raises :class:`EnvWriteError` when the file cannot be written, so a settings
    screen can say "saved for this session only" instead of claiming success.
    """
    destination = Path(path) if path else target_path()
    try:
        existing = destination.read_text(encoding="utf-8").splitlines() if destination.exists() else []
    except OSError as exc:
        raise EnvWriteError(f"Could not read {destination}: {exc}") from exc

    body = "\n".join(render(existing, values)).rstrip("\n") + "\n"

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target so the replace below stays on one
        # filesystem, which is what makes it atomic.
        handle, temp_name = tempfile.mkstemp(
            dir=str(destination.parent), prefix=".env.", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, stat.S_IRUSR | stat.S_IWUSR)   # secrets: owner only
            os.replace(temp_name, destination)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        raise EnvWriteError(f"Could not write {destination}: {exc}") from exc

    logger.info("Updated %s (%s)", destination, ", ".join(sorted(values)))
    return destination


def mask(secret: Optional[str]) -> str:
    """A key as it may safely be shown back: enough to recognise, not to use."""
    text = (secret or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "•" * len(text)
    return f"{text[:4]}{'•' * 6}{text[-4:]}"


def read_values(keys: List[str], path: Optional[Path] = None) -> Dict[str, str]:
    """Current raw values for ``keys`` as they appear in the file."""
    destination = Path(path) if path else target_path()
    found: Dict[str, str] = {}
    try:
        lines = destination.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for line in lines:
        match = _LINE.match(line)
        if not match or match.group("hash") or match.group("key") not in keys:
            continue
        raw = match.group("value").strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        found[match.group("key")] = raw
    return found
