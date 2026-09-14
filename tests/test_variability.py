import math
import sqlite3

import pandas as pd
import pytest

from compute.variability import (
    CHANGE_SCORE_HIGH,
    CHANGE_SCORE_LOW,
    CHANGE_SCORE_MEDIUM,
    CHANGE_SCORE_NONE,
    _validate_state_pair,
    build_variability_report,
    classify_change_score,
    effect_size,
    load_state_pair,
    render_variability_report,
)


def make_run(register_values, slave_id=1):
    sample_count = len(next(iter(register_values.values())))
    columns = {"sample_index": list(range(sample_count)), "slave_id": slave_id}
    for register, values in register_values.items():
        columns[f"register_{register}"] = values
    return pd.DataFrame(columns)


class TestEffectSize:
    def test_zero_delta_is_zero(self):
        assert effect_size(0, 1.0, 1.0) == 0.0

    def test_nan_delta_is_zero(self):
        assert effect_size(float("nan"), 1.0, 1.0) == 0.0

    def test_both_zero_sigma_is_infinite(self):
        assert effect_size(5.0, 0.0, 0.0) == math.inf

    def test_finite_case(self):
        assert effect_size(5.0, 1.0, 1.0) == pytest.approx(5.0)

    def test_nan_sigma_is_nan(self):
        assert math.isnan(effect_size(5.0, float("nan"), 1.0))


class TestClassifyChangeScore:
    def test_zero_delta_is_none(self):
        assert classify_change_score(0, 1.0, 1.0) == CHANGE_SCORE_NONE

    def test_nan_delta_is_none(self):
        assert classify_change_score(float("nan"), 1.0, 1.0) == CHANGE_SCORE_NONE

    def test_nan_sigma_is_none(self):
        assert classify_change_score(5.0, float("nan"), 1.0) == CHANGE_SCORE_NONE

    def test_low(self):
        assert classify_change_score(0.4, 1.0, 1.0) == CHANGE_SCORE_LOW

    def test_medium(self):
        assert classify_change_score(1.0, 1.0, 1.0) == CHANGE_SCORE_MEDIUM

    def test_high(self):
        assert classify_change_score(2.0, 1.0, 1.0) == CHANGE_SCORE_HIGH

    def test_both_zero_sigma_is_high(self):
        assert classify_change_score(5.0, 0.0, 0.0) == CHANGE_SCORE_HIGH


class TestBuildVariabilityReport:
    def test_single_sample_is_not_classified(self):
        air = make_run({0: [100.0]})
        soil = make_run({0: [150.0]})
        report = build_variability_report(air, soil)

        assert len(report) == 1
        row = report.iloc[0]
        assert row["n_air"] == 1
        assert row["n_soil"] == 1
        assert math.isnan(row["d"])
        assert row["change_score"] == CHANGE_SCORE_NONE

    def test_non_numeric_register_column_is_skipped(self):
        air = make_run({0: [1.0, 2.0], "abc": [9, 9]})
        soil = make_run({0: [3.0, 4.0], "abc": [9, 9]})
        report = build_variability_report(air, soil)

        assert list(report["register"]) == [0]

    def test_empty_register_is_included_as_none(self):
        air = make_run({5: [None, None], 0: [1.0, 2.0]})
        soil = make_run({5: [1.0, 2.0], 0: [1.0, 2.0]})
        report = build_variability_report(air, soil)

        assert len(report) == 2
        empty = report[report["register"] == 5].iloc[0]
        assert empty["n_air"] == 0
        assert empty["change_score"] == CHANGE_SCORE_NONE
        assert math.isnan(empty["d"])

    def test_large_delta_is_high(self):
        air = make_run({0: [100.0, 102.0, 104.0]})
        soil = make_run({0: [150.0, 152.0, 154.0]})
        report = build_variability_report(air, soil)

        row = report.iloc[0]
        assert row["mean_air"] == pytest.approx(102.0)
        assert row["mean_soil"] == pytest.approx(152.0)
        assert row["delta"] == pytest.approx(50.0)
        assert row["change_score"] == CHANGE_SCORE_HIGH

    def test_report_sorted_by_effect_descending(self):
        air = make_run({0: [100.0, 102.0, 104.0], 1: [100.0, 101.0, 102.0]})
        soil = make_run({0: [150.0, 152.0, 154.0], 1: [100.0, 101.0, 102.0]})
        report = build_variability_report(air, soil)

        assert list(report["d"]) == sorted(report["d"], reverse=True)


class TestRenderVariabilityReport:
    def test_empty_report_message(self):
        empty = pd.DataFrame()
        assert "No se encontraron" in render_variability_report(empty)

    def test_none_hidden_by_default_and_shown_with_show_all(self):
        report = build_variability_report(
            make_run({0: [100.0, 102.0, 104.0], 1: [1.0, 2.0, 3.0]}),
            make_run({0: [150.0, 152.0, 154.0], 1: [1.0, 2.0, 3.0]}),
        )
        default = render_variability_report(report)
        all_rows = render_variability_report(report, show_all=True)

        assert "Register 1" not in default
        assert "Register 1" in all_rows
        assert "Register 0" in default

    def test_all_none_returns_no_changes_message(self):
        report = build_variability_report(
            make_run({0: [1.0, 2.0, 3.0]}),
            make_run({0: [1.0, 2.0, 3.0]}),
        )
        assert "No se detectaron cambios" in render_variability_report(report)

    def test_top_n_limits_rows(self):
        air = make_run({0: [100.0, 102.0], 1: [100.0, 101.0]})
        soil = make_run({0: [150.0, 152.0], 1: [200.0, 201.0]})
        report = build_variability_report(air, soil)

        assert render_variability_report(report, top_n=1).count("Register") == 1


class TestValidateStatePair:
    def test_air_without_register_columns(self):
        df = pd.DataFrame({"sample_index": [0], "slave_id": [1]})
        with pytest.raises(ValueError, match="air"):
            _validate_state_pair(df, df.copy())

    def test_mismatched_registers(self):
        with pytest.raises(ValueError, match="registros"):
            _validate_state_pair(make_run({0: [1.0]}), make_run({1: [1.0]}))

    def test_mismatched_slaves(self):
        with pytest.raises(ValueError, match="esclavos"):
            _validate_state_pair(
                make_run({0: [1.0]}, slave_id=1), make_run({0: [1.0]}, slave_id=2)
            )


@pytest.fixture
def db_with_runs(tmp_path):
    database_path = tmp_path / "monitoring.sqlite"
    with sqlite3.connect(database_path) as connection:
        make_run({0: [1.0, 2.0]}).to_sql("run_air", connection, index=False)
        make_run({0: [3.0, 4.0]}).to_sql("run_soil", connection, index=False)
    return database_path


class TestLoadStatePair:
    def test_missing_table(self, db_with_runs):
        with pytest.raises(ValueError, match="no existe"):
            load_state_pair(db_with_runs, "run_air", "run_missing")

    def test_happy_path(self, db_with_runs):
        air_df, soil_df = load_state_pair(db_with_runs, "run_air", "run_soil")
        assert len(air_df) == 2
        assert len(soil_df) == 2
