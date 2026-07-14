from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .domain import Recommendation


@dataclass(frozen=True)
class PublicationSnapshot:
    generated_at: datetime
    environment: str
    engine_status: str
    data_status: str
    recommendations: tuple[dict[str, Any], ...]
    games: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "environment": self.environment,
            "engine_status": self.engine_status,
            "data_status": self.data_status,
            "recommendations": list(self.recommendations),
            "games": list(self.games),
            "metrics": self.metrics,
        }


class AtomicPublicationStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, snapshot: PublicationSnapshot) -> None:
        content = json.dumps(
            snapshot.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        )
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "generated_at": datetime.now(UTC).isoformat(),
                "environment": "unknown",
                "engine_status": "STARTING",
                "data_status": "NO_SNAPSHOT",
                "recommendations": [],
                "games": [],
                "metrics": {},
            }
        return json.loads(self.path.read_text(encoding="utf-8"))


def recommendation_snapshot(
    recommendations: Iterable[Recommendation],
    *,
    environment: str,
    engine_status: str,
    data_status: str,
    games: Iterable[dict[str, Any]],
    extra_metrics: dict[str, Any] | None = None,
) -> PublicationSnapshot:
    items = tuple(
        item.to_dict()
        for item in sorted(
            recommendations,
            key=lambda value: value.conservative_roi,
            reverse=True,
        )
    )
    published = sum(item["status"] == "PUBLISHED" for item in items)
    suspended = sum(item["status"] == "SUSPENDED" for item in items)
    metrics = {
        "recommendation_count": len(items),
        "published_count": published,
        "suspended_count": suspended,
        "hard_min_conservative_roi": 0.02,
    }
    metrics.update(extra_metrics or {})
    return PublicationSnapshot(
        generated_at=datetime.now(UTC),
        environment=environment,
        engine_status=engine_status,
        data_status=data_status,
        recommendations=items,
        games=tuple(games),
        metrics=metrics,
    )
