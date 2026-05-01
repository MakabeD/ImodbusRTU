from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from typing import Iterable

import pandas as pd

from compute.modbus_compute import MasterModbusCompute


RegisterReader = Callable[[int, int], int | None]


@dataclass(frozen=True)
class MonitoringRun:
    run_name: str
    table_name: str
    dataframe: pd.DataFrame
    database_path: Path


def sanitize_table_name(run_name: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", run_name.strip().lower())
    cleaned = cleaned.strip("_")
    if not cleaned:
        raise ValueError("run_name debe contener al menos un caracter valido.")
    return f"run_{cleaned}"


def ensure_monitoring_schema(connection: sqlite3.Connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS experiment_runs (
            run_name TEXT PRIMARY KEY,
            table_name TEXT NOT NULL UNIQUE,
            slave_id INTEGER NOT NULL,
            registers TEXT NOT NULL,
            sample_every_seconds INTEGER NOT NULL,
            duration_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def build_modbus_register_reader(client: MasterModbusCompute) -> RegisterReader:
    def reader(slave_id: int, register: int) -> int | None:
        values = client.read_holding_registers(slave_id=slave_id, address=register, count=1)
        if not values:
            return None
        return values[0]

    return reader


def build_run_dataframe(
    run_name: str,
    slave_id: int,
    registers: Iterable[int],
    duration_minutes: int,
    reader: RegisterReader,
    sample_every_seconds: int = 60,
    started_at: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> pd.DataFrame:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes debe ser mayor que cero.")
    if sample_every_seconds <= 0:
        raise ValueError("sample_every_seconds debe ser mayor que cero.")

    register_list = list(registers)
    if not register_list:
        raise ValueError("registers debe contener al menos un registro.")

    sample_count = max(1, duration_minutes * 60 // sample_every_seconds)
    base_time = started_at or datetime.now(timezone.utc)
    rows: list[dict[str, object]] = []

    for sample_index in range(sample_count):
        row: dict[str, object] = {
            "run_name": run_name,
            "slave_id": slave_id,
            "sample_index": sample_index,
            "sample_timestamp": (
                base_time + timedelta(seconds=sample_index * sample_every_seconds)
            ).isoformat(),
        }
        for register in register_list:
            row[f"register_{register}"] = reader(slave_id, register)
        rows.append(row)

        if sample_index < sample_count - 1:
            sleep_fn(sample_every_seconds)

    return pd.DataFrame(rows)


def save_run_dataframe(
    dataframe: pd.DataFrame,
    database_path: str | Path,
    run_name: str,
    slave_id: int,
    registers: Iterable[int],
    sample_every_seconds: int,
    duration_minutes: int,
    table_name: str | None = None,
) -> MonitoringRun:
    db_path = Path(database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_table_name = table_name or sanitize_table_name(run_name)
    with sqlite3.connect(db_path) as connection:
        ensure_monitoring_schema(connection)
        dataframe.to_sql(resolved_table_name, connection, if_exists="replace", index=False)
        connection.execute(
            """
            INSERT INTO experiment_runs (
                run_name,
                table_name,
                slave_id,
                registers,
                sample_every_seconds,
                duration_minutes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_name) DO UPDATE SET
                table_name = excluded.table_name,
                slave_id = excluded.slave_id,
                registers = excluded.registers,
                sample_every_seconds = excluded.sample_every_seconds,
                duration_minutes = excluded.duration_minutes,
                created_at = excluded.created_at
            """,
            (
                run_name,
                resolved_table_name,
                slave_id,
                ",".join(str(register) for register in registers),
                sample_every_seconds,
                duration_minutes,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.commit()

    return MonitoringRun(
        run_name=run_name,
        table_name=resolved_table_name,
        dataframe=dataframe,
        database_path=db_path,
    )


def monitor_slave_registers(
    run_name: str,
    slave_id: int,
    registers: Iterable[int],
    duration_minutes: int,
    reader: RegisterReader,
    database_path: str | Path = "data/monitoring.sqlite",
    sample_every_seconds: int = 60,
    table_name: str | None = None,
    started_at: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MonitoringRun:
    dataframe = build_run_dataframe(
        run_name=run_name,
        slave_id=slave_id,
        registers=registers,
        duration_minutes=duration_minutes,
        reader=reader,
        sample_every_seconds=sample_every_seconds,
        started_at=started_at,
        sleep_fn=sleep_fn,
    )
    return save_run_dataframe(
        dataframe=dataframe,
        database_path=database_path,
        run_name=run_name,
        slave_id=slave_id,
        registers=registers,
        sample_every_seconds=sample_every_seconds,
        duration_minutes=duration_minutes,
        table_name=table_name,
    )


def monitor_with_client(
    run_name: str,
    client: MasterModbusCompute,
    slave_id: int,
    registers: Iterable[int],
    duration_minutes: int,
    database_path: str | Path = "data/monitoring.sqlite",
    sample_every_seconds: int = 60,
    table_name: str | None = None,
    started_at: datetime | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> MonitoringRun:
    return monitor_slave_registers(
        run_name=run_name,
        slave_id=slave_id,
        registers=registers,
        duration_minutes=duration_minutes,
        reader=build_modbus_register_reader(client),
        database_path=database_path,
        sample_every_seconds=sample_every_seconds,
        table_name=table_name,
        started_at=started_at,
        sleep_fn=sleep_fn,
    )


def load_run_dataframe(database_path: str | Path, table_name: str) -> pd.DataFrame:
    with sqlite3.connect(database_path) as connection:
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', connection)
