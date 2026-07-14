from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    data_dir: Path = Field(default=Path("/data"), alias="DATA_DIR")
    database_url: str = Field(
        default="sqlite:///./wizard_wnba.db",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="", alias="REDIS_URL")
    public_base_url: str = Field(
        default="http://localhost:8080",
        alias="PUBLIC_BASE_URL",
    )

    balldontlie_api_key: str = Field(default="", alias="BALLDONTLIE_API_KEY")
    balldontlie_base_url: str = Field(
        default="https://api.balldontlie.io",
        alias="BALLDONTLIE_BASE_URL",
    )
    the_odds_api_key: str = Field(default="", alias="THE_ODDS_API_KEY")
    the_odds_api_base_url: str = Field(
        default="https://api.the-odds-api.com",
        alias="THE_ODDS_API_BASE_URL",
    )
    the_odds_api_sport_key: str = Field(
        default="basketball_wnba",
        alias="THE_ODDS_API_SPORT_KEY",
    )
    odds_regions: str = Field(default="us", alias="ODDS_REGIONS")
    odds_bookmakers: str = Field(default="", alias="ODDS_BOOKMAKERS")
    odds_markets: str = Field(
        default=(
            "player_points,player_rebounds,player_assists,player_threes,"
            "player_points_rebounds_assists,h2h,spreads,totals"
        ),
        alias="ODDS_MARKETS",
    )

    live_enabled: bool = Field(default=False, alias="LIVE_ENABLED")
    game_discovery_seconds: float = Field(
        default=30.0,
        alias="GAME_DISCOVERY_SECONDS",
    )
    play_poll_seconds: float = Field(default=1.5, alias="PLAY_POLL_SECONDS")
    state_poll_seconds: float = Field(default=2.5, alias="STATE_POLL_SECONDS")
    prop_poll_seconds: float = Field(default=4.0, alias="PROP_POLL_SECONDS")
    espn_reconcile_seconds: float = Field(
        default=8.0,
        alias="ESPN_RECONCILE_SECONDS",
    )
    max_game_state_age_seconds: float = Field(
        default=4.0,
        alias="MAX_GAME_STATE_AGE_SECONDS",
    )
    max_market_age_seconds: float = Field(
        default=12.0,
        alias="MAX_MARKET_AGE_SECONDS",
    )

    policy_mode: str = Field(default="adaptive", alias="POLICY_MODE")
    hard_min_conservative_roi: float = Field(
        default=0.02,
        alias="HARD_MIN_CONSERVATIVE_ROI",
    )
    base_conservative_roi: float = Field(
        default=0.02,
        alias="BASE_CONSERVATIVE_ROI",
    )
    min_book_count: int = Field(default=2, alias="MIN_BOOK_COUNT")
    max_total_uncertainty: float = Field(
        default=0.08,
        alias="MAX_TOTAL_UNCERTAINTY",
    )
    min_calibration_score: float = Field(
        default=0.75,
        alias="MIN_CALIBRATION_SCORE",
    )
    publication_ttl_seconds: int = Field(
        default=20,
        alias="PUBLICATION_TTL_SECONDS",
    )

    default_simulations: int = Field(
        default=20_000,
        alias="DEFAULT_SIMULATIONS",
    )
    high_liability_simulations: int = Field(
        default=50_000,
        alias="HIGH_LIABILITY_SIMULATIONS",
    )
    random_seed: int = Field(default=20260714, alias="RANDOM_SEED")

    @property
    def parsed_markets(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.odds_markets.split(",") if item.strip())

    @property
    def parsed_bookmakers(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.odds_bookmakers.split(",") if item.strip())

    def ensure_runtime_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("raw", "normalized", "recommendations", "models"):
            (self.data_dir / name).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_directories()
    return settings
