"""L0 data source handbooks. One YAML per source, loaded and validated here."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml

HANDBOOK_DIR = Path(__file__).resolve().parent.parent / "handbooks"


@dataclass
class Handbook:
    key: str
    title: str
    endpoint: str
    auth: str
    licence: str
    spatial: str
    fields: dict[str, str]
    cleaning: list[dict[str, Any]]
    query: dict[str, Any]
    citation: str
    raw: dict[str, Any]

    @property
    def needs_key(self) -> bool:
        return self.auth.lower() not in ("none", "", "public")


def load(key: str) -> Handbook:
    path = HANDBOOK_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no handbook for {key!r} at {path}")
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = {"title", "endpoint", "auth", "licence", "citation"} - set(d)
    if missing:
        raise ValueError(f"handbook {key} missing keys: {sorted(missing)}")
    return Handbook(
        key=key,
        title=d["title"],
        endpoint=d["endpoint"],
        auth=d["auth"],
        licence=d["licence"],
        spatial=d.get("spatial", ""),
        fields=d.get("fields", {}),
        cleaning=d.get("cleaning", []),
        query=d.get("query", {}),
        citation=d["citation"],
        raw=d,
    )


def available() -> list[str]:
    return sorted(p.stem for p in HANDBOOK_DIR.glob("*.yaml"))
