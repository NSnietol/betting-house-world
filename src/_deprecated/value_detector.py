"""Value Bet Detector module for identifying edges across bookmakers.

Compares odds across bookmakers to find where soft bookmakers offer odds
above fair value, calculated from a weighted consensus of sharp and soft
bookmaker implied probabilities.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import AggregatedEvent


SHARP_BOOKMAKERS: set[str] = {"pinnacle", "betfair"}


@dataclass
class ValueBet:
    """A detected value bet where a bookmaker offers odds above fair value.

    Attributes:
        home_team: Home team name.
        away_team: Away team name.
        outcome: Betting outcome label (e.g., "Home", "Draw", "Away",
                 "Over 2.5", "Under 2.5").
        bookmaker_id: Identifier of the bookmaker offering the value.
        offered_odds: Decimal odds offered by the bookmaker.
        fair_odds: Fair decimal odds based on consensus probability.
        consensus_probability: Weighted consensus true probability for this outcome.
        implied_probability: Probability implied by the bookmaker's offered odds.
        edge_pct: Expected edge as percentage: (consensus_prob * offered_odds - 1) * 100.
        kelly_fraction: Optimal bet size as fraction of bankroll using Kelly criterion.
    """

    home_team: str
    away_team: str
    outcome: str
    bookmaker_id: str
    offered_odds: float
    fair_odds: float
    consensus_probability: float
    implied_probability: float
    edge_pct: float
    kelly_fraction: float


class ValueBetDetector:
    """Detects value bets by comparing soft bookmaker odds to consensus fair value.

    Sharp bookmakers (pinnacle, betfair) receive 2x weight in the consensus
    calculation. Value is flagged when a soft bookmaker's implied probability
    is lower than the consensus true probability by more than the configured
    edge threshold.

    Args:
        edge_threshold: Minimum edge (as decimal, e.g. 0.03 for 3%) to flag
                        a value bet. Defaults to 0.03.
    """

    def __init__(self, edge_threshold: float = 0.03) -> None:
        """Initialize ValueBetDetector with the given edge threshold.

        Args:
            edge_threshold: Minimum probability edge to flag as value.
                           Expressed as a decimal (0.03 = 3%).
        """
        self.edge_threshold = edge_threshold

    def detect(self, event: AggregatedEvent) -> list[ValueBet]:
        """Detect value bets for a single aggregated event.

        Calculates weighted consensus probabilities across all bookmakers,
        then checks each soft bookmaker's odds against the consensus to
        identify edges above the threshold.

        Args:
            event: AggregatedEvent with odds from multiple bookmakers.

        Returns:
            List of ValueBet instances for all detected value opportunities.
        """
        value_bets: list[ValueBet] = []

        # Detect 1X2 value bets
        value_bets.extend(self._detect_1x2(event))

        # Detect Over/Under value bets
        value_bets.extend(self._detect_over_under(event))

        return value_bets

    def _detect_1x2(self, event: AggregatedEvent) -> list[ValueBet]:
        """Detect value bets in the 1X2 market.

        Args:
            event: AggregatedEvent with bookmaker_odds_1x2 data.

        Returns:
            List of ValueBet instances for 1X2 market.
        """
        if not event.bookmaker_odds_1x2:
            return []

        # Calculate consensus probabilities for each outcome
        consensus = self._calculate_consensus_1x2(event.bookmaker_odds_1x2)
        if consensus is None:
            return []

        consensus_home, consensus_draw, consensus_away = consensus
        outcomes = [
            ("Home", 0, consensus_home),
            ("Draw", 1, consensus_draw),
            ("Away", 2, consensus_away),
        ]

        value_bets: list[ValueBet] = []

        for bm_id, odds_tuple in event.bookmaker_odds_1x2.items():
            if bm_id in SHARP_BOOKMAKERS:
                continue

            for outcome_name, idx, cons_prob in outcomes:
                offered_odds = odds_tuple[idx]
                implied_prob = 1.0 / offered_odds

                edge = cons_prob - implied_prob

                if edge > self.edge_threshold:
                    fair_odds = 1.0 / cons_prob if cons_prob > 0 else float("inf")
                    edge_pct = (cons_prob * offered_odds - 1.0) * 100.0
                    kelly = self._kelly_fraction(cons_prob, offered_odds)

                    value_bets.append(
                        ValueBet(
                            home_team=event.home_team,
                            away_team=event.away_team,
                            outcome=outcome_name,
                            bookmaker_id=bm_id,
                            offered_odds=offered_odds,
                            fair_odds=fair_odds,
                            consensus_probability=cons_prob,
                            implied_probability=implied_prob,
                            edge_pct=edge_pct,
                            kelly_fraction=kelly,
                        )
                    )

        return value_bets

    def _detect_over_under(self, event: AggregatedEvent) -> list[ValueBet]:
        """Detect value bets in the Over/Under 2.5 market.

        Args:
            event: AggregatedEvent with bookmaker_odds_over_under data.

        Returns:
            List of ValueBet instances for Over/Under market.
        """
        if not event.bookmaker_odds_over_under:
            return []

        consensus = self._calculate_consensus_over_under(
            event.bookmaker_odds_over_under
        )
        if consensus is None:
            return []

        consensus_over, consensus_under = consensus
        outcomes = [
            ("Over 2.5", 0, consensus_over),
            ("Under 2.5", 1, consensus_under),
        ]

        value_bets: list[ValueBet] = []

        for bm_id, odds_tuple in event.bookmaker_odds_over_under.items():
            if bm_id in SHARP_BOOKMAKERS:
                continue

            for outcome_name, idx, cons_prob in outcomes:
                offered_odds = odds_tuple[idx]
                implied_prob = 1.0 / offered_odds

                edge = cons_prob - implied_prob

                if edge > self.edge_threshold:
                    fair_odds = 1.0 / cons_prob if cons_prob > 0 else float("inf")
                    edge_pct = (cons_prob * offered_odds - 1.0) * 100.0
                    kelly = self._kelly_fraction(cons_prob, offered_odds)

                    value_bets.append(
                        ValueBet(
                            home_team=event.home_team,
                            away_team=event.away_team,
                            outcome=outcome_name,
                            bookmaker_id=bm_id,
                            offered_odds=offered_odds,
                            fair_odds=fair_odds,
                            consensus_probability=cons_prob,
                            implied_probability=implied_prob,
                            edge_pct=edge_pct,
                            kelly_fraction=kelly,
                        )
                    )

        return value_bets

    def _calculate_consensus_1x2(
        self, bookmaker_odds: dict[str, tuple[float, float, float]]
    ) -> tuple[float, float, float] | None:
        """Calculate weighted consensus probabilities for 1X2 outcomes.

        Sharp bookmakers receive 2x weight; soft bookmakers receive 1x weight.
        Implied probabilities are normalized per bookmaker before weighting.

        Args:
            bookmaker_odds: Dict mapping bookmaker_id to (home, draw, away) odds.

        Returns:
            Tuple of (home_prob, draw_prob, away_prob) consensus probabilities,
            or None if no valid bookmakers.
        """
        if not bookmaker_odds:
            return None

        weighted_home = 0.0
        weighted_draw = 0.0
        weighted_away = 0.0
        total_weight = 0.0

        for bm_id, odds in bookmaker_odds.items():
            weight = 2.0 if bm_id in SHARP_BOOKMAKERS else 1.0

            # Convert to implied probabilities and normalize
            imp_home = 1.0 / odds[0]
            imp_draw = 1.0 / odds[1]
            imp_away = 1.0 / odds[2]
            imp_total = imp_home + imp_draw + imp_away

            if imp_total <= 0:
                continue

            # Normalize to remove overround
            norm_home = imp_home / imp_total
            norm_draw = imp_draw / imp_total
            norm_away = imp_away / imp_total

            weighted_home += norm_home * weight
            weighted_draw += norm_draw * weight
            weighted_away += norm_away * weight
            total_weight += weight

        if total_weight == 0:
            return None

        cons_home = weighted_home / total_weight
        cons_draw = weighted_draw / total_weight
        cons_away = weighted_away / total_weight

        return (cons_home, cons_draw, cons_away)

    def _calculate_consensus_over_under(
        self, bookmaker_odds: dict[str, tuple[float, float]]
    ) -> tuple[float, float] | None:
        """Calculate weighted consensus probabilities for Over/Under 2.5.

        Sharp bookmakers receive 2x weight; soft bookmakers receive 1x weight.

        Args:
            bookmaker_odds: Dict mapping bookmaker_id to (over, under) odds.

        Returns:
            Tuple of (over_prob, under_prob) consensus probabilities,
            or None if no valid bookmakers.
        """
        if not bookmaker_odds:
            return None

        weighted_over = 0.0
        weighted_under = 0.0
        total_weight = 0.0

        for bm_id, odds in bookmaker_odds.items():
            weight = 2.0 if bm_id in SHARP_BOOKMAKERS else 1.0

            imp_over = 1.0 / odds[0]
            imp_under = 1.0 / odds[1]
            imp_total = imp_over + imp_under

            if imp_total <= 0:
                continue

            norm_over = imp_over / imp_total
            norm_under = imp_under / imp_total

            weighted_over += norm_over * weight
            weighted_under += norm_under * weight
            total_weight += weight

        if total_weight == 0:
            return None

        cons_over = weighted_over / total_weight
        cons_under = weighted_under / total_weight

        return (cons_over, cons_under)

    @staticmethod
    def _kelly_fraction(consensus_prob: float, offered_odds: float) -> float:
        """Calculate the Kelly criterion fraction for optimal bet sizing.

        Formula: kelly = (consensus_prob * offered_odds - 1) / (offered_odds - 1)

        Args:
            consensus_prob: True probability of the outcome.
            offered_odds: Decimal odds offered by the bookmaker.

        Returns:
            Kelly fraction (0 to 1). Returns 0 if edge is non-positive
            or odds equal 1.
        """
        if offered_odds <= 1.0:
            return 0.0

        kelly = (consensus_prob * offered_odds - 1.0) / (offered_odds - 1.0)
        return max(0.0, kelly)
