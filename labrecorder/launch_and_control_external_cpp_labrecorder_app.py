from __future__ import annotations

"""Run with: uv run python -m labrecorder.launch_and_control_external_cpp_labrecorder_app
Launches LabRecorder in the background with the Emotiv capture config and starts recording all available streams.

After start, the launcher queries RCS ``recordingpath`` and prints the active XDF path. That command is provided
by the App-LabRecorder fork (not stock LabStreamingLayer builds): rebuild App-LabRecorder and set
``LABRECORDER_EXE`` / ``--exe`` to that binary, or path discovery will fail with a clear error.

Default base config is ``labrecorder/default_external_labrecorder.cfg`` (overridable with ``LABRECORDER_CONFIG`` / ``--config``).
"""

import argparse
import configparser
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from labrecorder.bridge import DEFAULT_CONFIG_PATH, DEFAULT_HOST, DEFAULT_RCS_POLL_INTERVAL_S, DEFAULT_RCS_TIMEOUT_S, DEFAULT_STREAM_REFRESH_WAIT_S, build_temp_config, choose_free_port, resolve_config_path, resolve_exe_path
from labrecorder.service.rcs_client import RcsClient, parse_recording_path_rcs_response

logger = logging.getLogger("lab-recorder-python.ExternalCppLabRecorderBridge")


@dataclass(frozen=True)
class LabRecorderCaptureResult:
    process: subprocess.Popen
    recording_path: Path | None
    rcs_port: int
    update_response: str
    start_response: str
    recordingpath_response: str


class ExternalLabRecorderInstance:

    @classmethod
    def resolve_exe_path(cls, cli_exe: str | None = None) -> Path:
        return resolve_exe_path(cli_exe)

    @classmethod
    def resolve_config_path(cls, cli_config: str | None = None) -> Path:
        return resolve_config_path(cli_config)

    @classmethod
    def choose_free_port(cls, host: str = DEFAULT_HOST) -> int:
        return choose_free_port(host)

    @classmethod
    def build_temp_config(cls, base_config_path: Path, rcs_port: int) -> Path:
        return build_temp_config(base_config_path, rcs_port)

    @classmethod
    def launch_labrecorder(cls, exe_path: Path, config_path: Path, extra_args: list[str] | None = None) -> subprocess.Popen:
        if not exe_path.exists():
            raise FileNotFoundError(f"Executable not found: {exe_path}")
        cmd = [str(exe_path), "-c", str(config_path)]
        if extra_args:
            cmd.extend(extra_args)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def wait_for_rcs(cls, host: str, port: int, timeout_s: float, poll_interval_s: float) -> None:
        RcsClient(host=host, port=port).wait_until_ready(timeout_s=timeout_s, poll_interval_s=poll_interval_s)

    @classmethod
    def send_rcs_command(cls, host: str, port: int, command: str, timeout_s: float = 5.0) -> str:
        return RcsClient(host=host, port=port, timeout_s=timeout_s).send_command(command)

    @classmethod
    def request_labrecorder_stop_via_rcs(cls, host: str, port: int, timeout_s: float = 5.0) -> bool:
        return RcsClient(host=host, port=port, timeout_s=timeout_s).request_stop()

    @classmethod
    def parse_recording_path_rcs_response(cls, response: str) -> Path | None:
        return parse_recording_path_rcs_response(response)

    @classmethod
    def run_labrecorder_capture(cls, exe_path: Path, base_config_path: Path, host: str = DEFAULT_HOST, rcs_port: int | None = None, rcs_timeout_s: float = DEFAULT_RCS_TIMEOUT_S, poll_interval_s: float = DEFAULT_RCS_POLL_INTERVAL_S, refresh_wait_s: float = DEFAULT_STREAM_REFRESH_WAIT_S) -> LabRecorderCaptureResult:
        port = int(rcs_port) if rcs_port is not None else cls.choose_free_port(host)
        temp_config_path = cls.build_temp_config(base_config_path, port)
        client = RcsClient(host=host, port=port)
        try:
            proc = cls.launch_labrecorder(exe_path, temp_config_path)
            client.wait_until_ready(timeout_s=rcs_timeout_s, poll_interval_s=poll_interval_s)
            update_response = client.send_command("update")
            time.sleep(max(0.0, refresh_wait_s))
            start_response = client.send_command("start")
            recordingpath_response = client.send_recordingpath()
        finally:
            cls.cleanup_temp_config(temp_config_path)
        recording_path = parse_recording_path_rcs_response(recordingpath_response)
        return LabRecorderCaptureResult(process=proc, recording_path=recording_path, rcs_port=port, update_response=update_response, start_response=start_response, recordingpath_response=recordingpath_response)

    @classmethod
    def cleanup_temp_config(cls, temp_config_path: Path | None) -> None:
        if temp_config_path is None:
            return
        try:
            temp_config_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            temp_config_path.parent.rmdir()
        except OSError:
            pass

    @classmethod
    def start_external_lsl_capture(cls) -> dict:
        startup_status = {"labrecorder_started": False, "labrecorder_exit_code": None, "labrecorder_recording_path": None, "labrecorder_pid": None, "labrecorder_rcs_host": None, "labrecorder_rcs_port": None, "labrecorder_stop_rcs_ok": None}
        try:
            exe_path = cls.resolve_exe_path()
            base_config = cls.resolve_config_path()
            result = cls.run_labrecorder_capture(exe_path=exe_path, base_config_path=base_config)
        except (FileNotFoundError, TimeoutError, RuntimeError, OSError, configparser.Error) as exc:
            logger.error(f"LabRecorder capture failed to start: {exc}")
            startup_status["labrecorder_exit_code"] = 1
            startup_status["labrecorder_started"] = False
            return startup_status
        startup_status["labrecorder_rcs_host"] = DEFAULT_HOST
        startup_status["labrecorder_rcs_port"] = result.rcs_port
        if result.recording_path is None:
            logger.error("LabRecorder did not return an active recording path over RCS. Use an App-LabRecorder build that implements the `recordingpath` command, and point LABRECORDER_EXE at that LabRecorder.exe.")
            logger.error(f"recordingpath RCS response was: {result.recordingpath_response!r}")
            logger.error(f"Launched LabRecorder from {exe_path} (pid={result.process.pid}); start may still have succeeded — check LabRecorder UI.")
            startup_status["labrecorder_exit_code"] = 1
            startup_status["labrecorder_started"] = False
            startup_status["labrecorder_pid"] = result.process.pid
        else:
            resolved_path = result.recording_path.resolve()
            startup_status["labrecorder_recording_path"] = str(resolved_path)
            startup_status["labrecorder_pid"] = result.process.pid
            startup_status["labrecorder_exit_code"] = 0
            startup_status["labrecorder_started"] = True
            logger.info(f"LabRecorder capture started in the background; recording path: {resolved_path}")
        return startup_status


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Launch LabRecorder in the background and immediately start recording all available streams.")
    parser.add_argument("--exe", type=str, default=None, help="Optional path to LabRecorder.exe. Overrides LABRECORDER_EXE.")
    parser.add_argument("--config", type=str, default=None, help="Optional path to the base LabRecorder cfg. Overrides LABRECORDER_CONFIG.")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help="LabRecorder remote-control host.")
    parser.add_argument("--port", type=int, default=None, help="Optional remote-control port. Defaults to an available ephemeral localhost port.")
    parser.add_argument("--rcs-timeout", type=float, default=DEFAULT_RCS_TIMEOUT_S, help="Seconds to wait for LabRecorder's remote-control socket.")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_RCS_POLL_INTERVAL_S, help="Polling interval while waiting for the remote-control socket.")
    parser.add_argument("--refresh-wait", type=float, default=DEFAULT_STREAM_REFRESH_WAIT_S, help="Seconds to wait after sending update before sending start.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        exe_path = ExternalLabRecorderInstance.resolve_exe_path(args.exe)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    base_config_path = ExternalLabRecorderInstance.resolve_config_path(args.config)
    try:
        result = ExternalLabRecorderInstance.run_labrecorder_capture(exe_path=exe_path, base_config_path=base_config_path, host=args.host, rcs_port=args.port, rcs_timeout_s=args.rcs_timeout, poll_interval_s=args.poll_interval, refresh_wait_s=args.refresh_wait)
    except (FileNotFoundError, TimeoutError, RuntimeError, OSError, configparser.Error) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if result.recording_path is None:
        print("LabRecorder did not return an active recording path over RCS.", file=sys.stderr)
        print("Use an App-LabRecorder build that implements the `recordingpath` command, and point LABRECORDER_EXE / --exe at that LabRecorder.exe.", file=sys.stderr)
        print(f"recordingpath RCS response was: {result.recordingpath_response!r}", file=sys.stderr)
        print(f"Launched LabRecorder from {exe_path} (pid={result.process.pid}); start may still have succeeded — check LabRecorder UI.")
        return 1
    print(result.recording_path)
    print(f"Launched LabRecorder in the background from {exe_path} (pid={result.process.pid}).")
    print(f"Base config: {base_config_path}")
    print(f"Temporary remote-control port: {result.rcs_port}")
    print(f"Update response: {result.update_response.strip()}")
    print(f"Start response: {result.start_response.strip()}")
    print(f"Recording path response: {result.recordingpath_response.strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
