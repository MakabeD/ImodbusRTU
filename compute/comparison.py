from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from compute.monitoring import ensure_monitoring_schema
from compute.monitoring import load_run_dataframe


@dataclass(frozen=True)
class ComparisonMetadata:
    left_table: str
    right_table: str
    sample_count: int
    register_count: int
    changed_register_count: int
    aligned_on: str
    same_slave_id: bool
    timestamps_match: bool | None


@dataclass(frozen=True)
class ComparisonDashboard:
    left_table: str
    right_table: str
    summary_df: pd.DataFrame
    delta_df: pd.DataFrame
    html: str
    output_path: Path | None
    plots: dict[str, str] | None = None
    metadata: ComparisonMetadata | None = None


def _register_columns(dataframe: pd.DataFrame) -> list[str]:
    return [column for column in dataframe.columns if column.startswith("register_")]


def _alignment_column(left_df: pd.DataFrame, right_df: pd.DataFrame) -> str:
    if "sample_index" in left_df.columns and "sample_index" in right_df.columns:
        return "sample_index"
    if "sample_timestamp" in left_df.columns and "sample_timestamp" in right_df.columns:
        return "sample_timestamp"
    raise ValueError(
        "Las tablas deben compartir sample_index o sample_timestamp para alinearse."
    )


def _validate_comparable_tables(left_df: pd.DataFrame, right_df: pd.DataFrame):
    left_registers = _register_columns(left_df)
    right_registers = _register_columns(right_df)

    if not left_registers:
        raise ValueError("La tabla izquierda no contiene columnas de registros.")
    if not right_registers:
        raise ValueError("La tabla derecha no contiene columnas de registros.")
    if left_registers != right_registers:
        raise ValueError("Las tablas no tienen las mismas columnas de registros.")

    if len(left_df) != len(right_df):
        raise ValueError("Las tablas no tienen la misma cantidad de filas.")

    alignment_column = _alignment_column(left_df, right_df)

    left_duplicates = left_df[alignment_column].duplicated().any()
    right_duplicates = right_df[alignment_column].duplicated().any()
    if left_duplicates or right_duplicates:
        raise ValueError(
            f"La columna de alineacion '{alignment_column}' contiene valores duplicados."
        )

    if "slave_id" in left_df.columns and "slave_id" in right_df.columns:
        left_slaves = set(left_df["slave_id"].dropna().unique().tolist())
        right_slaves = set(right_df["slave_id"].dropna().unique().tolist())
        if left_slaves != right_slaves:
            raise ValueError("Las tablas pertenecen a esclavos distintos.")


def _aligned_frames(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    _validate_comparable_tables(left_df, right_df)

    alignment_column = _alignment_column(left_df, right_df)
    left_sorted = left_df.sort_values(alignment_column).reset_index(drop=True)
    right_sorted = right_df.sort_values(alignment_column).reset_index(drop=True)

    if not left_sorted[alignment_column].equals(right_sorted[alignment_column]):
        raise ValueError(
            f"Las tablas no tienen los mismos valores de alineacion en '{alignment_column}'."
        )

    return left_sorted, right_sorted, [alignment_column]


def build_delta_dataframe(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
) -> pd.DataFrame:
    left_aligned, right_aligned, alignment_columns = _aligned_frames(left_df, right_df)

    registers = _register_columns(left_aligned)
    delta_df = right_aligned[registers].subtract(left_aligned[registers])

    insert_position = 0
    for column in alignment_columns:
        delta_df.insert(insert_position, column, left_aligned[column])
        insert_position += 1

    if "slave_id" in left_aligned.columns:
        delta_df.insert(insert_position, "slave_id", left_aligned["slave_id"])

    return delta_df


def build_summary_dataframe(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
) -> pd.DataFrame:
    left_aligned, right_aligned, _ = _aligned_frames(left_df, right_df)

    rows: list[dict[str, object]] = []
    for column in _register_columns(left_aligned):
        left_series = pd.to_numeric(left_aligned[column], errors="coerce")
        right_series = pd.to_numeric(right_aligned[column], errors="coerce")
        delta_series = right_series - left_series
        rows.append(
            {
                "register": column,
                "left_mean": left_series.mean(),
                "right_mean": right_series.mean(),
                "mean_delta": delta_series.mean(),
                "max_abs_delta": delta_series.abs().max(),
                "changed_samples": int(delta_series.fillna(0).ne(0).sum()),
                "left_min": left_series.min(),
                "left_max": left_series.max(),
                "right_min": right_series.min(),
                "right_max": right_series.max(),
            }
        )

    summary_df = pd.DataFrame(rows)
    if not summary_df.empty:
        summary_df = summary_df.sort_values(
            by=["max_abs_delta", "changed_samples", "register"],
            ascending=[False, False, True],
        ).reset_index(drop=True)

    return summary_df


def _build_metadata(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_table: str,
    right_table: str,
    summary_df: pd.DataFrame,
    alignment_columns: list[str],
) -> ComparisonMetadata:
    same_slave_id = True
    if "slave_id" in left_df.columns and "slave_id" in right_df.columns:
        same_slave_id = left_df["slave_id"].equals(right_df["slave_id"])

    timestamps_match: bool | None = None
    if "sample_timestamp" in left_df.columns and "sample_timestamp" in right_df.columns:
        timestamps_match = left_df["sample_timestamp"].equals(right_df["sample_timestamp"])

    changed_register_count = int((summary_df["changed_samples"] > 0).sum())
    return ComparisonMetadata(
        left_table=left_table,
        right_table=right_table,
        sample_count=len(left_df),
        register_count=len(_register_columns(left_df)),
        changed_register_count=changed_register_count,
        aligned_on=alignment_columns[0],
        same_slave_id=same_slave_id,
        timestamps_match=timestamps_match,
    )


def _plot_dependencies_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def _build_change_overview_plot(summary_df: pd.DataFrame):
    import matplotlib.pyplot as plt

    top_df = summary_df.head(12).copy()
    labels = [register.replace("register_", "R") for register in top_df["register"]]
    mean_delta = top_df["mean_delta"].fillna(0)
    max_abs_delta = top_df["max_abs_delta"].fillna(0)
    changed_samples = top_df["changed_samples"].fillna(0)
    colors = ["#ca8a04" if value >= 0 else "#0f766e" for value in mean_delta]

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(16, 6),
        gridspec_kw={"width_ratios": [1.35, 1]},
        facecolor="#f5fbf7",
    )
    fig.suptitle(
        "Register Change Overview",
        fontsize=18,
        fontweight="bold",
        color="#163329",
        y=0.98,
    )

    axes[0].barh(labels, max_abs_delta, color=colors, edgecolor="#163329", alpha=0.9)
    axes[0].invert_yaxis()
    axes[0].set_title("Top Registers by Max Absolute Delta", loc="left", fontsize=12)
    axes[0].set_xlabel("Max |delta|")
    axes[0].grid(axis="x", linestyle="--", alpha=0.25)
    axes[0].set_facecolor("#ffffff")

    scatter = axes[1].scatter(
        changed_samples,
        max_abs_delta,
        s=(changed_samples + 1) * 45,
        c=mean_delta,
        cmap="RdYlGn_r",
        edgecolors="#163329",
        linewidths=0.7,
        alpha=0.95,
    )
    axes[1].set_title("Stability vs Delta", loc="left", fontsize=12)
    axes[1].set_xlabel("Changed Samples")
    axes[1].set_ylabel("Max |delta|")
    axes[1].grid(True, linestyle="--", alpha=0.25)
    axes[1].set_facecolor("#ffffff")
    for _, row in top_df.iterrows():
        axes[1].annotate(
            row["register"].replace("register_", "R"),
            (row["changed_samples"], row["max_abs_delta"]),
            fontsize=8,
            xytext=(4, 4),
            textcoords="offset points",
            color="#163329",
        )
    colorbar = fig.colorbar(scatter, ax=axes[1], shrink=0.86)
    colorbar.set_label("Mean delta")

    fig.tight_layout()
    return fig


def _build_trend_plot(left_df: pd.DataFrame, right_df: pd.DataFrame, summary_df: pd.DataFrame):
    import matplotlib.pyplot as plt

    trend_registers = summary_df.head(4)["register"].tolist()
    sample_axis = (
        left_df["sample_index"].tolist()
        if "sample_index" in left_df.columns
        else list(range(len(left_df)))
    )

    fig, axes = plt.subplots(
        len(trend_registers),
        1,
        figsize=(14, max(3.2 * len(trend_registers), 4.5)),
        sharex=True,
        facecolor="#f5fbf7",
    )
    if len(trend_registers) == 1:
        axes = [axes]

    fig.suptitle(
        "Trend Comparison for Most Dynamic Registers",
        fontsize=18,
        fontweight="bold",
        color="#163329",
        y=0.995,
    )

    for axis, register in zip(axes, trend_registers):
        left_series = pd.to_numeric(left_df[register], errors="coerce")
        right_series = pd.to_numeric(right_df[register], errors="coerce")
        axis.plot(
            sample_axis,
            left_series,
            color="#0f766e",
            linewidth=2.1,
            marker="o",
            markersize=3,
            label="Left run",
        )
        axis.plot(
            sample_axis,
            right_series,
            color="#ca8a04",
            linewidth=2.1,
            marker="o",
            markersize=3,
            label="Right run",
        )
        axis.fill_between(
            sample_axis,
            left_series,
            right_series,
            color="#d97706",
            alpha=0.12,
        )
        axis.set_title(register.replace("register_", "Register "), loc="left", fontsize=11)
        axis.grid(True, linestyle="--", alpha=0.24)
        axis.set_facecolor("#ffffff")
        axis.legend(loc="upper right")

    axes[-1].set_xlabel("Sample index")
    fig.tight_layout()
    return fig


def _build_heatmap_plot(delta_df: pd.DataFrame, registers: list[str]):
    import matplotlib.pyplot as plt
    import numpy as np

    heatmap_df = delta_df[registers].copy()
    sample_labels = (
        delta_df["sample_index"].tolist()
        if "sample_index" in delta_df.columns
        else list(range(len(delta_df)))
    )
    if len(heatmap_df) > 160:
        sample_idx = np.linspace(0, len(heatmap_df) - 1, 160, dtype=int)
        heatmap_df = heatmap_df.iloc[sample_idx]
        sample_labels = [sample_labels[index] for index in sample_idx]

    values = heatmap_df.T.values.astype(float)
    finite_mask = ~pd.isna(values)
    max_abs = float(abs(values[finite_mask]).max()) if finite_mask.any() else 1.0
    if max_abs == 0:
        max_abs = 1.0

    fig, ax = plt.subplots(figsize=(16, 6), facecolor="#f5fbf7")
    image = ax.imshow(
        values,
        aspect="auto",
        cmap="RdYlGn_r",
        vmin=-max_abs,
        vmax=max_abs,
    )
    ax.set_title("Delta Heatmap by Register and Sample", loc="left", fontsize=14)
    ax.set_xlabel("Sample slice")
    ax.set_ylabel("Register")
    ax.set_yticks(range(len(registers)))
    ax.set_yticklabels([register.replace("register_", "R") for register in registers])

    xtick_count = min(len(heatmap_df), 8)
    if xtick_count > 0:
        xticks = np.linspace(0, len(heatmap_df) - 1, xtick_count, dtype=int)
        ax.set_xticks(xticks)
        labels = [sample_labels[index] for index in xticks]
        ax.set_xticklabels(labels)

    colorbar = fig.colorbar(image, ax=ax, shrink=0.9)
    colorbar.set_label("Delta")
    fig.tight_layout()
    return fig


def render_dashboard_plots(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    delta_df: pd.DataFrame,
    output_path: str | Path | None,
) -> dict[str, str] | None:
    if output_path is None or summary_df.empty or delta_df.empty:
        return None
    if not _plot_dependencies_available():
        return None

    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    resolved_output_path = Path(output_path)
    resolved_output_path.parent.mkdir(parents=True, exist_ok=True)
    stem = resolved_output_path.stem or "dashboard"

    overview_fig = _build_change_overview_plot(summary_df)
    trend_fig = _build_trend_plot(left_df, right_df, summary_df)
    heatmap_fig = _build_heatmap_plot(delta_df, summary_df["register"].tolist())

    overview_path = resolved_output_path.parent / f"{stem}_overview.png"
    trend_path = resolved_output_path.parent / f"{stem}_trends.png"
    heatmap_path = resolved_output_path.parent / f"{stem}_heatmap.png"

    overview_fig.savefig(overview_path, dpi=220, bbox_inches="tight", facecolor="#f5fbf7")
    trend_fig.savefig(trend_path, dpi=220, bbox_inches="tight", facecolor="#f5fbf7")
    heatmap_fig.savefig(heatmap_path, dpi=220, bbox_inches="tight", facecolor="#f5fbf7")

    plt.close(overview_fig)
    plt.close(trend_fig)
    plt.close(heatmap_fig)

    return {
        "overview": str(overview_path),
        "trends": str(trend_path),
        "heatmap": str(heatmap_path),
    }


def _format_number(value) -> str:
    if pd.isna(value):
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.3f}"
    return str(value)


def _image_sections(plots: dict[str, str] | None, html_output_path: Path | None) -> str:
    if not plots:
        return "<p class='empty-state'>Plot generation was not available in this environment.</p>"

    sections: list[str] = []
    titles = {
        "overview": "Overview Plot",
        "trends": "Trend Comparison",
        "heatmap": "Delta Heatmap",
    }
    for key, title in titles.items():
        if key not in plots:
            continue
        image_path = Path(plots[key])
        relative_path = (
            image_path.name
            if html_output_path is None
            else image_path.relative_to(html_output_path.parent).as_posix()
        )
        sections.append(
            f"""
            <section class="plot-card">
              <h3>{html.escape(title)}</h3>
              <img src="{html.escape(relative_path)}" alt="{html.escape(title)}">
            </section>
            """.strip()
        )
    return "\n".join(sections)


def render_dashboard_html(
    left_table: str,
    right_table: str,
    summary_df: pd.DataFrame,
    delta_df: pd.DataFrame,
    metadata: ComparisonMetadata,
    plots: dict[str, str] | None,
    html_output_path: Path | None,
) -> str:
    styled_summary = summary_df.copy()
    for numeric_column in [
        "left_mean",
        "right_mean",
        "mean_delta",
        "max_abs_delta",
        "left_min",
        "left_max",
        "right_min",
        "right_max",
    ]:
        if numeric_column in styled_summary.columns:
            styled_summary[numeric_column] = styled_summary[numeric_column].map(
                _format_number
            )

    delta_preview = delta_df.head(24).copy()
    for column in delta_preview.columns:
        if column.startswith("register_"):
            delta_preview[column] = delta_preview[column].map(_format_number)

    summary_html = styled_summary.to_html(index=False, classes=["summary-table"])
    delta_html = delta_preview.to_html(index=False, classes=["delta-table"])
    alignment = metadata.aligned_on
    timestamp_status = "n/a"
    if metadata.timestamps_match is True:
        timestamp_status = "same timestamps"
    elif metadata.timestamps_match is False:
        timestamp_status = "different timestamps"
    plot_sections = _image_sections(plots, html_output_path)

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Monitoring Comparison Dashboard</title>
  <style>
    :root {{
      --ink: #163329;
      --muted: #5b6f68;
      --card: rgba(255, 255, 255, 0.92);
      --line: #d7e4dc;
      --accent: #0f766e;
      --accent-2: #ca8a04;
      --background-a: #f4fbf5;
      --background-b: #eef7fb;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Trebuchet MS", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.14), transparent 30%),
        radial-gradient(circle at top right, rgba(202, 138, 4, 0.12), transparent 25%),
        linear-gradient(180deg, var(--background-a) 0%, var(--background-b) 100%);
    }}
    .page {{
      width: min(1380px, calc(100% - 48px));
      margin: 32px auto 48px;
    }}
    .hero {{
      padding: 28px 30px;
      border-radius: 24px;
      background: linear-gradient(140deg, rgba(22, 51, 41, 0.96), rgba(15, 118, 110, 0.92));
      color: #f5fbf7;
      box-shadow: 0 24px 60px rgba(22, 51, 41, 0.22);
    }}
    .hero h1 {{
      margin: 0 0 10px;
      font-size: 2rem;
      letter-spacing: 0.02em;
    }}
    .hero p {{
      margin: 0;
      color: rgba(245, 251, 247, 0.82);
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 14px;
      margin-top: 22px;
    }}
    .metric {{
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(255, 255, 255, 0.12);
      border: 1px solid rgba(255, 255, 255, 0.14);
      backdrop-filter: blur(8px);
    }}
    .metric .label {{
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: rgba(245, 251, 247, 0.68);
    }}
    .metric .value {{
      display: block;
      margin-top: 6px;
      font-size: 1.35rem;
      font-weight: 700;
      color: #ffffff;
    }}
    .section {{
      margin-top: 28px;
      padding: 24px;
      border-radius: 22px;
      background: var(--card);
      border: 1px solid rgba(215, 228, 220, 0.95);
      box-shadow: 0 18px 42px rgba(31, 41, 55, 0.08);
    }}
    .section h2 {{
      margin: 0 0 14px;
      font-size: 1.25rem;
    }}
    .plot-grid {{
      display: grid;
      gap: 18px;
    }}
    .plot-card {{
      padding: 18px;
      border-radius: 18px;
      background: linear-gradient(180deg, #ffffff, #f8fcfa);
      border: 1px solid var(--line);
    }}
    .plot-card h3 {{
      margin: 0 0 10px;
      font-size: 1rem;
    }}
    .plot-card img {{
      width: 100%;
      display: block;
      border-radius: 14px;
      border: 1px solid #e0ebe4;
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.94rem;
      overflow: hidden;
      border-radius: 16px;
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid #e5efe8;
      text-align: right;
      white-space: nowrap;
    }}
    th {{
      background: #e4f1e9;
      color: var(--ink);
      position: sticky;
      top: 0;
    }}
    td:first-child, th:first-child {{
      text-align: left;
    }}
    tr:nth-child(even) td {{
      background: rgba(242, 248, 244, 0.8);
    }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: white;
    }}
    .caption {{
      margin: 0 0 14px;
      color: var(--muted);
    }}
    .empty-state {{
      margin: 0;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="page">
    <section class="hero">
      <h1>Monitoring Comparison Dashboard</h1>
      <p>Comparing <strong>{html.escape(left_table)}</strong> against <strong>{html.escape(right_table)}</strong>.</p>
      <div class="hero-grid">
        <div class="metric">
          <span class="label">Samples</span>
          <span class="value">{metadata.sample_count}</span>
        </div>
        <div class="metric">
          <span class="label">Registers</span>
          <span class="value">{metadata.register_count}</span>
        </div>
        <div class="metric">
          <span class="label">Changed Registers</span>
          <span class="value">{metadata.changed_register_count}</span>
        </div>
        <div class="metric">
          <span class="label">Aligned On</span>
          <span class="value">{html.escape(alignment)}</span>
        </div>
        <div class="metric">
          <span class="label">Timestamp Check</span>
          <span class="value">{html.escape(timestamp_status)}</span>
        </div>
      </div>
    </section>

    <section class="section">
      <h2>Visual Analysis</h2>
      <p class="caption">The plots highlight the strongest register shifts, how both runs evolve over time, and where deltas concentrate across the experiment.</p>
      <div class="plot-grid">
        {plot_sections}
      </div>
    </section>

    <section class="section">
      <h2>Register Summary</h2>
      <p class="caption">Registers are sorted by strongest absolute change first.</p>
      <div class="table-wrap">
        {summary_html}
      </div>
    </section>

    <section class="section">
      <h2>Delta Preview</h2>
      <p class="caption">Showing the first 24 aligned samples from the sample-by-sample delta table.</p>
      <div class="table-wrap">
        {delta_html}
      </div>
    </section>
  </div>
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
    left_aligned, right_aligned, alignment_columns = _aligned_frames(left_df, right_df)
    summary_df = build_summary_dataframe(left_aligned, right_aligned)
    delta_df = build_delta_dataframe(left_aligned, right_aligned)
    metadata = _build_metadata(
        left_df=left_aligned,
        right_df=right_aligned,
        left_table=left_table,
        right_table=right_table,
        summary_df=summary_df,
        alignment_columns=alignment_columns,
    )

    resolved_output_path: Path | None = None
    if output_path is not None:
        resolved_output_path = Path(output_path)
        if resolved_output_path.suffix.lower() != ".html":
            resolved_output_path = resolved_output_path / "dashboard.html"
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

    plots = render_dashboard_plots(
        left_df=left_aligned,
        right_df=right_aligned,
        summary_df=summary_df,
        delta_df=delta_df,
        output_path=resolved_output_path,
    )
    html_content = render_dashboard_html(
        left_table=left_table,
        right_table=right_table,
        summary_df=summary_df,
        delta_df=delta_df,
        metadata=metadata,
        plots=plots,
        html_output_path=resolved_output_path,
    )

    if resolved_output_path is not None:
        resolved_output_path.write_text(html_content, encoding="utf-8")

    return ComparisonDashboard(
        left_table=left_table,
        right_table=right_table,
        summary_df=summary_df,
        delta_df=delta_df,
        html=html_content,
        output_path=resolved_output_path,
        plots=plots,
        metadata=metadata,
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
        ensure_monitoring_schema(connection)
        return pd.read_sql_query(
            """
            SELECT run_name, table_name, slave_id, registers, sample_every_seconds,
                   duration_minutes, created_at
            FROM experiment_runs
            ORDER BY created_at DESC
            """,
            connection,
        )
