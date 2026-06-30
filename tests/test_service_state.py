"""Unit tests for service state store."""

import json
import tempfile
from pathlib import Path

from labrecorder.service.state_store import ServiceState, ServiceStateStore


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        store = ServiceStateStore(path)
        store.write(ServiceState(status="ready", port=22345, pid=999))
        loaded = store.read()
        assert loaded.status == "ready"
        assert loaded.port == 22345
        assert loaded.pid == 999
