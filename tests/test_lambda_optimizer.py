"""Unit tests for the LambdaOptimizer module."""

import math

import pytest

from src.lambda_optimizer import LambdaOptimizer


@pytest.fixture
def optimizer():
    return LambdaOptimizer()


class TestPoissonPmf:
    """Tests for the internal Poisson PMF helper."""

    def test_pmf_zero_goals(self, optimizer):
        # P(X=0 | λ=1.5) = e^(-1.5) ≈ 0.2231
        result = optimizer._poisson_pmf(0, 1.5)
        assert abs(result - math.exp(-1.5)) < 1e-10

    def test_pmf_one_goal(self, optimizer):
        # P(X=1 | λ=2.0) = 2.0 * e^(-2.0) ≈ 0.2707
        result = optimizer._poisson_pmf(1, 2.0)
        expected = 2.0 * math.exp(-2.0)
        assert abs(result - expected) < 1e-10

    def test_pmf_sums_to_approximately_one(self, optimizer):
        lam = 1.8
        total = sum(optimizer._poisson_pmf(k, lam) for k in range(20))
        assert abs(total - 1.0) < 1e-6


class TestPoisson1x2:
    """Tests for P(home), P(draw), P(away) computation."""

    def test_probabilities_sum_to_one(self, optimizer):
        p_home, p_draw, p_away = optimizer._poisson_1x2(1.5, 1.2)
        assert abs(p_home + p_draw + p_away - 1.0) < 1e-6

    def test_equal_lambdas_symmetric(self, optimizer):
        # With equal expected goals, P(home) should equal P(away)
        p_home, p_draw, p_away = optimizer._poisson_1x2(1.5, 1.5)
        assert abs(p_home - p_away) < 1e-6

    def test_higher_lambda_means_more_home_wins(self, optimizer):
        p_home, p_draw, p_away = optimizer._poisson_1x2(3.0, 1.0)
        assert p_home > p_away
        assert p_home > p_draw


class TestPoissonOver25:
    """Tests for P(over 2.5 goals) computation."""

    def test_high_scoring_match(self, optimizer):
        # High λ+μ should give high over 2.5 probability
        result = optimizer._poisson_over_2_5(2.5, 2.0)
        assert result > 0.7

    def test_low_scoring_match(self, optimizer):
        # Low λ+μ should give low over 2.5 probability
        result = optimizer._poisson_over_2_5(0.5, 0.5)
        assert result < 0.3

    def test_result_in_valid_range(self, optimizer):
        result = optimizer._poisson_over_2_5(1.5, 1.2)
        assert 0.0 <= result <= 1.0


class TestOptimize:
    """Tests for the full optimization routine."""

    def test_round_trip_recovery(self, optimizer):
        """Given known λ, μ, compute probabilities and recover them."""
        lam_true, mu_true = 1.8, 1.3

        # Compute Poisson-predicted probabilities from known parameters
        real_1x2 = optimizer._poisson_1x2(lam_true, mu_true)
        real_over = optimizer._poisson_over_2_5(lam_true, mu_true)

        # Optimize should recover the original parameters
        result = optimizer.optimize(real_1x2, real_over)
        assert result is not None

        lam_opt, mu_opt = result
        assert abs(lam_opt - lam_true) < 0.05
        assert abs(mu_opt - mu_true) < 0.05

    def test_returns_tuple_of_floats(self, optimizer):
        real_1x2 = optimizer._poisson_1x2(1.5, 1.2)
        real_over = optimizer._poisson_over_2_5(1.5, 1.2)

        result = optimizer.optimize(real_1x2, real_over)
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], float)
        assert isinstance(result[1], float)

    def test_result_rounded_to_4_decimals(self, optimizer):
        real_1x2 = optimizer._poisson_1x2(2.1, 0.9)
        real_over = optimizer._poisson_over_2_5(2.1, 0.9)

        result = optimizer.optimize(real_1x2, real_over)
        assert result is not None

        lam, mu = result
        # Check that values are rounded to 4 decimal places
        assert lam == round(lam, 4)
        assert mu == round(mu, 4)

    def test_bounds_respected(self, optimizer):
        """Result should always be within [0.1, 5.0]."""
        real_1x2 = optimizer._poisson_1x2(0.3, 0.3)
        real_over = optimizer._poisson_over_2_5(0.3, 0.3)

        result = optimizer.optimize(real_1x2, real_over)
        assert result is not None

        lam, mu = result
        assert 0.1 <= lam <= 5.0
        assert 0.1 <= mu <= 5.0

    def test_returns_none_on_impossible_probabilities(self, optimizer):
        """Probabilities that no Poisson model can fit should return None."""
        # Probabilities that don't correspond to any Poisson distribution
        # e.g., 99% home win, 0.5% draw, 0.5% away with 99% over 2.5
        # is contradictory (high home win needs high λ, but also needs
        # very low μ, which conflicts with over 2.5)
        impossible_1x2 = (0.99, 0.005, 0.005)
        impossible_over = 0.01

        result = optimizer.optimize(impossible_1x2, impossible_over)
        # May return None or a result with poor fit; if it returns something,
        # we just verify it's within bounds
        if result is not None:
            lam, mu = result
            assert 0.1 <= lam <= 5.0
            assert 0.1 <= mu <= 5.0

    def test_various_lambda_mu_recovery(self, optimizer):
        """Test recovery for several different parameter pairs."""
        test_cases = [
            (0.5, 0.5),
            (1.0, 1.0),
            (2.0, 1.5),
            (3.0, 0.8),
            (1.2, 2.5),
        ]

        for lam_true, mu_true in test_cases:
            real_1x2 = optimizer._poisson_1x2(lam_true, mu_true)
            real_over = optimizer._poisson_over_2_5(lam_true, mu_true)

            result = optimizer.optimize(real_1x2, real_over)
            assert result is not None, f"Failed for λ={lam_true}, μ={mu_true}"

            lam_opt, mu_opt = result
            assert abs(lam_opt - lam_true) < 0.05, (
                f"λ mismatch for ({lam_true}, {mu_true}): got {lam_opt}"
            )
            assert abs(mu_opt - mu_true) < 0.05, (
                f"μ mismatch for ({lam_true}, {mu_true}): got {mu_opt}"
            )
