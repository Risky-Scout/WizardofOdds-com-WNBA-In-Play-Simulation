from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .domain import GameConfig, GameState
from .events import GameEvent
from .state_engine import StateEngine


def write_jsonl(events: Iterable[GameEvent], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")


def read_jsonl(path: str | Path) -> list[GameEvent]:
    events: list[GameEvent] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                events.append(GameEvent(**json.loads(line)))
            except Exception as exc:
                raise ValueError(
                    f"invalid event JSON at line {line_number}"
                ) from exc
    return events


def replay(config: GameConfig, events: Iterable[GameEvent]) -> GameState:
    engine = StateEngine(config)
    for event in events:
        engine.apply(event)
    return engine.state
