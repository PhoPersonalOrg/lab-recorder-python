"""Atomic JSON state for tray and automation discovery."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import state_path


@dataclass
class ServiceState:
    status: str = "stopped"
    host: str = "127.0.0.1"
    port: int = 22345
    pid: Optional[int] = None
    config_path: Optional[str] = None
    recording_path: Optional[str] = None
    last_error: Optional[str] = None
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ServiceState":
        return cls(
            status=str(data.get("status", "stopped")),
            host=str(data.get("host", "127.0.0.1")),
            port=int(data.get("port", 22345)),
            pid=data.get("pid"),
            config_path=data.get("config_path"),
            recording_path=data.get("recording_path"),
            last_error=data.get("last_error"),
            updated_at=float(data.get("updated_at", time.time())),
        )


class ServiceStateStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or state_path()

    def read(self) -> ServiceState:
        if not self.path.exists():
            return ServiceState()
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return ServiceState.from_dict(json.load(handle))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return ServiceState()

    def write(self, state: ServiceState) -> None:
        state.updated_at = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(state.to_dict(), indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
