"""Unit tests for the TerminalOutput module."""

from src.models import MatchResult
from src.output import TerminalOutput


class TestTerminalOutput:
    """Tests for TerminalOutput.render()."""

    def setup_method(self):
        self.output = TerminalOutput()

    def _make_result(
        self,
        home="TeamA",
        away="TeamB",
        real_probs=(0.45, 0.28, 0.27),
        lambda_home=1.87,
        mu_away=1.23,
        suggested_score=(1, 0),
        score_probability=0.1234,
        variance_flag=0.0,
        high_margin=0.0,
        low_coverage=0,
    ) -> MatchResult:
        return MatchResult(
            home_team=home,
            away_team=away,
            real_probs=real_probs,
            lambda_home=lambda_home,
            mu_away=mu_away,
            suggested_score=suggested_score,
            score_probability=score_probability,
            variance_flag=variance_flag,
            high_margin=high_margin,
            low_coverage=low_coverage,
        )

    def test_render_empty_list_returns_header_only(self):
        result = self.output.render([])
        lines = result.split("\n")
        assert len(lines) == 1
        assert "Match" in lines[0]
        assert "Real Home Prob %" in lines[0]

    def test_render_header_contains_all_columns_in_order(self):
        result = self.output.render([])
        header = result.split("\n")[0]
        columns = [col.strip() for col in header.split("|")]
        assert columns == TerminalOutput.COLUMNS

    def test_render_single_match(self):
        match = self._make_result()
        result = self.output.render([match])
        lines = result.split("\n")
        assert len(lines) == 2  # header + 1 data row

    def test_match_column_format(self):
        match = self._make_result(home="Arsenal", away="Chelsea")
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[0] == "Arsenal vs Chelsea"

    def test_probability_columns_are_percentages_rounded_to_2dp(self):
        match = self._make_result(real_probs=(0.4523, 0.2801, 0.2676))
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[1] == "45.23"
        assert columns[2] == "28.01"
        assert columns[3] == "26.76"

    def test_lambda_mu_rounded_to_2dp(self):
        match = self._make_result(lambda_home=1.8765, mu_away=1.2345)
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[4] == "1.88"
        assert columns[5] == "1.23"

    def test_suggested_score_format(self):
        match = self._make_result(suggested_score=(2, 1))
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[6] == "2-1"

    def test_score_probability_as_percentage(self):
        match = self._make_result(score_probability=0.1234)
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[7] == "12.34"

    def test_flags_show_zero_when_not_triggered(self):
        match = self._make_result(variance_flag=0.0, high_margin=0.0, low_coverage=0)
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[10] == "0.00"
        assert columns[11] == "0.00"
        assert columns[12] == "0.00"

    def test_flags_show_values_when_triggered(self):
        match = self._make_result(variance_flag=0.045, high_margin=0.12, low_coverage=2)
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]
        assert columns[10] == "0.04"  # variance flag rounded
        assert columns[11] == "0.12"
        assert columns[12] == "2.00"

    def test_render_multiple_matches(self):
        matches = [
            self._make_result(home="TeamA", away="TeamB"),
            self._make_result(home="TeamC", away="TeamD"),
            self._make_result(home="TeamE", away="TeamF"),
        ]
        result = self.output.render(matches)
        lines = result.split("\n")
        assert len(lines) == 4  # header + 3 data rows

    def test_all_values_are_numeric_except_match_and_score(self):
        """Ensure no qualitative text in numeric columns."""
        match = self._make_result(
            real_probs=(0.55, 0.25, 0.20),
            lambda_home=2.10,
            mu_away=0.95,
            suggested_score=(2, 0),
            score_probability=0.0987,
            variance_flag=0.05,
            high_margin=0.15,
            low_coverage=2,
        )
        result = self.output.render([match])
        data_row = result.split("\n")[1]
        columns = [col.strip() for col in data_row.split("|")]

        # Columns 1-5, 7, 9-12 should be purely numeric (parseable as float)
        # Column 8 is Polla Optimal (score format like "1-0" or "-")
        for i in [1, 2, 3, 4, 5, 7, 9, 10, 11, 12]:
            float(columns[i])  # Should not raise ValueError

    def test_pipe_delimiter_used(self):
        match = self._make_result()
        result = self.output.render([match])
        for line in result.split("\n"):
            assert "|" in line

    def test_column_count_consistent(self):
        """Each row should have exactly 13 columns."""
        matches = [
            self._make_result(home="A", away="B"),
            self._make_result(home="C", away="D"),
        ]
        result = self.output.render(matches)
        for line in result.split("\n"):
            columns = [col.strip() for col in line.split("|")]
            assert len(columns) == 13
