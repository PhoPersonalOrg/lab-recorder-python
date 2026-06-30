"""Client helpers for apps (e.g. PhoLog) to use a running LabRecorderService via RCS."""

from __future__ import annotations

from typing import Optional

from .config import state_path
from .rcs_client import RcsClient
from .state_store import ServiceStateStore


def is_service_available() -> bool:
    state = ServiceStateStore().read()
    return state.status in ("ready", "recording", "restarting") and state.port > 0


def get_service_rcs_client() -> Optional[RcsClient]:
    if not is_service_available():
        return None
    state = ServiceStateStore().read()
    return RcsClient(host=state.host, port=state.port)


def get_state_file_path():
    return state_path()
