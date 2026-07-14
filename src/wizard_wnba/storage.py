from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import threading
from typing import Any, Iterable, Mapping

from .domain import Recommendation


@dataclass(frozen=True)
class SnapshotReference:
    provider: str
    endpoint: str
    captured_at: datetime
    sha256: str
    path: Path
    status_code: int


class RawSnapshotStore:
    """Append-only content-addressed raw response archive."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        *,
        provider: str,
        endpoint: str,
        payload: Any,
        request_parameters: Mapping[str, Any] | None = None,
        response_headers: Mapping[str, str] | None = None,
        status_code: int = 200,
        captured_at: datetime | None = None,
    ) -> SnapshotReference:
        captured = captured_at or datetime.now(UTC)
        envelope = {
            "provider": provider,
            "endpoint": endpoint,
            "captured_at": captured.isoformat(),
            "request_parameters": dict(request_parameters or {}),
            "response_headers": dict(response_headers or {}),
            "status_code": status_code,
            "payload": payload,
        }
        rendered = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        digest = hashlib.sha256(rendered).hexdigest()
        safe_endpoint = endpoint.strip("/").replace("/", "_") or "root"
        directory = (
            self.root
            / f"provider={provider}"
            / f"endpoint={safe_endpoint}"
            / f"date={captured.date().isoformat()}"
        )
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{captured.strftime('%H%M%S.%fZ')}-{digest[:16]}.json"
        path.write_bytes(rendered)
        return SnapshotReference(
            provider=provider,
            endpoint=endpoint,
            captured_at=captured,
            sha256=digest,
            path=path,
            status_code=status_code,
        )


class RecommendationStore:
    """Thread-safe in-memory current view plus append-only JSONL audit history."""

    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._current: dict[str, Recommendation] = {}

    def publish(self, recommendation: Recommendation) -> None:
        record = recommendation.to_dict()
        with self._lock:
            self._current[recommendation.recommendation_id] = recommendation
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")

    def current(self) -> tuple[Recommendation, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._current.values(),
                    key=lambda item: item.conservative_roi,
                    reverse=True,
                )
            )

    def get(self, recommendation_id: str) -> Recommendation | None:
        with self._lock:
            return self._current.get(recommendation_id)

    def remove_expired(self, now: datetime | None = None) -> int:
        threshold = now or datetime.now(UTC)
        with self._lock:
            expired = [
                key
                for key, recommendation in self._current.items()
                if recommendation.expires_at <= threshold
            ]
            for key in expired:
                del self._current[key]
            return len(expired)

    def load_history(self) -> Iterable[dict[str, Any]]:
        if not self.history_path.exists():
            return ()
        records = []
        with self.history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return tuple(records)
