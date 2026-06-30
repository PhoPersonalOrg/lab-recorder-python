"""CLI entry points for LabRecorder Windows service and tray."""

from __future__ import annotations

import argparse
import sys


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Emotiv LabRecorder Windows service and tray controller.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("install", help="Install the Windows SCM service.")
    sub.add_parser("uninstall", help="Uninstall the Windows SCM service.")
    sub.add_parser("start", help="Start the Windows SCM service.")
    sub.add_parser("stop", help="Stop the Windows SCM service.")
    sub.add_parser("restart", help="Restart the Windows SCM service.")
    fg = sub.add_parser("foreground", help="Run supervisor in foreground (no SCM) for development.")
    fg.add_argument("--port", type=int, default=None, help="RCS port (default 22345).")
    sub.add_parser("tray", help="Run the system tray controller.")
    return parser


def run_service_entry() -> None:
    """Entry point for labrecorder-service: SCM when no args, else CLI subcommands."""
    if sys.platform != "win32":
        print("LabRecorder service requires Windows.", file=sys.stderr)
        raise SystemExit(1)
    argv = sys.argv[1:]
    if not argv:
        from .windows_service import run_windows_service
        run_windows_service()
        return
    run_service(argv)


def run_service(argv: list[str] | None = None) -> None:
    if sys.platform != "win32":
        print("LabRecorder service requires Windows.", file=sys.stderr)
        raise SystemExit(1)
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "tray":
        from .tray_app import run_tray
        run_tray()
        return
    if args.command == "foreground":
        from .config import ServiceSettings
        from .lab_recorder_service import LabRecorderService
        settings = ServiceSettings()
        if args.port is not None:
            settings.rcs_port = int(args.port)
        svc = LabRecorderService(settings=settings)
        try:
            svc.start()
            print(f"LabRecorderService running in foreground on RCS port {settings.rcs_port}. Ctrl+C to stop.")
            import time
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            svc.stop()
        return
    import win32service
    import win32serviceutil
    from .config import SERVICE_NAME
    from .windows_service import LabRecorderWindowsService
    if args.command == "install":
        module_name = LabRecorderWindowsService.__module__ + "." + LabRecorderWindowsService.__name__
        win32serviceutil.InstallService(module_name, SERVICE_NAME, LabRecorderWindowsService._svc_display_name_, startType=win32service.SERVICE_AUTO_START, description=LabRecorderWindowsService._svc_description_)
        print(f"Installed service {SERVICE_NAME}.")
    elif args.command == "uninstall":
        try:
            win32serviceutil.StopService(SERVICE_NAME)
        except Exception:
            pass
        win32serviceutil.RemoveService(SERVICE_NAME)
        print(f"Uninstalled service {SERVICE_NAME}.")
    elif args.command == "start":
        win32serviceutil.StartService(SERVICE_NAME)
    elif args.command == "stop":
        win32serviceutil.StopService(SERVICE_NAME)
    elif args.command == "restart":
        win32serviceutil.RestartService(SERVICE_NAME)
    else:
        from .windows_service import run_windows_service
        run_windows_service()


def run_tray_entry() -> None:
    from .tray_app import run_tray
    run_tray()


if __name__ == "__main__":
    run_service_entry()
