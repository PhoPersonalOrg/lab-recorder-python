"""System tray controller for LabRecorderService via RCS."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .config import logs_dir, program_data_dir, service_config_path, state_path
from .rcs_client import RcsClient
from .state_store import ServiceStateStore

logger = logging.getLogger(__name__)
REFRESH_WAIT_S = 2.0


class LabRecorderTrayApp:
    def __init__(self):
        self.state_store = ServiceStateStore()
        self._icon = None
        self._stop_refresh = threading.Event()
        self._setup_logging()

    def _setup_logging(self) -> None:
        logs_dir().mkdir(parents=True, exist_ok=True)
        log_file = logs_dir() / "tray.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("labrecorder.service.tray").addHandler(handler)

    def _client(self) -> Optional[RcsClient]:
        state = self.state_store.read()
        if state.port <= 0:
            return None
        return RcsClient(host=state.host, port=state.port)

    def _status_label(self) -> str:
        state = self.state_store.read()
        if state.status == "recording":
            return f"Recording: {state.recording_path or 'active'}"
        if state.status == "waiting_for_user":
            return "Waiting for user login"
        if state.status == "error":
            return f"Error: {state.last_error or 'unknown'}"
        if state.status in ("ready", "restarting"):
            return f"Ready (RCS :{state.port})"
        return f"Status: {state.status}"

    def _on_start_recording(self, icon, item) -> None:
        client = self._client()
        if client is None:
            return
        try:
            client.send_command("update")
            time.sleep(REFRESH_WAIT_S)
            client.send_command("select all")
            client.send_command("start")
            path = client.get_recording_path()
            state = self.state_store.read()
            state.status = "recording"
            state.recording_path = str(path) if path else None
            self.state_store.write(state)
        except Exception as exc:
            logger.exception("Start recording failed: %s", exc)

    def _on_stop_recording(self, icon, item) -> None:
        client = self._client()
        if client is None:
            return
        try:
            client.request_stop()
            state = self.state_store.read()
            state.status = "ready"
            state.recording_path = None
            self.state_store.write(state)
        except Exception as exc:
            logger.exception("Stop recording failed: %s", exc)

    def _on_refresh_streams(self, icon, item) -> None:
        client = self._client()
        if client is None:
            return
        try:
            client.send_command("update")
        except Exception as exc:
            logger.exception("Refresh failed: %s", exc)

    def _on_open_output_folder(self, icon, item) -> None:
        state = self.state_store.read()
        folder = None
        if state.recording_path:
            folder = Path(state.recording_path).parent
        if folder is None or not folder.exists():
            folder = program_data_dir()
        os.startfile(str(folder))

    def _on_edit_config(self, icon, item) -> None:
        cfg = service_config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        if not cfg.exists():
            from labrecorder.bridge import build_labrecorder_config, resolve_config_path
            build_labrecorder_config(resolve_config_path(), cfg)
        os.startfile(str(cfg))

    def _on_restart_labrecorder(self, icon, item) -> None:
        try:
            import win32serviceutil
            from .config import SERVICE_NAME
            win32serviceutil.RestartService(SERVICE_NAME)
        except Exception as exc:
            logger.exception("Restart service failed: %s", exc)

    def _on_exit_tray(self, icon, item) -> None:
        self._stop_refresh.set()
        icon.stop()

    def _build_menu(self):
        import pystray
        return pystray.Menu(
            pystray.MenuItem(lambda item: self._status_label(), None, enabled=False),
            pystray.MenuItem("Start recording", self._on_start_recording),
            pystray.MenuItem("Stop recording", self._on_stop_recording),
            pystray.MenuItem("Refresh streams", self._on_refresh_streams),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Open output folder", self._on_open_output_folder),
            pystray.MenuItem("Edit configuration", self._on_edit_config),
            pystray.MenuItem("Restart LabRecorder", self._on_restart_labrecorder),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit tray", self._on_exit_tray),
        )

    def _refresh_status_loop(self) -> None:
        while not self._stop_refresh.is_set():
            client = self._client()
            if client is not None:
                try:
                    path = client.get_recording_path(timeout_s=2.0)
                    state = self.state_store.read()
                    if path:
                        state.status = "recording"
                        state.recording_path = str(path)
                    elif state.status == "recording":
                        state.status = "ready"
                        state.recording_path = None
                    self.state_store.write(state)
                except Exception:
                    pass
            self._stop_refresh.wait(5.0)

    def run(self) -> None:
        import pystray
        from PIL import Image, ImageDraw
        image = Image.new("RGB", (64, 64), color=(30, 90, 160))
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 12, 52, 52), fill=(220, 220, 255))
        menu = self._build_menu()
        self._icon = pystray.Icon("LabRecorderTray", image, "LabRecorder", menu)
        threading.Thread(target=self._refresh_status_loop, name="TrayStatusRefresh", daemon=True).start()
        self._icon.run()


def run_tray() -> None:
    if sys.platform != "win32":
        raise RuntimeError("LabRecorder tray is only supported on Windows.")
    LabRecorderTrayApp().run()
