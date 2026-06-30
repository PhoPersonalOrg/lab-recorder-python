"""Windows LabRecorder service supervisor and tray controller."""

from .rcs_client import RcsClient, parse_recording_path_rcs_response

__all__ = ["RcsClient", "parse_recording_path_rcs_response", "LabRecorderService", "ServiceStatus"]


def __getattr__(name: str):
    if name in ("LabRecorderService", "ServiceStatus"):
        from .lab_recorder_service import LabRecorderService, ServiceStatus
        return LabRecorderService if name == "LabRecorderService" else ServiceStatus
    raise AttributeError(name)
