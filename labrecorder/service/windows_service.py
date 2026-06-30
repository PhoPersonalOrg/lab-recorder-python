"""Windows SCM service wrapper for LabRecorderService."""

from __future__ import annotations

import sys

if sys.platform != "win32":
    raise ImportError("windows_service is only available on Windows")

import servicemanager
import win32event
import win32service
import win32serviceutil

from .config import SERVICE_DESCRIPTION, SERVICE_DISPLAY_NAME, SERVICE_NAME
from .lab_recorder_service import LabRecorderService


class LabRecorderWindowsService(win32serviceutil.ServiceFramework):
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.service = LabRecorderService.get_instance()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        self.service.stop()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE, servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        self.service.start()
        win32event.WaitForSingleObject(self.stop_event, win32event.INFINITE)


def run_windows_service() -> None:
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(LabRecorderWindowsService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(LabRecorderWindowsService)
