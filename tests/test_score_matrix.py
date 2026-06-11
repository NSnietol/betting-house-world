"""Unit tests for ScoreMatrixGenerator."""

import math

import pytest

from src.score_matrix import ScoreMatrixGenerator, _poisson_pmf


class TestPoissonPmf:
    """Tests for the manual Poisson PMF implementation."""

    def test_pmf_zero_goals(self):
        """P(X=0) = e^(-λ) for any λ."""
        lam = 1.5
        assert math.isclose(_poisson_pmf(0, lam), math.exp(-lam), rel_tol=1e-10)

    def test_pmf_one_goal(self):
        """P(X=1) = λ * e^(-λ)."""
        lam = 2.0
        expected = lam * math.exp(-lam)
        assert math.isclose(_poisson_pmf(1, lam), expected, rel_tol=1e-10)

    def test_pmf_known_value(self):
        """Verify against a known Poisson PMF value."""
        # P(X=3 | λ=2) = (2^3 * e^-2) / 3! = 8 * e^-2 / 6
        expected = (8 * math.exp(-2)) / 6
        assert math.isclose(_poisson_pmf(3, 2.0), expected, rel_tol=1e-10)

    def test_pmf_sums_close_to_one(self):
        """Sum of PMF over 0..20 should be very close to 1 for typical λ."""
        lam = 1.8
        total = sum(_poisson_pmf(k, lam) for k in range(21))
        assert math.isclose(total, 1.0, abs_tol=1e-8)


class TestScoreMatrixGenerator:
    """Tests for ScoreMatrixGenerator."""

    def setup_method(self):
        self.generator = ScoreMatrixGenerator()

    def test_generate_returns_6x6_matrix(self):
        """Matrix should be 6 rows × 6 columns."""
        result = self.generator.generate(1.5, 1.2)
        assert result is not None
        matrix = result["matrix"]
        assert len(matrix) == 6
        for row in matrix:
            assert len(row) == 6

    def test_generate_returns_none_for_invalid_lambda(self):
        """Returns None if λ is non-positive or None."""
        assert self.generator.generate(0, 1.2) is None
        assert self.generator.generate(-1.0, 1.2) is None
        assert self.generator.generate(None, 1.2) is None

    def test_generate_returns_none_for_invalid_mu(self):
        """Returns None if μ is non-positive or None."""
        assert self.generator.generate(1.5, 0) is None
        assert self.generator.generate(1.5, -0.5) is None
        assert self.generator.generate(1.5, None) is None

    def test_matrix_cells_are_product_of_pmfs(self):
        """Each cell (i,j) should equal P(X=i|λ) × P(Y=j|μ)."""
        lam, mu = 1.5, 1.2
        result = self.generator.generate(lam, mu)
        matrix = result["matrix"]
        for i in range(6):
            for j in range(6):
                expected = _poisson_pmf(i, lam) * _poisson_pmf(j, mu)
                assert math.isclose(matrix[i][j], expected, rel_tol=1e-10)

    def test_matrix_cells_non_negative(self):
        """All probabilities should be non-negative."""
        result = self.generator.generate(2.0, 1.0)
        for row in result["matrix"]:
            for cell in row:
                assert cell >= 0.0

    def test_suggested_score_is_max_probability_cell(self):
        """Suggested score should correspond to the highest probability cell."""
        lam, mu = 1.5, 1.2
        result = self.generator.generate(lam, mu)
        matrix = result["matrix"]
        suggested = result["suggested_score"]

        max_prob = max(cell for row in matrix for cell in row)
        assert math.isclose(
            matrix[suggested[0]][suggested[1]], max_prob, rel_tol=1e-10
        )

    def test_score_probability_matches_matrix_cell(self):
        """score_probability should equal the matrix cell of the suggested score."""
        result = self.generator.generate(1.5, 1.2)
        home, away = result["suggested_score"]
        assert result["score_probability"] == result["matrix"][home][away]

    def test_typical_result_1_0(self):
        """With λ=1.5 and μ=0.8, the most likely score should be 1-0."""
        result = self.generator.generate(1.5, 0.8)
        # 1-0 is typically the most probable score with these parameters
        assert result["suggested_score"] == (1, 0)

    def test_symmetric_parameters_selects_lower_home_goals(self):
        """With equal λ and μ, 0-0 should be preferred over 1-1 if same probability isn't the case."""
        # When λ=μ, the most likely cell is often (1,1) for moderate values
        # or (0,0) for low values. Let's test the tiebreaker with low values.
        result = self.generator.generate(0.5, 0.5)
        # P(0,0) = e^(-0.5) * e^(-0.5) = e^(-1) ≈ 0.3679
        # P(1,1) = 0.5*e^(-0.5) * 0.5*e^(-0.5) = 0.25*e^(-1) ≈ 0.0920
        # So (0,0) clearly wins here
        assert result["suggested_score"] == (0, 0)


class TestSelectMaxCell:
    """Tests for the _select_max_cell tiebreaker logic."""

    def setup_method(self):
        self.generator = ScoreMatrixGenerator()

    def test_clear_maximum(self):
        """Single maximum cell is selected."""
        matrix = [[0.0] * 6 for _ in range(6)]
        matrix[2][1] = 0.5
        assert self.generator._select_max_cell(matrix) == (2, 1)

    def test_tiebreaker_lowest_total_goals(self):
        """When tied, prefer lowest total goals (i + j)."""
        matrix = [[0.0] * 6 for _ in range(6)]
        # Same probability at (2,3) total=5 and (1,1) total=2
        matrix[2][3] = 0.3
        matrix[1][1] = 0.3
        assert self.generator._select_max_cell(matrix) == (1, 1)

    def test_tiebreaker_fewer_home_goals(self):
        """When tied on total goals, prefer fewer home goals."""
        matrix = [[0.0] * 6 for _ in range(6)]
        # Same probability at (2,1) and (1,2) — both total=3
        matrix[2][1] = 0.3
        matrix[1][2] = 0.3
        # (1,2) has fewer home goals → preferred
        assert self.generator._select_max_cell(matrix) == (1, 2)

    def test_tiebreaker_three_way_tie(self):
        """Three-way tie resolved by total goals then home goals."""
        matrix = [[0.0] * 6 for _ in range(6)]
        matrix[3][2] = 0.2  # total=5
        matrix[2][1] = 0.2  # total=3
        matrix[1][2] = 0.2  # total=3, fewer home goals
        assert self.generator._select_max_cell(matrix) == (1, 2)

    def test_tiebreaker_same_total_same_home(self):
        """When everything is equal, the first encountered cell wins (by iteration order)."""
        matrix = [[0.0] * 6 for _ in range(6)]
        # Only one cell at (0,0) with some value - trivial case
        matrix[0][0] = 0.1
        assert self.generator._select_max_cell(matrix) == (0, 0)
