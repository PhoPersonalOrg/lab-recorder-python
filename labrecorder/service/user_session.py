"""Launch processes in the active interactive user session (Windows)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def get_active_console_session_id() -> Optional[int]:
    if sys.platform != "win32":
        return None
    import win32ts
    return win32ts.WTSGetActiveConsoleSessionId()


def launch_in_user_session(exe_path: Path, args: List[str], cwd: Optional[Path] = None) -> subprocess.Popen:
    """Launch ``exe_path`` in the active console user's session when running as a service."""
    if sys.platform != "win32":
        cmd = [str(exe_path), *args]
        return subprocess.Popen(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    session_id = get_active_console_session_id()
    if session_id is None or session_id == 0xFFFFFFFF:
        raise RuntimeError("No active console user session; cannot launch LabRecorder.")
    return _launch_win32_session(exe_path, args, session_id, cwd)


def _launch_win32_session(exe_path: Path, args: List[str], session_id: int, cwd: Optional[Path]) -> subprocess.Popen:
    import win32api
    import win32con
    import win32process
    import win32profile
    import win32security
    import win32ts

    user_token = win32ts.WTSQueryUserToken(session_id)
    try:
        env = win32profile.CreateEnvironmentBlock(user_token, False)
        try:
            cmd = " ".join(f'"{a}"' if " " in a else a for a in [str(exe_path), *args])
            startup = win32process.STARTUPINFO()
            startup.dwFlags = win32con.STARTF_USESHOWWINDOW
            startup.wShowWindow = win32con.SW_SHOWMINNOACTIVE
            flags = win32con.CREATE_UNICODE_ENVIRONMENT
            proc_info = win32process.CreateProcessAsUser(
                user_token,
                None,
                cmd,
                None,
                None,
                False,
                flags,
                env,
                str(cwd) if cwd else None,
                startup,
            )
            handle, thread_handle, pid, _ = proc_info
            win32api.CloseHandle(thread_handle)
            win32api.CloseHandle(handle)
            return _PopenFromPid(pid)
        finally:
            win32profile.DestroyEnvironmentBlock(env)
    finally:
        win32security.CloseHandle(user_token)


class _PopenFromPid:
    """Minimal Popen-like wrapper for a process launched via CreateProcessAsUser."""

    def __init__(self, pid: int):
        self.pid = pid
        self.returncode = None

    def poll(self) -> Optional[int]:
        if sys.platform != "win32":
            return None
        import win32api
        import win32con
        import win32process
        try:
            handle = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
            code = win32process.GetExitCodeProcess(handle)
            win32api.CloseHandle(handle)
            if code != 259:
                self.returncode = code
                return code
            return None
        except Exception:
            self.returncode = -1
            return -1

    def terminate(self) -> None:
        if sys.platform != "win32":
            return
        import win32api
        import win32con
        handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, self.pid)
        try:
            win32api.TerminateProcess(handle, 1)
        finally:
            win32api.CloseHandle(handle)

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: Optional[float] = None) -> int:
        import time
        deadline = None if timeout is None else time.time() + timeout
        while True:
            code = self.poll()
            if code is not None:
                return code
            if deadline is not None and time.time() >= deadline:
                raise subprocess.TimeoutExpired(cmd=[], timeout=timeout)
            time.sleep(0.2)
