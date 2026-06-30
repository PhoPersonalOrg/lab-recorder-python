"""Singleton façade orchestrating CppSupervisor and service state."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional

from .config import ServiceSettings, logs_dir, program_data_dir
from .cpp_supervisor import CppSupervisor
from .singleton import ServiceSingletonLock
from .state_store import ServiceState, ServiceStateStore

logger = logging.getLogger(__name__)


@dataclass
class ServiceStatus:
    running: bool
    state: ServiceState


class LabRecorderService:
    _instance: Optional["LabRecorderService"] = None
    _instance_lock = threading.Lock()

    def __init__(self, settings: Optional[ServiceSettings] = None):
        self.settings = settings or ServiceSettings()
        self.state_store = ServiceStateStore()
        self._supervisor = CppSupervisor(self.settings, self.state_store)
        self._mutex = ServiceSingletonLock()
        self._running = False
        self._setup_logging()

    @classmethod
    def get_instance(cls, settings: Optional[ServiceSettings] = None) -> "LabRecorderService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(settings=settings)
            return cls._instance

    def _setup_logging(self) -> None:
        program_data_dir().mkdir(parents=True, exist_ok=True)
        logs_dir().mkdir(parents=True, exist_ok=True)
        log_file = logs_dir() / "service.log"
        if not any(isinstance(h, logging.FileHandler) and getattr(h, "baseFilename", "") == str(log_file) for h in logger.root.handlers):
            handler = logging.FileHandler(log_file, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            logging.getLogger("labrecorder.service").addHandler(handler)
            logging.getLogger("labrecorder.service").setLevel(logging.INFO)

    def start(self) -> None:
        if self._running:
            return
        if not self._mutex.acquire():
            raise RuntimeError("Another LabRecorderService instance is already running.")
        try:
            self._supervisor.start_supervision()
            self._supervisor.ensure_running()
            self._running = True
            logger.info("LabRecorderService started.")
        except Exception:
            self._mutex.release()
            raise

    def stop(self) -> None:
        if not self._running:
            return
        self._supervisor.stop_supervision()
        self._running = False
        self._mutex.release()
        logger.info("LabRecorderService stopped.")

    def status(self) -> ServiceStatus:
        state = self.state_store.read()
        return ServiceStatus(running=self._running, state=state)

    def restart_labrecorder(self) -> None:
        self._supervisor.restart_process()
