"""Who the robot has been introduced to, remembered across restarts.

A face embedding is only meaningful to the model that produced it: dlib's
128 numbers and ArcFace's 512 describe the same face in different languages,
and the distance between them is not small — it is meaningless. So every
stored sample records which backend made it, and matching refuses to cross
that line rather than answering with a confident number computed from noise.
That matters in practice: installing a better recogniser later is a normal
thing to do, and it must fail loudly ("re-introduce yourself") instead of
quietly stopping recognising you.

The file is small, human-readable JSON. Someone debugging why the robot has
stopped recognising them should be able to open it and see.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .errors import BackendMismatch, RobotEyeError
from .faces import Match, Vector, best_match, merge_embeddings

STORE_VERSION = 1

#: More samples make recognition steadier across glasses, haircuts and lamps.
#: Past about a dozen the gain flattens while the file and every comparison
#: keep growing, so the oldest fall off the end.
MAX_SAMPLES_PER_PERSON = 12

_MAX_NAME_LENGTH = 48


class UnknownPerson(RobotEyeError):
    """No such person is enrolled."""


def normalize_person_name(name: str) -> str:
    """Canonical form of a person's name: trimmed, single-spaced.

    Case is preserved — the robot says this name out loud, and "prakash" read
    aloud in a transcript is not the same courtesy as "Prakash". Matching is
    case-insensitive; storage is not.
    """
    cleaned = " ".join(str(name or "").strip().split())
    if not cleaned:
        raise ValueError("A person needs a name.")
    if len(cleaned) > _MAX_NAME_LENGTH:
        raise ValueError(f"That name is longer than {_MAX_NAME_LENGTH} characters.")
    # No separate check for newlines or tabs: str.split() above splits on all
    # whitespace, so they have already collapsed into single spaces by here.
    return cleaned


@dataclass
class Person:
    """One enrolled human and the face samples taken of them."""

    name: str
    backend: str
    metric: str
    samples: List[Tuple[float, ...]] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    notes: str = ""
    #: Set when this is the person the robot belongs to, so "who am I" and
    #: "do you know me" can be answered without being told a name.
    owner: bool = False

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    def centroid(self) -> Tuple[float, ...]:
        """One representative vector, for diagnostics and display.

        Matching deliberately does not use this — see :meth:`FaceStore.match`.
        """
        return merge_embeddings(self.samples, self.metric)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "backend": self.backend,
            "metric": self.metric,
            "owner": self.owner,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "samples": [list(sample) for sample in self.samples],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Person":
        samples_raw = data.get("samples") or []
        samples: List[Tuple[float, ...]] = []
        for sample in samples_raw:
            if not isinstance(sample, (list, tuple)) or not sample:
                continue
            try:
                samples.append(tuple(float(v) for v in sample))
            except (TypeError, ValueError):
                continue      # one corrupt sample must not lose the person
        return cls(
            name=normalize_person_name(data["name"]),
            backend=str(data.get("backend") or "unknown"),
            metric=str(data.get("metric") or "euclidean"),
            samples=samples,
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            notes=str(data.get("notes") or ""),
            owner=bool(data.get("owner")),
        )


class FaceStore:
    """The enrolled faces, on disk as JSON."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._people: Dict[str, Person] = {}
        self._load()

    # ----------------------------------------------------------- persistence
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError):
            # A store that will not parse is not worth crashing the assistant
            # over; it is worth not silently overwriting either, so it is left
            # alone on disk and treated as empty in memory.
            return

        for item in raw.get("people", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or "name" not in item:
                continue
            try:
                person = Person.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if person.samples:
                self._people[person.name.lower()] = person

    def save(self) -> None:
        """Write the store out, atomically.

        Written to a temporary file in the same directory and renamed over the
        original: a crash or a full disk half way through leaves the previous
        store intact rather than a truncated file that loses every face.
        """
        payload = {
            "version": STORE_VERSION,
            "people": [person.to_dict() for person in self.people()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=str(self.path.parent),
            prefix=self.path.name, suffix=".tmp", delete=False,
        )
        try:
            with handle as out:
                json.dump(payload, out, indent=2)
                out.flush()
                os.fsync(out.fileno())
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ CRUD
    def people(self) -> List[Person]:
        return sorted(self._people.values(), key=lambda p: p.name.lower())

    def __len__(self) -> int:
        return len(self._people)

    def __iter__(self) -> Iterator[Person]:
        return iter(self.people())

    def get(self, name: str) -> Optional[Person]:
        try:
            key = normalize_person_name(name).lower()
        except ValueError:
            return None
        return self._people.get(key)

    def owner(self) -> Optional[Person]:
        for person in self.people():
            if person.owner:
                return person
        return None

    def enroll(
        self,
        name: str,
        embedding: Vector,
        *,
        backend: str,
        metric: str,
        now: float,
        owner: Optional[bool] = None,
        notes: Optional[str] = None,
    ) -> Person:
        """Add one face sample for someone, creating them if new.

        Enrolling the same person repeatedly is the intended way to use this —
        a few samples in different light is what makes recognition steady.
        """
        clean = normalize_person_name(name)
        key = clean.lower()
        vector = tuple(float(v) for v in embedding)
        if not vector:
            raise ValueError("That is an empty face embedding.")

        person = self._people.get(key)
        if person is None:
            person = Person(
                name=clean, backend=backend, metric=metric,
                created_at=now, updated_at=now,
                owner=bool(owner), notes=notes or "",
            )
            self._people[key] = person
        else:
            if person.backend != backend:
                raise BackendMismatch(
                    f"{person.name} was introduced using the '{person.backend}' recogniser "
                    f"and this is '{backend}'. Faces from the two cannot be compared — "
                    f"forget {person.name} and introduce them again."
                )
            if person.samples and len(person.samples[0]) != len(vector):
                raise BackendMismatch(
                    f"{person.name}'s stored face has {len(person.samples[0])} numbers and this "
                    f"one has {len(vector)}. Forget them and introduce them again."
                )
            person.updated_at = now
            if owner is not None:
                person.owner = bool(owner)
            if notes is not None:
                person.notes = notes

        person.samples.append(vector)
        if len(person.samples) > MAX_SAMPLES_PER_PERSON:
            del person.samples[: len(person.samples) - MAX_SAMPLES_PER_PERSON]

        # Exactly one owner. Naming a new one demotes the old, rather than
        # leaving two people both answering "who am I".
        if person.owner:
            for other in self._people.values():
                if other is not person:
                    other.owner = False

        self.save()
        return person

    def forget(self, name: str) -> bool:
        try:
            key = normalize_person_name(name).lower()
        except ValueError:
            return False
        if self._people.pop(key, None) is None:
            return False
        self.save()
        return True

    def rename(self, old: str, new: str, *, now: float) -> Person:
        person = self.get(old)
        if person is None:
            raise UnknownPerson(f"I don't know anyone called {old}.")
        clean = normalize_person_name(new)
        if clean.lower() != person.name.lower() and clean.lower() in self._people:
            raise ValueError(f"I already know a {clean}.")
        del self._people[person.name.lower()]
        person.name = clean
        person.updated_at = now
        self._people[clean.lower()] = person
        self.save()
        return person

    def set_owner(self, name: str) -> Person:
        person = self.get(name)
        if person is None:
            raise UnknownPerson(f"I don't know anyone called {name}.")
        for other in self._people.values():
            other.owner = other is person
        self.save()
        return person

    def clear(self) -> None:
        self._people.clear()
        self.save()

    # -------------------------------------------------------------- matching
    def match(
        self,
        embedding: Vector,
        *,
        backend: str,
        metric: str,
        threshold: float,
    ) -> Optional[Match]:
        """Closest enrolled person to this face, or ``None``.

        Compared against every stored sample rather than one averaged vector
        per person: samples exist precisely because a face looks different in
        different light and with or without glasses, and averaging them back
        into one point throws away the variety that makes the comparison work.
        """
        candidates: List[Tuple[str, Vector]] = []
        skipped: List[str] = []
        for person in self.people():
            if person.backend != backend:
                skipped.append(person.name)
                continue
            for sample in person.samples:
                candidates.append((person.name, sample))

        if not candidates:
            if skipped:
                raise BackendMismatch(
                    "Everyone I know was introduced with a different recogniser "
                    f"('{'/'.join(sorted({p.backend for p in self.people()}))}' rather than "
                    f"'{backend}'), so I cannot compare. Introduce "
                    f"{'them' if len(skipped) > 1 else skipped[0]} again."
                )
            return None

        return best_match(embedding, candidates, metric, threshold)

    def describe(self) -> Dict[str, Any]:
        """A summary safe to show or speak — counts, never raw embeddings."""
        return {
            "count": len(self._people),
            "people": [
                {
                    "name": person.name,
                    "samples": person.sample_count,
                    "owner": person.owner,
                    "backend": person.backend,
                    "notes": person.notes,
                }
                for person in self.people()
            ],
            "path": str(self.path),
        }
