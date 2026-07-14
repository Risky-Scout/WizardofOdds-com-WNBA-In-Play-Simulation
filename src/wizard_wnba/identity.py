from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import unicodedata


def normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


@dataclass(frozen=True)
class PlayerCrosswalk:
    canonical_player_id: str
    display_name: str
    team: str
    balldontlie_player_id: str | None = None
    espn_player_id: str | None = None
    odds_api_name: str | None = None
    confidence: float = 1.0
    manual_override: bool = False


class IdentityRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._players: dict[str, PlayerCrosswalk] = {}
        if path.exists():
            self._load()

    def _load(self) -> None:
        records = json.loads(self.path.read_text(encoding="utf-8"))
        self._players = {
            record["canonical_player_id"]: PlayerCrosswalk(**record)
            for record in records
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = [asdict(value) for value in self._players.values()]
        self.path.write_text(
            json.dumps(records, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def upsert(self, crosswalk: PlayerCrosswalk) -> None:
        if not 0 <= crosswalk.confidence <= 1:
            raise ValueError("confidence must be in [0,1]")
        self._players[crosswalk.canonical_player_id] = crosswalk

    def resolve_name(self, name: str, team: str | None = None) -> PlayerCrosswalk | None:
        target = normalize_name(name)
        candidates = [
            item
            for item in self._players.values()
            if target
            in {
                normalize_name(item.display_name),
                normalize_name(item.odds_api_name or ""),
            }
            and (team is None or item.team == team)
        ]
        if len(candidates) == 1 and candidates[0].confidence >= 0.95:
            return candidates[0]
        return None

    def all_players(self) -> tuple[PlayerCrosswalk, ...]:
        return tuple(self._players.values())
