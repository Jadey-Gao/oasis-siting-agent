"""L1 provenance. Every pull emits one record; the report reads from these."""
from __future__ import annotations
import json, hashlib, datetime as dt
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Pull:
    """One retrieval from one source, with everything needed to footnote it."""
    source: str
    endpoint: str
    query: str
    # Which handbook describes this source. The retrieving module knows this for
    # certain, because it loaded that handbook to build the request; saying so
    # here is what stops the report having to recover the pairing by matching one
    # piece of prose against another. It did that until now, and the friction
    # surface fell out of the bibliography because "MAP friction surface" is not a
    # substring of "Malaria Atlas Project motorised friction surface".
    handbook: str = ""
    fetched_at: str = field(default_factory=_now)
    rows_raw: int = 0
    rows_clean: int = 0
    drops: dict[str, int] = field(default_factory=dict)
    licence: str = ""
    note: str = ""
    # Whether the bytes this run used came off the local disk rather than the
    # network, and which file they came from. Recorded as a fact rather than as a
    # note, because it decides whether `fetched_at` above means anything at all.
    from_cache: bool = False
    cache_path: str = ""

    def drop(self, reason: str, n: int) -> None:
        if n:
            self.drops[reason] = self.drops.get(reason, 0) + int(n)

    @property
    def dropped_total(self) -> int:
        return sum(self.drops.values())

    def _anchor(self, path: Path, cached: bool) -> None:
        """Anchor this retrieval to the local file the run actually read.

        `fetched_at` becomes that file's modification time, for a fresh download
        and for a cache hit alike, so the record says when the bytes left the
        source rather than when this process happened to start. One rule for both
        paths, and two consequences that matter. A report stops stamping today's
        date on a file downloaded last week, which is what an `accessed` date in a
        citation is for. And the provenance hash stops moving between a run and
        its `--resume` when nothing about the data has changed, which is what made
        a resumed run's manifest describe a PDF it had not rebuilt.
        """
        p = Path(path)
        self.fetched_at = dt.datetime.fromtimestamp(
            p.stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.from_cache = cached
        self.cache_path = str(p)

    def downloaded(self, path: Path) -> None:
        """This run fetched the file from the source."""
        self._anchor(path, cached=False)

    def served_from_cache(self, path: Path) -> None:
        """This run read a copy already on disk; no request was issued."""
        self._anchor(path, cached=True)

    def footnote(self) -> str:
        """The sentence that appears at the bottom of a report page."""
        s = f"{self.source}, retrieved {self.fetched_at[:10]}"
        if self.from_cache:
            s += " and read from the local cache for this run"
        if self.rows_clean:
            s += f"; {self.rows_clean:,} records after cleaning"
            if self.dropped_total:
                s += f" ({self.dropped_total:,} dropped)"
        if self.licence:
            s += f". Licence: {self.licence}"
        return s + "."

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dropped_total"] = self.dropped_total
        d["footnote"] = self.footnote()
        return d


class Ledger:
    """Collects every Pull in a run. Nothing enters the report unbacked."""

    def __init__(self) -> None:
        self._pulls: list[Pull] = []

    def add(self, pull: Pull) -> Pull:
        self._pulls.append(pull)
        return pull

    def __iter__(self):
        return iter(self._pulls)

    def __len__(self) -> int:
        return len(self._pulls)

    def by_source(self, source: str) -> Pull | None:
        for p in self._pulls:
            if p.source == source:
                return p
        return None

    def to_list(self) -> list[dict]:
        return [p.to_dict() for p in self._pulls]

    def hash(self) -> str:
        blob = json.dumps(self.to_list(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_list(), indent=2), encoding="utf-8")


@dataclass
class Anomaly:
    """Something the agent noticed in a source and had to decide what to do about.

    Recorded rather than silently handled. A run that reports no anomalies on a
    real register is a run that did not look.
    """
    source: str
    kind: str                 # "semantics" | "currency" | "duplication" | "coverage" | "method"
    observed: str             # what the data actually shows, in the data's own terms
    handling: str             # what the agent did, and why
    consequence: str = ""     # what a reader must keep in mind because of it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Notebook:
    """Anomalies found during a run. Read straight into the corrections exhibit."""

    def __init__(self) -> None:
        self._items: list[Anomaly] = []

    def note(self, **kw: Any) -> Anomaly:
        a = Anomaly(**kw)
        self._items.append(a)
        return a

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def to_list(self) -> list[dict]:
        return [a.to_dict() for a in self._items]
