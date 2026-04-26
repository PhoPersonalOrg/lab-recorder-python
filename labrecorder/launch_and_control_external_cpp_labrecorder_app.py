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
import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("lab-recorder-python.ExternalCppLabRecorderBridge")


# DEFAULT_EXE_PATH = Path(r"C:\Users\pho\bin\LabRecorder\LabRecorder.exe")
DEFAULT_EXE_PATH = Path(r"C:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/App-LabRecorder/out/build/win-vs-release-single/Release/LabRecorder.exe")
_DEFAULT_CONFIG_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = _DEFAULT_CONFIG_DIR / "default_external_labrecorder.cfg"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_RCS_TIMEOUT_S = 30.0
DEFAULT_RCS_POLL_INTERVAL_S = 0.5
DEFAULT_STREAM_REFRESH_WAIT_S = 2.0


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
        if cli_exe:
            return Path(cli_exe)
        env_exe = os.environ.get("LABRECORDER_EXE")
        if env_exe:
            return Path(env_exe)
        return DEFAULT_EXE_PATH


    @classmethod
    def resolve_config_path(cls, cli_config: str | None = None) -> Path:
        if cli_config:
            return Path(cli_config)
        env_config = os.environ.get("LABRECORDER_CONFIG")
        if env_config:
            return Path(env_config)
        return DEFAULT_CONFIG_PATH


    @classmethod
    def choose_free_port(cls, host: str = DEFAULT_HOST) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])


    @classmethod
    def build_temp_config(cls, base_config_path: Path, rcs_port: int) -> Path:
        if not base_config_path.exists():
            raise FileNotFoundError(f"Config file not found: {base_config_path}")

        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        with base_config_path.open("r", encoding="utf-8") as handle:
            parser.read_file(handle)

        if "General" not in parser:
            parser["General"] = {}

        parser["General"]["RCSEnabled"] = "1"
        parser["General"]["RCSPort"] = str(rcs_port)
        parser["General"]["AutoStart"] = "0"

        temp_dir = Path(tempfile.mkdtemp(prefix="labrecorder_capture_"))
        temp_config_path = temp_dir / base_config_path.name
        with temp_config_path.open("w", encoding="utf-8", newline="\n") as handle:
            parser.write(handle, space_around_delimiters=False)
        return temp_config_path


    @classmethod
    def launch_labrecorder(cls, exe_path: Path, config_path: Path) -> subprocess.Popen:
        if not exe_path.exists():
            raise FileNotFoundError(f"Executable not found: {exe_path}")
        return subprocess.Popen([str(exe_path), "-c", str(config_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


    @classmethod
    def wait_for_rcs(cls, host: str, port: int, timeout_s: float, poll_interval_s: float) -> None:
        deadline = time.time() + max(0.0, timeout_s)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return
            except OSError as exc:
                last_error = exc
                time.sleep(max(0.05, poll_interval_s))
        raise TimeoutError(f"Timed out waiting for LabRecorder RCS on {host}:{port}") from last_error


    @classmethod
    def send_rcs_command(cls, host: str, port: int, command: str, timeout_s: float = 5.0) -> str:
        payload = f"{command}\n".encode("utf-8")
        with socket.create_connection((host, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(payload)
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(64)
                if not chunk:
                    break
                chunks.append(chunk)
                response = b"".join(chunks).decode("utf-8", errors="replace")
                if "OK" in response:
                    return response
        response = b"".join(chunks).decode("utf-8", errors="replace")
        raise RuntimeError(f"LabRecorder RCS command {command!r} was not acknowledged. Response: {response!r}")


    @classmethod
    def request_labrecorder_stop_via_rcs(cls, host: str, port: int, timeout_s: float = 5.0) -> bool:
        """Send RCS ``stop`` so LabRecorder closes the XDF in-process. Does not terminate LabRecorder."""
        log = logging.getLogger(__name__)
        try:
            cls.send_rcs_command(host, port, "stop", timeout_s=timeout_s)
            log.info("LabRecorder RCS stop acknowledged (XDF finalizes inside LabRecorder).")
            return True
        except (OSError, RuntimeError) as exc:
            log.warning("LabRecorder RCS stop failed for %s:%s: %s", host, port, exc)
            return False



    @classmethod
    def parse_recording_path_rcs_response(cls, response: str) -> Path | None:
        lines = response.replace("\r\n", "\n").strip().split("\n")
        if not lines or lines[-1].strip() != "OK":
            return None
        if len(lines) < 2:
            return None
        path_line = "\n".join(lines[:-1]).strip()
        if not path_line:
            return None
        return Path(path_line)


    @classmethod
    def run_labrecorder_capture(cls, exe_path: Path, base_config_path: Path, host: str = DEFAULT_HOST, rcs_port: int | None = None, rcs_timeout_s: float = DEFAULT_RCS_TIMEOUT_S, poll_interval_s: float = DEFAULT_RCS_POLL_INTERVAL_S, refresh_wait_s: float = DEFAULT_STREAM_REFRESH_WAIT_S) -> LabRecorderCaptureResult:
        port = int(rcs_port) if rcs_port is not None else cls.choose_free_port(host)
        temp_config_path = cls.build_temp_config(base_config_path, port)
        try:
            proc = cls.launch_labrecorder(exe_path, temp_config_path)
            cls.wait_for_rcs(host, port, timeout_s=rcs_timeout_s, poll_interval_s=poll_interval_s)
            update_response = cls.send_rcs_command(host, port, "update")
            time.sleep(max(0.0, refresh_wait_s))
            start_response = cls.send_rcs_command(host, port, "start")
            recordingpath_response = cls.send_rcs_command(host, port, "recordingpath")
        finally:
            cls.cleanup_temp_config(temp_config_path)
        recording_path = cls.parse_recording_path_rcs_response(recordingpath_response)
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
        """Start the unofficial LSL bridge and LabRecorder before worker threads."""
        startup_status = {
            "labrecorder_started": False,
            "labrecorder_exit_code": None,
            "labrecorder_recording_path": None,
            "labrecorder_pid": None,
            "labrecorder_rcs_host": None,
            "labrecorder_rcs_port": None,
            "labrecorder_stop_rcs_ok": None,
        }

        exe_path = cls.resolve_exe_path()
        base_config = cls.resolve_config_path()
        try:
            result = cls.run_labrecorder_capture(exe_path=exe_path, base_config_path=base_config)
        except (FileNotFoundError, TimeoutError, RuntimeError, OSError, configparser.Error) as exc:
            logger.error(f"LabRecorder capture failed to start: {exc}")
            startup_status["labrecorder_exit_code"] = 1
            startup_status["labrecorder_started"] = False
            return startup_status

        startup_status["labrecorder_rcs_host"] = DEFAULT_HOST
        startup_status["labrecorder_rcs_port"] = result.rcs_port
        if result.recording_path is None:
            logger.error(
                "LabRecorder did not return an active recording path over RCS. "
                "Use an App-LabRecorder build that implements the `recordingpath` command, and point LABRECORDER_EXE at that LabRecorder.exe."
            )
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
    """Launch LabRecorder, start recording, and print the active XDF path."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    exe_path = ExternalLabRecorderInstance.resolve_exe_path(args.exe)
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
