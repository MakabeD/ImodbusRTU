from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pandas as pd

from imodbus_rtu.compute.comparison import _register_columns
from imodbus_rtu.compute.monitoring import load_run_dataframe


CHANGE_SCORE_LOW = "LOW"
CHANGE_SCORE_MEDIUM = "MEDIUM"
CHANGE_SCORE_HIGH = "HIGH"
CHANGE_SCORE_NONE = "NONE"


def _safe_sigma(value) -> float:
    if value is None:
        return 0.0
    try:
        if math.isnan(float(value)):
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    return float(value)


def _pooled_sigma(sigma_air, sigma_soil) -> float:
    if pd.isna(sigma_air) or pd.isna(sigma_soil):
        return float("nan")
    s_air = _safe_sigma(sigma_air)
    s_soil = _safe_sigma(sigma_soil)
    return math.sqrt((s_air ** 2 + s_soil ** 2) / 2)


def effect_size(delta, sigma_air, sigma_soil) -> float:
    if delta is None or pd.isna(delta):
        return 0.0
    if float(delta) == 0:
        return 0.0
    if pd.isna(sigma_air) or pd.isna(sigma_soil):
        return float("nan")
    s_air = _safe_sigma(sigma_air)
    s_soil = _safe_sigma(sigma_soil)
    if s_air == 0 and s_soil == 0:
        return math.inf
    pooled_sigma = _pooled_sigma(sigma_air, sigma_soil)
    if pooled_sigma == 0:
        return math.inf
    return abs(float(delta)) / pooled_sigma


def classify_change_score(
    delta,
    sigma_air,
    sigma_soil,
    low_threshold: float = 0.5,
    medium_threshold: float = 1.5,
) -> str:
    if delta is None or pd.isna(delta) or float(delta) == 0:
        return CHANGE_SCORE_NONE

    if pd.isna(sigma_air) or pd.isna(sigma_soil):
        return CHANGE_SCORE_NONE

    s_air = _safe_sigma(sigma_air)
    s_soil = _safe_sigma(sigma_soil)
    if s_air == 0 and s_soil == 0:
        return CHANGE_SCORE_HIGH

    pooled_sigma = _pooled_sigma(sigma_air, sigma_soil)
    if pooled_sigma == 0:
        return CHANGE_SCORE_HIGH

    d = abs(float(delta)) / pooled_sigma
    if d < low_threshold:
        return CHANGE_SCORE_LOW
    if d < medium_threshold:
        return CHANGE_SCORE_MEDIUM
    return CHANGE_SCORE_HIGH


def _validate_state_pair(air_df: pd.DataFrame, soil_df: pd.DataFrame):
    air_registers = _register_columns(air_df)
    soil_registers = _register_columns(soil_df)

    if not air_registers:
        raise ValueError("La corrida air no contiene columnas de registros.")

    if air_registers != soil_registers:
        raise ValueError("Las corridas air y soil no monitorean los mismos registros.")

    if "slave_id" in air_df.columns and "slave_id" in soil_df.columns:
        air_slaves = set(air_df["slave_id"].dropna().unique().tolist())
        soil_slaves = set(soil_df["slave_id"].dropna().unique().tolist())
        if air_slaves != soil_slaves:
            raise ValueError("Las corridas pertenecen a esclavos distintos.")


def load_state_pair(
    database_path: str | Path,
    air_run: str,
    soil_run: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(database_path) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    for run_name in (air_run, soil_run):
        if run_name not in existing_tables:
            raise ValueError(
                f"La tabla '{run_name}' no existe en la base de datos. "
                "Verifica el nombre con 'list-runs'."
            )

    air_df = load_run_dataframe(database_path, air_run)
    soil_df = load_run_dataframe(database_path, soil_run)
    _validate_state_pair(air_df, soil_df)
    return air_df, soil_df


def build_variability_report(
    air_df: pd.DataFrame,
    soil_df: pd.DataFrame,
) -> pd.DataFrame:
    _validate_state_pair(air_df, soil_df)

    rows: list[dict[str, object]] = []
    for column in _register_columns(air_df):
        try:
            register = int(column.removeprefix("register_"))
        except ValueError:
            continue
        air = pd.to_numeric(air_df[column], errors="coerce").dropna()
        soil = pd.to_numeric(soil_df[column], errors="coerce").dropna()

        if air.empty or soil.empty:
            rows.append(
                {
                    "register": register,
                    "n_air": len(air),
                    "n_soil": len(soil),
                    "mean_air": float(air.mean()) if not air.empty else float("nan"),
                    "mean_soil": float(soil.mean()) if not soil.empty else float("nan"),
                    "delta": float("nan"),
                    "sigma_air": float("nan"),
                    "sigma_soil": float("nan"),
                    "pooled_sigma": float("nan"),
                    "d": float("nan"),
                    "change_score": CHANGE_SCORE_NONE,
                }
            )
            continue

        mean_air = float(air.mean())
        mean_soil = float(soil.mean())
        sigma_air = air.std(ddof=1)
        sigma_soil = soil.std(ddof=1)
        delta = mean_soil - mean_air

        rows.append(
            {
                "register": register,
                "n_air": len(air),
                "n_soil": len(soil),
                "mean_air": mean_air,
                "mean_soil": mean_soil,
                "delta": delta,
                "sigma_air": sigma_air,
                "sigma_soil": sigma_soil,
                "pooled_sigma": _pooled_sigma(sigma_air, sigma_soil),
                "d": effect_size(delta, sigma_air, sigma_soil),
                "change_score": classify_change_score(delta, sigma_air, sigma_soil),
            }
        )

    report = pd.DataFrame(rows)
    if not report.empty:
        report = report.sort_values(by="d", ascending=False).reset_index(drop=True)

    return report


def _format_number(value, force_sign: bool = False) -> str:
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    if value is None or pd.isna(value):
        return "n/a"
    if isinstance(value, int):
        return f"{value:+d}" if force_sign else f"{value:d}"

    magnitude = abs(float(value))
    decimals = 1 if magnitude >= 100 else 2
    text = f"{value:,.{decimals}f}".rstrip("0").rstrip(".")
    if force_sign and text and not text.startswith("-"):
        text = "+" + text
    return text


def render_variability_report(
    report: pd.DataFrame,
    top_n: int | None = None,
    show_all: bool = False,
) -> str:
    if report is None or report.empty:
        return "No se encontraron registros comparables entre las corridas."

    display = report
    if not show_all:
        display = display[display["change_score"] != CHANGE_SCORE_NONE]
        if display.empty:
            return "No se detectaron cambios significativos entre los estados air y soil."

    if top_n is not None and top_n > 0:
        display = display.head(top_n)

    changed = int((report["change_score"] != CHANGE_SCORE_NONE).sum())
    lines = [
        f"Registros analizados: {len(report)} | Con cambio: {changed}",
        "",
    ]

    for _, row in display.iterrows():
        lines.append(f"Register {row['register']}")
        lines.append("-" * 16)
        lines.append(f"{'Muestras air:':>14} {row['n_air']}")
        lines.append(f"{'Muestras soil:':>14} {row['n_soil']}")
        lines.append(f"{'Mean air:':>14} {_format_number(row['mean_air'])}")
        lines.append(f"{'Mean soil:':>14} {_format_number(row['mean_soil'])}")
        lines.append(f"{'Δ:':>14} {_format_number(row['delta'], force_sign=True)}")
        lines.append(f"{'σ air:':>14} {_format_number(row['sigma_air'])}")
        lines.append(f"{'σ soil:':>14} {_format_number(row['sigma_soil'])}")
        lines.append(f"{'Change score:':>14} {row['change_score']}")
        lines.append("")

    return "\n".join(lines)


def export_variability_csv(report: pd.DataFrame, path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(output_path, index=False)
    return output_path