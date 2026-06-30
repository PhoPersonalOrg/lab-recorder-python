"""Supervise a single C++ LabRecorder.exe process with RCS health checks."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

from labrecorder.bridge import build_labrecorder_config
from .config import ServiceSettings, logs_dir, program_data_dir, service_config_path
from .rcs_client import RcsClient
from .state_store import ServiceState, ServiceStateStore
from .user_session import get_active_console_session_id, launch_in_user_session

logger = logging.getLogger(__name__)


class CppSupervisor:
    def __init__(self, settings: ServiceSettings, state_store: Optional[ServiceStateStore] = None, on_state_change: Optional[Callable[[ServiceState], None]] = None):
        self.settings = settings
        self.state_store = state_store or ServiceStateStore()
        self.on_state_change = on_state_change
        self._process: Optional[subprocess.Popen] = None
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None
        self._restart_backoff_s = settings.restart_backoff_initial_s
        self._lock = threading.Lock()

    @property
    def rcs_client(self) -> RcsClient:
        return RcsClient(host=self.settings.host, port=self.settings.rcs_port, timeout_s=5.0)

    def start_supervision(self) -> None:
        self._stop_event.clear()
        self._health_thread = threading.Thread(target=self._health_loop, name="CppSupervisorHealth", daemon=True)
        self._health_thread.start()

    def stop_supervision(self) -> None:
        self._stop_event.set()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=10.0)
        self.shutdown_process()

    def ensure_running(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return
            session_id = get_active_console_session_id()
            if session_id is None or session_id == 0xFFFFFFFF:
                self._update_state(status="waiting_for_user", last_error="No interactive user session logged in.")
                return
            try:
                self._launch_process()
            except Exception as exc:
                logger.exception("Failed to launch LabRecorder")
                self._update_state(status="error", last_error=str(exc))

    def shutdown_process(self) -> None:
        with self._lock:
            proc = self._process
            self._process = None
        if proc is None:
            self._update_state(status="stopped", pid=None, recording_path=None)
            return
        try:
            self.rcs_client.request_stop()
            time.sleep(1.0)
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=15.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._update_state(status="stopped", pid=None, recording_path=None)

    def restart_process(self) -> None:
        self.shutdown_process()
        self._restart_backoff_s = self.settings.restart_backoff_initial_s
        self.ensure_running()

    def _launch_process(self) -> None:
        exe_path = self.settings.resolve_exe()
        base_config = self.settings.resolve_base_config()
        cfg_path = service_config_path()
        build_labrecorder_config(base_config, cfg_path, rcs_port=self.settings.rcs_port, rcs_enabled=True, auto_start=False)
        args = ["-c", str(cfg_path)]
        if self.settings.minimized_launch:
            args.append("--minimized")
        logs_dir().mkdir(parents=True, exist_ok=True)
        program_data_dir().mkdir(parents=True, exist_ok=True)
        proc = launch_in_user_session(exe_path, args, cwd=program_data_dir())
        self._process = proc
        client = self.rcs_client
        client.wait_until_ready(timeout_s=self.settings.rcs_timeout_s, poll_interval_s=self.settings.poll_interval_s)
        self._restart_backoff_s = self.settings.restart_backoff_initial_s
        self._update_state(status="ready", pid=proc.pid, config_path=str(cfg_path), last_error=None)
        logger.info("LabRecorder started pid=%s port=%s", proc.pid, self.settings.rcs_port)

    def _health_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.ensure_running()
                proc = self._process
                if proc is not None:
                    code = proc.poll()
                    if code is not None:
                        logger.warning("LabRecorder exited with code %s; restarting.", code)
                        self._process = None
                        self._update_state(status="restarting", pid=None, last_error=f"Process exited with code {code}")
                        self._stop_event.wait(self._restart_backoff_s)
                        self._restart_backoff_s = min(self._restart_backoff_s * 2, self.settings.restart_backoff_max_s)
                        continue
                    try:
                        path = self.rcs_client.get_recording_path(timeout_s=3.0)
                        rec = str(path) if path else None
                        self._update_state(status="recording" if rec else "ready", recording_path=rec)
                    except Exception:
                        self._update_state(status="ready", recording_path=None)
            except Exception as exc:
                logger.exception("Health loop error")
                self._update_state(status="error", last_error=str(exc))
            self._stop_event.wait(self.settings.health_interval_s)

    def _update_state(self, **kwargs) -> None:
        state = self.state_store.read()
        state.host = self.settings.host
        state.port = self.settings.rcs_port
        for key, value in kwargs.items():
            if hasattr(state, key):
                setattr(state, key, value)
        self.state_store.write(state)
        if self.on_state_change:
            self.on_state_change(state)
