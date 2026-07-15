from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GameEvent:
    event_id: str
    game_id: str
    sequence: int
    event_type: str
    source_timestamp_ms: int
    received_timestamp_ms: int
    payload: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.game_id:
            raise ValueError("game_id is required")
        if self.sequence <= 0:
            raise ValueError("sequence must be positive")
        if self.source_timestamp_ms < 0 or self.received_timestamp_ms < 0:
            raise ValueError("timestamps cannot be negative")
        if self.received_timestamp_ms < self.source_timestamp_ms:
            # Some feeds can report clock skew, but production ingestion should
            # normalize it before events reach the authoritative state engine.
            raise ValueError("received timestamp precedes source timestamp")
