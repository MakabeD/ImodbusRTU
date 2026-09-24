import pytest

from imodbus_rtu.config import (
    load_config,
    resolve_profile,
    resolve_serial_settings,
)


def write_config(directory, content, name="imodbus.toml"):
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


class TestLoadConfig:
    def test_missing_default_file_is_not_an_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_config() == {}

    def test_missing_explicit_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="config.toml"):
            load_config(tmp_path / "config.toml")

    def test_invalid_toml_raises(self, tmp_path):
        path = write_config(tmp_path, "esto no es [toml")
        with pytest.raises(ValueError, match="TOML invalido"):
            load_config(path)

    def test_valid_toml(self, tmp_path):
        path = write_config(tmp_path, '[serial]\nport = "COM3"\n')
        assert load_config(path) == {"serial": {"port": "COM3"}}


class TestResolveProfile:
    def test_serial_section_only(self):
        settings = resolve_profile({"serial": {"port": "COM3", "baud": 19200}})
        assert settings.port == "COM3"
        assert settings.baud == 19200

    def test_no_config_gives_empty_settings(self):
        settings = resolve_profile({})
        assert settings.port is None
        assert settings.baud is None
        assert settings.timeout is None

    def test_profile_overrides_serial(self):
        config = {
            "serial": {"port": "COM3", "baud": 9600},
            "profiles": {"fast": {"baud": 115200}},
        }
        settings = resolve_profile(config, "fast")
        assert settings.port == "COM3"  # inherited from [serial]
        assert settings.baud == 115200  # overridden by profile

    def test_unknown_profile_lists_available(self):
        config = {"profiles": {"air": {"port": "COM3"}}}
        with pytest.raises(ValueError, match="'soil'.*air"):
            resolve_profile(config, "soil")

    def test_unknown_profile_without_any_profiles(self):
        with pytest.raises(ValueError, match="ninguno"):
            resolve_profile({}, "ghost")

    def test_unknown_keys_rejected(self):
        with pytest.raises(ValueError, match="slave_start"):
            resolve_profile({"serial": {"slave_start": 1}})


class TestResolveSerialSettings:
    def test_built_in_defaults(self):
        port, baud, timeout = resolve_serial_settings(None, None, "COM5", None, None)
        assert (port, baud, timeout) == ("COM5", 9600, 0.2)

    def test_cli_flag_wins_over_everything(self, tmp_path):
        path = write_config(
            tmp_path,
            '[serial]\nport = "COM3"\nbaud = 19200\n\n[profiles.fast]\nbaud = 115200\n',
        )
        port, baud, timeout = resolve_serial_settings(path, "fast", "COM9", 4800, 1.5)
        assert (port, baud, timeout) == ("COM9", 4800, 1.5)

    def test_profile_used_when_no_flag(self, tmp_path):
        path = write_config(
            tmp_path,
            '[serial]\nport = "COM3"\n\n[profiles.fast]\nbaud = 115200\ntimeout = 0.5\n',
        )
        port, baud, timeout = resolve_serial_settings(path, "fast", None, None, None)
        assert (port, baud, timeout) == ("COM3", 115200, 0.5)

    def test_config_serial_used_when_no_profile(self, tmp_path):
        path = write_config(tmp_path, '[serial]\nport = "COM3"\nbaud = 19200\n')
        port, baud, timeout = resolve_serial_settings(path, None, None, None, None)
        assert (port, baud, timeout) == ("COM3", 19200, 0.2)

    def test_missing_port_raises(self, tmp_path):
        path = write_config(tmp_path, "")  # existing but empty config
        with pytest.raises(ValueError, match="Falta el puerto"):
            resolve_serial_settings(path, None, None, None, None)

    def test_explicit_config_file_must_exist(self):
        with pytest.raises(FileNotFoundError):
            resolve_serial_settings("/no/such/file.toml", None, None, None, None)
