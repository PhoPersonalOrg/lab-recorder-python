"""Line-based TCP client for App-LabRecorder Remote Control Socket (RCS)."""

from __future__ import annotations

import socket
import time
from pathlib import Path
from typing import Optional


DEFAULT_HOST = "127.0.0.1"
DEFAULT_RCS_TIMEOUT_S = 30.0
DEFAULT_RCS_POLL_INTERVAL_S = 0.5


def parse_recording_path_rcs_response(response: str) -> Optional[Path]:
    """Parse multi-line ``recordingpath`` response: path line(s) then ``OK``."""
    lines = response.replace("\r\n", "\n").strip().split("\n")
    if not lines or lines[-1].strip() != "OK":
        return None
    if len(lines) < 2:
        return None
    path_line = "\n".join(lines[:-1]).strip()
    if not path_line:
        return None
    return Path(path_line)


class RcsClient:
    """TCP client matching App-LabRecorder newline-delimited RCS protocol."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = 22345, timeout_s: float = 5.0):
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)

    def wait_until_ready(self, timeout_s: float = DEFAULT_RCS_TIMEOUT_S, poll_interval_s: float = DEFAULT_RCS_POLL_INTERVAL_S) -> None:
        deadline = time.time() + max(0.0, timeout_s)
        last_error: Optional[Exception] = None
        while time.time() < deadline:
            try:
                with socket.create_connection((self.host, self.port), timeout=1.0):
                    return
            except OSError as exc:
                last_error = exc
                time.sleep(max(0.05, poll_interval_s))
        raise TimeoutError(f"Timed out waiting for LabRecorder RCS on {self.host}:{self.port}") from last_error

    def send_command(self, command: str, timeout_s: Optional[float] = None) -> str:
        # C++ tcpinterface.cpp always writes OK for recognised commands; do not treat OK as proof of success.
        timeout = self.timeout_s if timeout_s is None else timeout_s
        payload = f"{command}\n".encode("utf-8")
        with socket.create_connection((self.host, self.port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
            return self._read_response(sock, command)

    def send_recordingpath(self, timeout_s: Optional[float] = None) -> str:
        return self.send_command("recordingpath", timeout_s=timeout_s)

    def get_recording_path(self, timeout_s: Optional[float] = None) -> Optional[Path]:
        return parse_recording_path_rcs_response(self.send_recordingpath(timeout_s=timeout_s))

    def request_stop(self, timeout_s: Optional[float] = None) -> bool:
        try:
            self.send_command("stop", timeout_s=timeout_s)
            return True
        except (OSError, RuntimeError):
            return False

    def _read_response(self, sock: socket.socket, command: str) -> str:
        lines: list[str] = []
        while True:
            line = self._read_line(sock)
            if line is None:
                break
            lines.append(line)
            if line.strip() == "OK":
                return "\n".join(lines) + ("\n" if lines else "")
        body = "\n".join(lines)
        if body:
            return body + "\n"
        raise RuntimeError(f"LabRecorder RCS command {command!r} was not acknowledged. Response: {body!r}")

    def _read_line(self, sock: socket.socket) -> Optional[str]:
        buffer = b""
        while True:
            chunk = sock.recv(1)
            if not chunk:
                if buffer:
                    return buffer.decode("utf-8", errors="replace")
                return None
            buffer += chunk
            if buffer.endswith(b"\n"):
                return buffer.decode("utf-8", errors="replace").rstrip("\r\n")
