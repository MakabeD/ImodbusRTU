"""Load serial settings from an optional imodbus.toml configuration file.

Precedence: CLI flag > selected profile ([profiles.<name>]) > [serial]
section > built-in defaults.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

DEFAULT_CONFIG_FILE = "imodbus.toml"
DEFAULT_BAUD = 9600
DEFAULT_TIMEOUT = 0.2

VALID_PROFILE_KEYS = {
    "port",
    "baud",
    "timeout",
}


@dataclass(frozen=True)
class SerialSettings:
    port: str | None = None
    baud: int | None = None
    timeout: float | None = None

    @classmethod
    def from_dict(cls, data: dict) -> SerialSettings:
        unknown = set(data) - VALID_PROFILE_KEYS
        if unknown:
            raise ValueError(
                "Claves no reconocidas en la configuracion serial: "
                f"{', '.join(sorted(unknown))}. "
                f"Claves validas: {', '.join(sorted(VALID_PROFILE_KEYS))}."
            )
        return cls(**data)


def load_config(path: str | Path | None = None) -> dict:
    """Read imodbus.toml. A missing default file is not an error."""
    resolved = Path(path) if path is not None else Path(DEFAULT_CONFIG_FILE)
    if not resolved.exists():
        if path is not None:
            raise FileNotFoundError(f"No existe el archivo de configuracion: {resolved}")
        return {}

    try:
        with resolved.open("rb") as file:
            return tomllib.load(file)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"TOML invalido en {resolved}: {error}") from error


def resolve_profile(config: dict, profile_name: str | None = None) -> SerialSettings:
    settings = dict(config.get("serial", {}))
    if profile_name:
        profiles = config.get("profiles", {})
        if profile_name not in profiles:
            available = ", ".join(sorted(profiles)) or "ninguno"
            raise ValueError(
                f"El perfil '{profile_name}' no existe en la configuracion. "
                f"Perfiles disponibles: {available}."
            )
        settings.update(profiles[profile_name])
    return SerialSettings.from_dict(settings)


def resolve_serial_settings(
    config_path: str | Path | None,
    profile_name: str | None,
    port: str | None,
    baud: int | None,
    timeout: float | None,
) -> tuple[str, int, float]:
    """Merge CLI flags with the config file, returning the effective triple."""
    settings = resolve_profile(load_config(config_path), profile_name)

    resolved_port = port if port is not None else settings.port
    resolved_baud = baud if baud is not None else settings.baud or DEFAULT_BAUD
    resolved_timeout = timeout if timeout is not None else settings.timeout or DEFAULT_TIMEOUT

    if not resolved_port:
        raise ValueError("Falta el puerto serial: pasa --port o define port en imodbus.toml.")
    return resolved_port, resolved_baud, resolved_timeout
