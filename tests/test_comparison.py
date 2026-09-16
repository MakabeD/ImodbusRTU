import pandas as pd
import pytest

from imodbus_rtu.compute.comparison import (
    _alignment_column,
    _validate_comparable_tables,
    build_delta_dataframe,
    build_summary_dataframe,
    compare_dataframes,
    list_monitoring_tables,
)


def run_df(values_by_register, slave_id=1):
    sample_count = len(next(iter(values_by_register.values())))
    columns = {"sample_index": list(range(sample_count)), "slave_id": [slave_id] * sample_count}
    for register, values in values_by_register.items():
        columns[f"register_{register}"] = values
    return pd.DataFrame(columns)


class TestAlignment:
    def test_prefer_sample_index(self):
        assert _alignment_column(run_df({0: [1]}), run_df({0: [1]})) == "sample_index"

    def test_missing_alignment_column_raises(self):
        left = pd.DataFrame({"other": [1]})
        right = pd.DataFrame({"other": [1]})
        with pytest.raises(ValueError, match="alinearse"):
            _alignment_column(left, right)


class TestValidateComparableTables:
    def test_row_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="cantidad de filas"):
            _validate_comparable_tables(run_df({0: [1, 2]}), run_df({0: [1]}))

    def test_register_mismatch_raises(self):
        with pytest.raises(ValueError, match="mismas columnas"):
            _validate_comparable_tables(run_df({0: [1]}), run_df({1: [1]}))

    def test_duplicate_alignment_values_raise(self):
        left = run_df({0: [1, 2]})
        right = run_df({0: [1, 2]}).copy()
        right.loc[1, "sample_index"] = 0
        with pytest.raises(ValueError, match="duplicados"):
            _validate_comparable_tables(left, right)

    def test_slave_mismatch_raises(self):
        with pytest.raises(ValueError, match="esclavos"):
            _validate_comparable_tables(run_df({0: [1]}, slave_id=1), run_df({0: [1]}, slave_id=2))


class TestDeltaAndSummary:
    def test_delta_computation(self):
        left = run_df({0: [100, 200], 1: [10, 20]})
        right = run_df({0: [150, 200], 1: [15, 25]})
        delta = build_delta_dataframe(left, right)

        assert delta["register_0"].tolist() == [50, 0]
        assert delta["register_1"].tolist() == [5, 5]
        assert "sample_index" in delta.columns

    def test_summary_sorts_by_max_abs_delta(self):
        left = run_df({0: [100], 1: [5]})
        right = run_df({0: [160], 1: [6]})
        summary = build_summary_dataframe(left, right)

        assert summary["register"].tolist() == ["register_0", "register_1"]
        row = summary.iloc[0]
        assert row["mean_delta"] == pytest.approx(60.0)
        assert row["max_abs_delta"] == pytest.approx(60.0)
        assert row["changed_samples"] == 1

    def test_summary_with_null_reads(self):
        # Failed reads (NaN) produce NaN deltas which count as unchanged.
        left = run_df({0: [100, None]})
        right = run_df({0: [150, 160]})
        summary = build_summary_dataframe(left, right)

        assert summary["changed_samples"].iloc[0] == 1


class TestCompareDataframes:
    def test_dashboard_contains_tables_and_metadata(self):
        left = run_df({0: [100, 101], 1: [1, 2]})
        right = run_df({0: [200, 201], 1: [1, 2]})
        dashboard = compare_dataframes(
            left_df=left,
            right_df=right,
            left_table="air",
            right_table="soil",
            output_path=None,
        )

        assert "Register Summary" in dashboard.html
        assert "air" in dashboard.html
        assert dashboard.metadata.register_count == 2
        assert dashboard.metadata.sample_count == 2
        assert dashboard.metadata.changed_register_count == 1
        assert dashboard.plots is None

    def test_mismatched_tables_raise(self):
        with pytest.raises(ValueError, match="filas"):
            compare_dataframes(run_df({0: [1]}), run_df({0: [1, 2]}))


class TestListMonitoringTables:
    def test_empty_database_returns_dataframe(self, tmp_path):
        database_path = tmp_path / "monitoring.sqlite"
        # create the schema without runs
        import sqlite3

        from imodbus_rtu.compute.monitoring import ensure_monitoring_schema

        with sqlite3.connect(database_path) as connection:
            ensure_monitoring_schema(connection)

        runs = list_monitoring_tables(database_path)
        assert isinstance(runs, pd.DataFrame)
        assert runs.empty
