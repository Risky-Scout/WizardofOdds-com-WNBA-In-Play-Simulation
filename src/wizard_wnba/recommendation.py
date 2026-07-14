from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib

from .domain import (
    GameState,
    MarketConsensus,
    MarketOffer,
    ProbabilityEstimate,
    Recommendation,
    RecommendationStatus,
    utc_now,
)
from .odds_math import expected_roi, implied_probability


@dataclass(frozen=True)
class AdaptivePolicy:
    hard_min_conservative_roi: float = 0.02
    base_conservative_roi: float = 0.02
    min_book_count: int = 2
    max_total_uncertainty: float = 0.08
    min_calibration_score: float = 0.75
    max_game_state_age_seconds: float = 4.0
    max_market_age_seconds: float = 12.0
    publication_ttl_seconds: int = 20
    confidence_z: float = 1.645

    def required_roi(
        self,
        *,
        probability: ProbabilityEstimate,
        consensus: MarketConsensus | None,
        game: GameState,
        offer: MarketOffer,
    ) -> float:
        uncertainty_ratio = min(
            probability.total_uncertainty / max(self.max_total_uncertainty, 1e-9),
            2.0,
        )
        market_age_ratio = min(
            offer.age_seconds / max(self.max_market_age_seconds, 1e-9),
            2.0,
        )
        state_age_ratio = min(
            game.age_seconds / max(self.max_game_state_age_seconds, 1e-9),
            2.0,
        )
        book_count = consensus.book_count if consensus else 0
        book_penalty = max(self.min_book_count - book_count, 0) * 0.005
        calibration_penalty = max(
            self.min_calibration_score - probability.calibration_score,
            0.0,
        ) * 0.10

        late_game_penalty = 0.0
        if game.regulation_seconds_remaining <= 120:
            late_game_penalty = 0.01
        elif game.regulation_seconds_remaining <= 300:
            late_game_penalty = 0.005

        adaptive = (
            self.base_conservative_roi
            + 0.015 * uncertainty_ratio
            + 0.006 * market_age_ratio
            + 0.006 * state_age_ratio
            + book_penalty
            + calibration_penalty
            + late_game_penalty
        )
        return max(self.hard_min_conservative_roi, adaptive)

    def evaluate(
        self,
        *,
        game: GameState,
        offer: MarketOffer,
        probability: ProbabilityEstimate,
        consensus: MarketConsensus | None,
    ) -> Recommendation:
        probability.validate()
        reasons: list[str] = []

        if game.sequence_gap:
            reasons.append("EVENT_SEQUENCE_GAP")
        if game.unresolved_review:
            reasons.append("UNRESOLVED_REVIEW")
        if not game.reconciliation_ok:
            reasons.append("STATE_RECONCILIATION_FAILED")
        if game.age_seconds > self.max_game_state_age_seconds:
            reasons.append("STALE_GAME_STATE")
        if offer.age_seconds > self.max_market_age_seconds:
            reasons.append("STALE_MARKET")
        if not offer.available:
            reasons.append("MARKET_UNAVAILABLE")
        if probability.calibration_score < self.min_calibration_score:
            reasons.append("CALIBRATION_BELOW_GATE")
        if probability.total_uncertainty > self.max_total_uncertainty:
            reasons.append("UNCERTAINTY_ABOVE_GATE")
        if not probability.calibrator_id:
            reasons.append("CALIBRATOR_MISSING")

        lower_win = max(
            0.0,
            probability.p_win - self.confidence_z * probability.total_uncertainty,
        )
        upper_loss = min(
            1.0 - probability.p_push,
            probability.p_loss + self.confidence_z * probability.total_uncertainty,
        )
        raw_roi = expected_roi(
            probability.p_win,
            probability.p_push,
            probability.p_loss,
            offer.decimal_odds,
        )
        conservative_roi = (
            lower_win * (offer.decimal_odds - 1.0) - upper_loss
        )

        consensus_probability = (
            consensus.no_vig_probability if consensus else None
        )
        raw_edge = probability.p_win / max(1.0 - probability.p_push, 1e-12)
        raw_edge -= implied_probability(offer.decimal_odds)

        required = self.required_roi(
            probability=probability,
            consensus=consensus,
            game=game,
            offer=offer,
        )

        if reasons:
            status = RecommendationStatus.SUSPENDED
        elif conservative_roi >= required:
            status = RecommendationStatus.PUBLISHED
        elif conservative_roi >= self.hard_min_conservative_roi:
            status = RecommendationStatus.QUALIFIED
        else:
            status = RecommendationStatus.WATCH

        grade = self._grade(
            conservative_roi=conservative_roi,
            required=required,
            calibration_score=probability.calibration_score,
            uncertainty=probability.total_uncertainty,
        )
        generated_at = utc_now()
        fingerprint = (
            f"{offer.offer_id}|{game.event_sequence}|{probability.model_version}|"
            f"{probability.calibrator_id}|{offer.last_update.isoformat()}"
        )
        recommendation_id = hashlib.sha256(fingerprint.encode()).hexdigest()[:20]

        return Recommendation(
            recommendation_id=recommendation_id,
            status=status,
            canonical_game_id=game.canonical_game_id,
            canonical_player_id=offer.canonical_player_id,
            player_name=offer.player_name,
            market_key=offer.market_key,
            side=offer.side,
            line=offer.line,
            bookmaker_key=offer.bookmaker_key,
            bookmaker_title=offer.bookmaker_title,
            decimal_odds=offer.decimal_odds,
            american_odds=offer.american_odds,
            model_probability=probability.p_win,
            fair_decimal_odds=probability.fair_decimal_odds,
            no_vig_consensus_probability=consensus_probability,
            raw_edge=raw_edge,
            expected_roi=raw_roi,
            conservative_roi=conservative_roi,
            required_roi=required,
            confidence_grade=grade,
            projected_mean=probability.projected_mean,
            projected_sd=probability.projected_sd,
            p_push=probability.p_push,
            total_uncertainty=probability.total_uncertainty,
            game_state_age_seconds=game.age_seconds,
            market_age_seconds=offer.age_seconds,
            book_count=consensus.book_count if consensus else 0,
            simulation_count=probability.simulation_count,
            calibrator_id=probability.calibrator_id,
            model_version=probability.model_version,
            event_sequence=game.event_sequence,
            generated_at=generated_at,
            expires_at=generated_at + timedelta(seconds=self.publication_ttl_seconds),
            reasons=tuple(reasons),
            deep_link=offer.deep_link,
        )

    @staticmethod
    def _grade(
        *,
        conservative_roi: float,
        required: float,
        calibration_score: float,
        uncertainty: float,
    ) -> str:
        cushion = conservative_roi - required
        score = (
            50
            + min(max(cushion, -0.10), 0.10) * 250
            + (calibration_score - 0.75) * 80
            - uncertainty * 150
        )
        if score >= 80:
            return "A"
        if score >= 68:
            return "B"
        if score >= 55:
            return "C"
        return "WATCH"
