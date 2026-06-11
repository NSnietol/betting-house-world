# Requirements Document

## Introduction

Enhanced Goals Extraction extends the existing Kambi/Unibet adapter and LambdaOptimizer to extract richer Over/Under market data (multiple goal lines and team-specific totals), apply a weighted multi-line optimization objective, produce a dual xG estimate with confidence validation, and optionally extract correct score markets for direct prediction validation. The primary goal is maximizing accuracy of score predictions for a Polla Mundialista (World Cup office pool).

## Glossary

- **Adapter**: The UnibetAdapter class that fetches and parses odds from the Kambi Offering API
- **LambdaOptimizer**: The scipy-based optimizer that reverse-engineers expected goals (λ, μ) from market probabilities
- **Overall_O_U_Line**: An Over/Under bet offer for total match goals at a specific threshold (e.g., 0.5, 1.5, 2.5, 3.5, 4.5)
- **Team_O_U_Line**: An Over/Under bet offer for goals scored by a specific team at a threshold (e.g., Home O/U 0.5, 1.5, 2.5)
- **Correct_Score_Market**: A bet offer listing odds for each exact final scoreline (e.g., 1-0, 2-1, 0-0)
- **Primary_Estimate**: The λ/μ pair produced by the full weighted optimizer using all available O/U lines
- **Secondary_Estimate**: The λ/μ pair produced using only team-specific O/U lines as a confidence/validation check
- **Weighted_Objective**: An optimization loss function where each term carries a configurable weight (team-specific lines weighted 2×, overall lines weighted 1×)
- **AggregatedEvent**: The dataclass in src/models.py that holds aggregated odds for a single match across bookmaker sources
- **MarketOutcome**: A dataclass representing a single outcome with name, odds, and optional point value
- **ScrapedMatch**: A dataclass representing standardized match data returned by any adapter

## Requirements

### Requirement 1: Multi-Line Overall Over/Under Extraction

**User Story:** As a prediction system operator, I want the adapter to extract all available overall Over/Under goal lines from the Kambi API, so that the optimizer has more data points to fit λ and μ accurately.

#### Acceptance Criteria

1. WHEN the Adapter fetches bet offers for a match, THE Adapter SHALL extract Overall_O_U_Line outcomes for thresholds 0.5, 1.5, 2.5, 3.5, and 4.5 when available in the Kambi response.
2. WHEN an Overall_O_U_Line is extracted, THE Adapter SHALL produce a MarketOutcome with the name set to "Over" or "Under", the decimal odds value, and the point field set to the exact threshold (e.g., 2.5).
3. IF a specific Overall_O_U_Line threshold is absent from the Kambi response, THEN THE Adapter SHALL skip that threshold without raising an error and continue extracting remaining available lines.
4. THE Adapter SHALL return extracted Overall_O_U_Lines as a list of ScrapedMatch objects with market_type set to "over_under_total".

### Requirement 2: Team-Specific Over/Under Extraction

**User Story:** As a prediction system operator, I want the adapter to extract team-specific Over/Under goal lines for both the home and away teams, so that the optimizer can fit λ and μ independently with higher precision.

#### Acceptance Criteria

1. WHEN the Adapter fetches bet offers for a match, THE Adapter SHALL identify Team_O_U_Line bet offers by matching criterion labels containing "Total Goals" followed by a team name (e.g., "Total Goals by Argentina").
2. WHEN a Team_O_U_Line is extracted, THE Adapter SHALL produce a MarketOutcome with the point field set to the threshold value and a team identifier indicating "home" or "away".
3. THE Adapter SHALL extract Team_O_U_Line outcomes for thresholds 0.5, 1.5, and 2.5 per team when available.
4. IF a Team_O_U_Line criterion label references a team name, THEN THE Adapter SHALL map the team name to "home" or "away" by comparing against the event homeName and awayName fields.
5. THE Adapter SHALL return extracted Team_O_U_Lines as a list of ScrapedMatch objects with market_type set to "over_under_team".

### Requirement 3: Correct Score Market Extraction

**User Story:** As a prediction system operator, I want the adapter to extract correct score market odds, so that predicted score probabilities can be directly validated against bookmaker-implied probabilities.

#### Acceptance Criteria

1. WHEN the Adapter fetches bet offers for a match, THE Adapter SHALL identify Correct_Score_Market bet offers by matching criterion labels containing "Correct Score".
2. WHEN a Correct_Score_Market is found, THE Adapter SHALL extract each scoreline outcome with the home goals, away goals, and corresponding decimal odds.
3. THE Adapter SHALL return extracted Correct_Score_Market data as a ScrapedMatch object with market_type set to "correct_score".
4. IF the Correct_Score_Market bet offer is absent from the Kambi response, THEN THE Adapter SHALL return an empty list for that market type without raising an error.

### Requirement 4: Weighted Multi-Line Optimizer

**User Story:** As a prediction system operator, I want the LambdaOptimizer to accept multiple O/U lines with configurable weights in the objective function, so that the xG estimate benefits from all available market data with team-specific lines contributing more strongly.

#### Acceptance Criteria

1. THE LambdaOptimizer SHALL accept a list of Overall_O_U_Line probability targets, each specified as a tuple of (threshold, probability).
2. THE LambdaOptimizer SHALL accept a list of Team_O_U_Line probability targets for the home team, each specified as a tuple of (threshold, probability).
3. THE LambdaOptimizer SHALL accept a list of Team_O_U_Line probability targets for the away team, each specified as a tuple of (threshold, probability).
4. WHEN computing the objective function, THE LambdaOptimizer SHALL apply a weight of 1.0 to each Overall_O_U_Line squared error term.
5. WHEN computing the objective function, THE LambdaOptimizer SHALL apply a weight of 2.0 to each Team_O_U_Line squared error term.
6. THE LambdaOptimizer SHALL maintain backward compatibility by continuing to accept the existing two-parameter interface (real_probs_1x2, real_prob_over_2_5) for callers that do not provide multi-line data.

### Requirement 5: Dual Estimate Production

**User Story:** As a prediction system operator, I want the optimizer to produce both a primary (full weighted) and secondary (team-totals-only) xG estimate, so that I can assess confidence by comparing the two estimates.

#### Acceptance Criteria

1. WHEN Team_O_U_Line data is available for both home and away teams, THE LambdaOptimizer SHALL produce a Secondary_Estimate using only team-specific O/U line probabilities (weight 2.0) and 1X2 probabilities (weight 1.0).
2. THE LambdaOptimizer SHALL produce a Primary_Estimate using all available market data (Overall_O_U_Lines, Team_O_U_Lines, and 1X2 probabilities) with the Weighted_Objective.
3. WHEN both Primary_Estimate and Secondary_Estimate are produced, THE LambdaOptimizer SHALL return both estimates as a named result containing primary_lambda, primary_mu, secondary_lambda, and secondary_mu.
4. IF Team_O_U_Line data is unavailable for one or both teams, THEN THE LambdaOptimizer SHALL return only the Primary_Estimate and set Secondary_Estimate fields to None.

### Requirement 6: AggregatedEvent Model Extension

**User Story:** As a prediction system operator, I want the AggregatedEvent dataclass to carry richer market data beyond the single O/U 2.5 line, so that downstream pipeline components can access multi-line and team-specific odds.

#### Acceptance Criteria

1. THE AggregatedEvent dataclass SHALL include a field for overall Over/Under lines storing a mapping from threshold (float) to a tuple of (over_odds, under_odds) per bookmaker.
2. THE AggregatedEvent dataclass SHALL include a field for home team Over/Under lines storing a mapping from threshold (float) to a tuple of (over_odds, under_odds) per bookmaker.
3. THE AggregatedEvent dataclass SHALL include a field for away team Over/Under lines storing a mapping from threshold (float) to a tuple of (over_odds, under_odds) per bookmaker.
4. THE AggregatedEvent dataclass SHALL include an optional field for correct score odds storing a mapping from scoreline tuple (int, int) to decimal odds per bookmaker.
5. THE AggregatedEvent dataclass SHALL preserve the existing bookmaker_odds_over_under field for backward compatibility with current pipeline consumers.

### Requirement 7: Confidence Divergence Reporting

**User Story:** As a prediction system operator, I want to see the divergence between the primary and secondary xG estimates, so that I can flag matches where the optimizer may be unreliable.

#### Acceptance Criteria

1. WHEN both Primary_Estimate and Secondary_Estimate are available, THE system SHALL compute the absolute difference between primary_lambda and secondary_lambda and between primary_mu and secondary_mu.
2. WHEN the absolute difference between corresponding λ or μ values exceeds 0.3, THE system SHALL flag the match as having high estimation divergence in the output.
3. THE system SHALL include primary_lambda, primary_mu, secondary_lambda, secondary_mu, and divergence values in the MatchResult output for each processed match.
