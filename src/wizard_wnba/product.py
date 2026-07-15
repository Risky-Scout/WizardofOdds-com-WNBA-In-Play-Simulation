from __future__ import annotations

BOOKMAKERS: tuple[dict[str, str], ...] = (
    {"key": "draftkings", "title": "DraftKings"},
    {"key": "fanduel", "title": "FanDuel"},
    {"key": "caesars", "title": "Caesars"},
    {"key": "betmgm", "title": "BetMGM"},
    {"key": "betrivers", "title": "BetRivers"},
    {"key": "fanatics", "title": "Fanatics"},
    {"key": "bovada", "title": "Bovada"},
)

MARKETS: tuple[dict[str, str], ...] = (
    {"key": "player_points", "title": "Player points"},
    {"key": "player_rebounds", "title": "Player rebounds"},
    {"key": "player_assists", "title": "Player assists"},
    {"key": "player_threes", "title": "Player threes"},
    {
        "key": "player_points_rebounds_assists",
        "title": "Player points + rebounds + assists",
    },
    {"key": "h2h", "title": "Moneyline"},
    {"key": "spreads", "title": "Point spread"},
    {"key": "totals", "title": "Game total"},
)

PUBLIC_ODDS_FORMAT = "american"
PREFERENCES_STORAGE_KEY = "wizardWnbaPreferences.v2"
