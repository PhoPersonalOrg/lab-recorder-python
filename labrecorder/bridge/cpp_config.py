"""Config and path resolution for external C++ LabRecorder."""

from __future__ import annotations

import configparser
import os
import socket
import tempfile
from pathlib import Path

from .constants import DEFAULT_CONFIG_PATH, DEFAULT_HOST, DEFAULT_RCS_PORT


def resolve_exe_path(cli_exe: str | None = None) -> Path:
    if cli_exe:
        return Path(cli_exe)
    env_exe = os.environ.get("LABRECORDER_EXE")
    if env_exe:
        return Path(env_exe)
    raise FileNotFoundError("LabRecorder.exe not configured. Set LABRECORDER_EXE or pass --exe.")


def resolve_config_path(cli_config: str | None = None) -> Path:
    if cli_config:
        return Path(cli_config)
    env_config = os.environ.get("LABRECORDER_CONFIG")
    if env_config:
        return Path(env_config)
    return DEFAULT_CONFIG_PATH


def choose_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def build_labrecorder_config(base_config_path: Path, output_config_path: Path, rcs_port: int = DEFAULT_RCS_PORT, rcs_enabled: bool = True, auto_start: bool = False, ephemeral: bool = False) -> Path:
    if not base_config_path.exists():
        raise FileNotFoundError(f"Config file not found: {base_config_path}")
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with base_config_path.open("r", encoding="utf-8") as handle:
        parser.read_file(handle)
    if "General" not in parser:
        parser["General"] = {}
    parser["General"]["RCSEnabled"] = "1" if rcs_enabled else "0"
    parser["General"]["RCSPort"] = str(int(rcs_port))
    parser["General"]["AutoStart"] = "1" if auto_start else "0"
    output_config_path.parent.mkdir(parents=True, exist_ok=True)
    with output_config_path.open("w", encoding="utf-8", newline="\n") as handle:
        parser.write(handle, space_around_delimiters=False)
    if ephemeral:
        return output_config_path
    return output_config_path


def build_temp_config(base_config_path: Path, rcs_port: int) -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="labrecorder_capture_"))
    temp_config_path = temp_dir / base_config_path.name
    return build_labrecorder_config(base_config_path, temp_config_path, rcs_port=rcs_port, ephemeral=True)
