"""Divergence computation between primary and secondary xG estimates.

Compares the primary (full multi-line) optimization result against
the secondary (team-only) result to flag matches where data sources
disagree significantly.
"""
from __future__ import annotations

from src.models import DivergenceResult, OptimizationResult

DIVERGENCE_THRESHOLD: float = 0.3


def compute_divergence(result: OptimizationResult) -> DivergenceResult | None:
    """Compute divergence between primary and secondary estimates.

    Args:
        result: OptimizationResult containing primary and optionally secondary
            lambda/mu estimates.

    Returns:
        DivergenceResult with divergence metrics, or None if secondary
        estimates are not available.
    """
    if result.secondary_lambda is None or result.secondary_mu is None:
        return None
    lambda_div = abs(result.primary_lambda - result.secondary_lambda)
    mu_div = abs(result.primary_mu - result.secondary_mu)
    is_high = lambda_div > DIVERGENCE_THRESHOLD or mu_div > DIVERGENCE_THRESHOLD
    return DivergenceResult(
        lambda_divergence=lambda_div,
        mu_divergence=mu_div,
        is_high_divergence=is_high,
    )
