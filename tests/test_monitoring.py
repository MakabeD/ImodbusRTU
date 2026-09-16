import sqlite3

import pandas as pd
import pytest

from imodbus_rtu.compute.monitoring import (
    build_run_dataframe,
    load_run_dataframe,
    sanitize_table_name,
    save_run_dataframe,
    table_exists,
)


class FakeReader:
    def __init__(self, values=None, fail_on=None):
        self.values = values or {}
        self.fail_on = set(fail_on or ())
        self.calls: list[tuple[int, int]] = []

    def __call__(self, slave_id: int, register: int):
        self.calls.append((slave_id, register))
        if register in self.fail_on:
            return None
        return self.values.get(register, register * 100)


class NoSleep:
    """Sleep stub that records durations without waiting."""

    def __init__(self, interrupt_after=None):
        self.durations: list[float] = []
        self.interrupt_after = interrupt_after
        self.calls = 0

    def __call__(self, seconds):
        self.calls += 1
        self.durations.append(seconds)
        if self.interrupt_after is not None and self.calls >= self.interrupt_after:
            raise KeyboardInterrupt


class TestSanitizeTableName:
    def test_basic_name(self):
        assert sanitize_table_name("Grounded Run") == "run_grounded_run"

    def test_strips_dangerous_characters(self):
        assert sanitize_table_name("aire/tierra; DROP") == "run_aire_tierra_drop"

    def test_unicode_normalizes_to_valid_name(self):
        assert sanitize_table_name("Aire-2024") == "run_aire_2024"

    def test_only_invalid_characters_raises(self):
        with pytest.raises(ValueError, match="run_name"):
            sanitize_table_name("///")

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="run_name"):
            sanitize_table_name("   ")


class TestBuildRunDataframe:
    def test_sample_count_and_columns(self):
        reader = FakeReader()
        df = build_run_dataframe(
            run_name="test",
            slave_id=1,
            registers=[0, 2],
            duration_minutes=2,
            reader=reader,
            sample_every_seconds=30,
            started_at=pd.Timestamp("2026-01-01T00:00:00Z").to_pydatetime(),
            sleep_fn=lambda s: None,
        )

        assert len(df) == 4  # 120 s / 30 s
        assert list(df.columns) == [
            "run_name",
            "slave_id",
            "sample_index",
            "sample_timestamp",
            "register_0",
            "register_2",
        ]
        assert df["register_0"].tolist() == [0, 0, 0, 0]
        assert reader.calls[0] == (1, 0)

    def test_invalid_duration_raises(self):
        with pytest.raises(ValueError, match="duration_minutes"):
            build_run_dataframe(
                run_name="t",
                slave_id=1,
                registers=[0],
                duration_minutes=0,
                reader=FakeReader(),
                sleep_fn=lambda s: None,
            )

    def test_invalid_sample_interval_raises(self):
        with pytest.raises(ValueError, match="sample_every_seconds"):
            build_run_dataframe(
                run_name="t",
                slave_id=1,
                registers=[0],
                duration_minutes=1,
                reader=FakeReader(),
                sample_every_seconds=0,
                sleep_fn=lambda s: None,
            )

    def test_empty_registers_raises(self):
        with pytest.raises(ValueError, match="registers"):
            build_run_dataframe(
                run_name="t",
                slave_id=1,
                registers=[],
                duration_minutes=1,
                reader=FakeReader(),
                sleep_fn=lambda s: None,
            )

    def test_failed_reads_are_stored_as_null(self):
        reader = FakeReader(fail_on=[1])
        df = build_run_dataframe(
            run_name="t",
            slave_id=1,
            registers=[0, 1],
            duration_minutes=1,
            reader=reader,
            sample_every_seconds=60,
            sleep_fn=lambda s: None,
        )
        assert df["register_0"].notna().all()
        assert df["register_1"].isna().all()

    def test_keyboard_interrupt_keeps_partial_samples(self):
        reader = FakeReader()
        sleep_fn = NoSleep(interrupt_after=1)  # dies before the 2nd sample
        df = build_run_dataframe(
            run_name="t",
            slave_id=1,
            registers=[0],
            duration_minutes=10,
            reader=reader,
            sample_every_seconds=60,
            sleep_fn=sleep_fn,
        )

        assert len(df) == 1
        assert df["sample_index"].tolist() == [0]

    def test_no_sleep_after_last_sample(self):
        sleep_fn = NoSleep()
        build_run_dataframe(
            run_name="t",
            slave_id=1,
            registers=[0],
            duration_minutes=1,
            reader=FakeReader(),
            sample_every_seconds=60,
            sleep_fn=sleep_fn,
        )
        assert sleep_fn.calls == 0  # single sample: never sleeps


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "monitoring.sqlite"


class TestSaveAndLoadRoundtrip:
    def test_roundtrip(self, database_path):
        df = pd.DataFrame(
            {
                "run_name": ["t", "t"],
                "slave_id": [1, 1],
                "sample_index": [0, 1],
                "register_0": [10, 20],
            }
        )
        run = save_run_dataframe(
            dataframe=df,
            database_path=database_path,
            run_name="test run",
            slave_id=1,
            registers=[0],
            sample_every_seconds=60,
            duration_minutes=2,
        )

        assert run.table_name == "run_test_run"
        assert run.database_path == database_path

        loaded = load_run_dataframe(database_path, "run_test_run")
        assert loaded["register_0"].tolist() == [10, 20]

        with sqlite3.connect(database_path) as connection:
            meta = connection.execute(
                "SELECT run_name, table_name FROM experiment_runs WHERE run_name = 'test run'"
            ).fetchone()
        assert meta == ("test run", "run_test_run")

    def test_load_missing_table_raises_friendly_error(self, database_path):
        with pytest.raises(ValueError, match="no existe"):
            load_run_dataframe(database_path, "run_ghost")

    def test_load_missing_database_raises(self, tmp_path):
        with pytest.raises(ValueError):
            load_run_dataframe(tmp_path / "ghost.sqlite", "run_x")

    def test_table_exists_helper(self, database_path):
        df = pd.DataFrame({"register_0": [1]})
        save_run_dataframe(
            dataframe=df,
            database_path=database_path,
            run_name="t",
            slave_id=1,
            registers=[0],
            sample_every_seconds=60,
            duration_minutes=1,
        )
        with sqlite3.connect(database_path) as connection:
            assert table_exists(connection, "run_t") is True
            assert table_exists(connection, "nope") is False
