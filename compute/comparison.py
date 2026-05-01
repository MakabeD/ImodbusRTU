from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt

# Optional plotting dependencies
import numpy as np
import pandas as pd

try:
    import seaborn as sns  # type: ignore

    SEABORN_AVAILABLE = True
except Exception:
    SEABORN_AVAILABLE = False

from compute.monitoring import load_run_dataframe


@dataclass(frozen=True)
class ComparisonDashboard:
    left_table: str
    right_table: str
    summary_df: pd.DataFrame
    delta_df: pd.DataFrame
    html: str
    output_path: Path | None
    # Optional path mappings for generated plots (name -> file path)
    plots: dict[str, str] | None = None


def _register_columns(dataframe: pd.DataFrame) -> list[str]:
    return [column for column in dataframe.columns if column.startswith("register_")]


def _validate_comparable_tables(left_df: pd.DataFrame, right_df: pd.DataFrame):
    left_registers = _register_columns(left_df)
    right_registers = _register_columns(right_df)

    if left_registers != right_registers:
        raise ValueError("Las tablas no tienen las mismas columnas de registros.")

    if len(left_df) != len(right_df):
        raise ValueError("Las tablas no tienen la misma cantidad de filas.")


def build_delta_dataframe(
    left_df: pd.DataFrame, right_df: pd.DataFrame
) -> pd.DataFrame:
    _validate_comparable_tables(left_df, right_df)

    registers = _register_columns(left_df)
    delta_df = right_df[registers].subtract(left_df[registers])
    delta_df.insert(0, "sample_index", left_df["sample_index"])
    if "sample_timestamp" in left_df.columns:
        delta_df.insert(1, "sample_timestamp", left_df["sample_timestamp"])
    return delta_df


def build_summary_dataframe(
    left_df: pd.DataFrame, right_df: pd.DataFrame
) -> pd.DataFrame:
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


def _build_plot_bar_chart(summary_df: pd.DataFrame) -> plt.Figure:
    """Create a grouped bar chart of left_mean vs right_mean per register.

    Returns a matplotlib Figure object.
    """
    registers = summary_df["register"].tolist()
    left_means = summary_df["left_mean"].tolist()
    right_means = summary_df["right_mean"].tolist()

    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(registers))
    width = max(0.25, min(0.35, 0.8 / max(1, len(registers))))

    ax.bar(x - width / 2, left_means, width=width, label="Left mean")
    ax.bar(x + width / 2, right_means, width=width, label="Right mean")
    ax.set_xticks(x)
    ax.set_xticklabels(registers, rotation=45, ha="right")
    ax.set_ylabel("Mean Value")
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.3)
    plt.tight_layout()
    return fig


def _build_plot_heatmap(delta_df: pd.DataFrame, registers: list[str]) -> plt.Figure:
    """Create a heatmap of delta values per sample (rows) and register (columns).

    This function returns a Figure object.
    """
    heatmap_df = delta_df[registers].copy()
    if heatmap_df.shape[0] > 200:
        # Downsample for readability
        idx = np.linspace(0, heatmap_df.shape[0] - 1, 200, dtype=int)
        heatmap_df = heatmap_df.iloc[idx]
    # Use sample_index as y-axis labels when available
    if "sample_index" in delta_df.columns:
        heatmap_df.index = (
            delta_df["sample_index"].astype(str).iloc[: heatmap_df.shape[0]]
        )

    fig, ax = plt.subplots(figsize=(10, 4))
    if SEABORN_AVAILABLE:
        sns.heatmap(heatmap_df.T, ax=ax, cmap="coolwarm", center=0)
        ax.set_xlabel("Sample index subset")
        ax.set_ylabel("Register")
        ax.invert_yaxis()
    else:
        im = ax.imshow(heatmap_df.T.values, aspect="auto", cmap="coolwarm")
        ax.set_xticks(range(heatmap_df.shape[0]))
        ax.set_yticks(range(len(registers)))
        ax.set_yticklabels(registers)
        fig.colorbar(im, ax=ax)
        ax.set_ylabel("Register")
        ax.set_xlabel("Sample index subset")
    plt.tight_layout()
    return fig


def render_dashboard_plots(
    left_table: str,
    right_table: str,
    summary_df: pd.DataFrame,
    delta_df: pd.DataFrame,
    output_path: str | Path | None = None,
) -> dict[str, str] | None:
    """Render a matplotlib/seaborn based dashboard and optionally save to file.

    Returns a dict mapping plot name to the saved file path, or None if not rendered.
    """
    if summary_df.empty or delta_df.empty:
        return None

    registers = summary_df["register"].tolist()

    # Build plots
    bar_fig = _build_plot_bar_chart(summary_df)
    heatmap_fig = _build_plot_heatmap(delta_df, registers)

    plots: dict[str, str] = {}
    if output_path is not None:
        out_p = Path(output_path)
        # If a file path was supplied, use it directly; otherwise create a default filename
        if out_p.suffix == "":
            out_p = out_p / "dashboard_plots.png"

        # Save the figures to separate files to avoid large in-memory PNGs
        bar_path = out_p.parent / (out_p.stem + "_bar.png")
        heat_path = out_p.parent / (out_p.stem + "_heatmap.png")
        bar_fig.savefig(bar_path, dpi=300, bbox_inches="tight")
        heatmap_fig.savefig(heat_path, dpi=300, bbox_inches="tight")
        plt.close(bar_fig)
        plt.close(heatmap_fig)
        plots["bar"] = str(bar_path)
        plots["heatmap"] = str(heat_path)
        return plots
    else:
        # If not saving, just close and return None (caller may display inline)
        plt.close(bar_fig)
        plt.close(heatmap_fig)
        return None


def compare_dataframes(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_table: str = "left",
    right_table: str = "right",
    output_path: str | Path | None = None,
    render_html: bool = False,
) -> ComparisonDashboard:
    summary_df = build_summary_dataframe(left_df, right_df)
    delta_df = build_delta_dataframe(left_df, right_df)
    html = render_dashboard_html(left_table, right_table, summary_df, delta_df)
    resolved_output_path: Path | None = None
    if output_path is not None:
        resolved_output_path = Path(output_path)
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_output_path.write_text(
            html, encoding="utf-8"
        ) if render_html else print("render html seted false")

    # Generate plots if an output path is provided
    plots: dict[str, str] | None = None
    if output_path is not None:
        plots = render_dashboard_plots(
            left_table=left_table,
            right_table=right_table,
            summary_df=summary_df,
            delta_df=delta_df,
            output_path=output_path,
        )

    return ComparisonDashboard(
        left_table=left_table,
        right_table=right_table,
        summary_df=summary_df,
        delta_df=delta_df,
        html=html,
        output_path=resolved_output_path,
        plots=plots,
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
