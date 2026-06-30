"""Process-wide singleton helpers for LabRecorderService."""

from __future__ import annotations

import sys
from typing import Optional


MUTEX_NAME = r"Global\EmotivLabRecorderService"


class ServiceSingletonLock:
    """Named mutex ensuring a single LabRecorderService supervisor."""

    def __init__(self, name: str = MUTEX_NAME):
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        import win32event
        import win32api
        import pywintypes
        try:
            self._handle = win32event.CreateMutex(None, False, self.name)
            last_error = win32api.GetLastError()
            if last_error == 183:
                return False
            return True
        except pywintypes.error:
            return False

    def release(self) -> None:
        if self._handle is not None:
            import win32api
            win32api.CloseHandle(self._handle)
            self._handle = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
