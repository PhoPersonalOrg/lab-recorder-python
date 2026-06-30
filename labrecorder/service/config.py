"""Paths and settings for LabRecorderService."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from labrecorder.bridge import DEFAULT_HOST, DEFAULT_RCS_PORT, resolve_config_path, resolve_exe_path


SERVICE_NAME = "EmotivLabRecorderService"
SERVICE_DISPLAY_NAME = "Emotiv LabRecorder Service"
SERVICE_DESCRIPTION = "Supervises App-LabRecorder for LSL XDF recording via Remote Control Socket."


def program_data_dir() -> Path:
    base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    return Path(base) / "LabRecorderService"


def service_config_path() -> Path:
    return program_data_dir() / "LabRecorder.cfg"


def state_path() -> Path:
    return program_data_dir() / "state.json"


def logs_dir() -> Path:
    return program_data_dir() / "logs"


@dataclass
class ServiceSettings:
    host: str = DEFAULT_HOST
    rcs_port: int = DEFAULT_RCS_PORT
    rcs_timeout_s: float = 30.0
    poll_interval_s: float = 0.5
    health_interval_s: float = 5.0
    restart_backoff_initial_s: float = 2.0
    restart_backoff_max_s: float = 60.0
    base_config_path: Path | None = None
    exe_path: Path | None = None
    minimized_launch: bool = True

    def resolve_exe(self) -> Path:
        if self.exe_path is not None:
            return self.exe_path
        return resolve_exe_path()

    def resolve_base_config(self) -> Path:
        if self.base_config_path is not None:
            return self.base_config_path
        return resolve_config_path()
