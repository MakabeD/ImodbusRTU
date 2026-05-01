from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from compute.monitoring import load_run_dataframe


@dataclass(frozen=True)
class ComparisonDashboard:
    left_table: str
    right_table: str
    summary_df: pd.DataFrame
    delta_df: pd.DataFrame
    html: str
    output_path: Path | None


def _register_columns(dataframe: pd.DataFrame) -> list[str]:
    return [column for column in dataframe.columns if column.startswith("register_")]


def _validate_comparable_tables(left_df: pd.DataFrame, right_df: pd.DataFrame):
    left_registers = _register_columns(left_df)
    right_registers = _register_columns(right_df)

    if left_registers != right_registers:
        raise ValueError("Las tablas no tienen las mismas columnas de registros.")

    if len(left_df) != len(right_df):
        raise ValueError("Las tablas no tienen la misma cantidad de filas.")


def build_delta_dataframe(left_df: pd.DataFrame, right_df: pd.DataFrame) -> pd.DataFrame:
    _validate_comparable_tables(left_df, right_df)

    registers = _register_columns(left_df)
    delta_df = right_df[registers].subtract(left_df[registers])
    delta_df.insert(0, "sample_index", left_df["sample_index"])
    if "sample_timestamp" in left_df.columns:
        delta_df.insert(1, "sample_timestamp", left_df["sample_timestamp"])
    return delta_df


def build_summary_dataframe(left_df: pd.DataFrame, right_df: pd.DataFrame) -> pd.DataFrame:
    _validate_comparable_tables(left_df, right_df)

    rows: list[dict[str, object]] = []
    for column in _register_columns(left_df):
        left_series = left_df[column]
        right_series = right_df[column]
        delta_series = right_series - left_series
        rows.append(
            {
                "register": column,
                "left_mean": left_series.mean(),
                "right_mean": right_series.mean(),
                "mean_delta": delta_series.mean(),
                "max_abs_delta": delta_series.abs().max(),
                "changed_samples": int(delta_series.fillna(0).ne(0).sum()),
            }
        )

    return pd.DataFrame(rows)


def render_dashboard_html(
    left_table: str,
    right_table: str,
    summary_df: pd.DataFrame,
    delta_df: pd.DataFrame,
) -> str:
    summary_html = summary_df.to_html(index=False, classes=["summary-table"])
    delta_html = delta_df.to_html(index=False, classes=["delta-table"])

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Monitoring Comparison Dashboard</title>
  <style>
    body {{
      font-family: "Segoe UI", sans-serif;
      margin: 2rem;
      color: #1f2937;
      background: linear-gradient(180deg, #f7fbff 0%, #eef6f2 100%);
    }}
    h1, h2 {{
      margin-bottom: 0.5rem;
    }}
    .meta {{
      margin-bottom: 1.5rem;
      padding: 1rem;
      background: white;
      border-radius: 12px;
      box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08);
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      background: white;
      margin-bottom: 2rem;
      box-shadow: 0 10px 25px rgba(15, 23, 42, 0.08);
    }}
    th, td {{
      border: 1px solid #dbe4ee;
      padding: 0.65rem 0.8rem;
      text-align: right;
    }}
    th {{
      background: #d9efe4;
      color: #123524;
    }}
    td:first-child, th:first-child {{
      text-align: left;
    }}
  </style>
</head>
<body>
  <div class="meta">
    <h1>Monitoring Comparison Dashboard</h1>
    <p>Left table: <strong>{left_table}</strong></p>
    <p>Right table: <strong>{right_table}</strong></p>
  </div>
  <h2>Summary</h2>
  {summary_html}
  <h2>Sample-by-Sample Delta</h2>
  {delta_html}
</body>
</html>
""".strip()


def compare_dataframes(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_table: str = "left",
    right_table: str = "right",
    output_path: str | Path | None = None,
) -> ComparisonDashboard:
    summary_df = build_summary_dataframe(left_df, right_df)
    delta_df = build_delta_dataframe(left_df, right_df)
    html = render_dashboard_html(left_table, right_table, summary_df, delta_df)

    resolved_output_path: Path | None = None
    if output_path is not None:
        resolved_output_path = Path(output_path)
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_output_path.write_text(html, encoding="utf-8")

    return ComparisonDashboard(
        left_table=left_table,
        right_table=right_table,
        summary_df=summary_df,
        delta_df=delta_df,
        html=html,
        output_path=resolved_output_path,
    )


def compare_sqlite_tables(
    database_path: str | Path,
    left_table: str,
    right_table: str,
    output_path: str | Path | None = None,
) -> ComparisonDashboard:
    left_df = load_run_dataframe(database_path, left_table)
    right_df = load_run_dataframe(database_path, right_table)
    return compare_dataframes(
        left_df=left_df,
        right_df=right_df,
        left_table=left_table,
        right_table=right_table,
        output_path=output_path,
    )


def list_monitoring_tables(database_path: str | Path) -> pd.DataFrame:
    with sqlite3.connect(database_path) as connection:
        return pd.read_sql_query(
            """
            SELECT run_name, table_name, slave_id, registers, sample_every_seconds,
                   duration_minutes, created_at
            FROM experiment_runs
            ORDER BY created_at DESC
            """,
            connection,
        )
