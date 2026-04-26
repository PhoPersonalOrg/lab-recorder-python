#!/usr/bin/env python3
"""
test_dual_xdf_recording.py — Dual-method XDF recording test (parallel)

Records the same set of text log entries to two .xdf files simultaneously:

1. **External (C++ LabRecorder)**: Launches the C++ LabRecorder binary with a
   dynamically chosen RCS port, then drives it via TCP commands to set the
   filename, discover the test stream, and start recording.
2. **Internal (lab-recorder-python)**: Uses ``LabRecorder`` from this library
   directly as a Python library to find the same stream and write a second XDF
   file in parallel.

Both recorders are active during the same single ``push_entries`` call so that,
in theory, both XDF files contain identical marker data.  Files are validated
with pyxdf after recording and their marker sequences are compared.

Note: byte-for-byte file identity is not guaranteed — see README.md "XDF Parity
Status" for known differences between the Python and C++ implementations.

Usage:
    cd lab-recorder-python
    uv run python tests/test_dual_xdf_recording.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pylsl

from labrecorder.launch_and_control_external_cpp_labrecorder_app import (
    DEFAULT_HOST,
    ExternalLabRecorderInstance,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]  # lab-recorder-python/
OUTPUT_DATA_DIR = REPO_ROOT / "data"
OUTPUT_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEMP_OUTPUT_DIR = OUTPUT_DATA_DIR / "_test_xdf_output"
TEMP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Test data — simple text log entries
# ---------------------------------------------------------------------------
TEST_LOG_ENTRIES: List[str] = [
    "Session started: dual XDF recording test",
    "Participant: test_user_001",
    "Condition: baseline",
    "Event: eyes_open",
    "Event: stimulus_onset_A",
    "Note: participant reported mild fatigue",
    "Event: stimulus_offset_A",
    "Event: eyes_closed",
    "Marker: rest_period_begin",
    "Marker: rest_period_end",
    "Event: stimulus_onset_B",
    "Event: stimulus_offset_B",
    "Session ended: dual XDF recording test",
]

# Interval between pushing samples (seconds)
PUSH_INTERVAL = 0.15


# ---------------------------------------------------------------------------
# LSL outlet — pushes the shared test data
# ---------------------------------------------------------------------------

class TestMarkerOutlet:
    """Creates an LSL string marker outlet and pushes test entries on demand."""

    STREAM_NAME = "TestTextLog"
    STREAM_TYPE = "Markers"
    SOURCE_ID = "test_dual_xdf_001"

    def __init__(self) -> None:
        info = pylsl.StreamInfo(name=self.STREAM_NAME, type=self.STREAM_TYPE, channel_count=1, nominal_srate=pylsl.IRREGULAR_RATE, channel_format=pylsl.cf_string, source_id=self.SOURCE_ID)
        info.desc().append_child_value("manufacturer", "test_dual_xdf_recording")
        info.desc().append_child_value("description", "Simple text log entries for dual-recording test")
        self.outlet = pylsl.StreamOutlet(info)
        self.pushed_timestamps: List[float] = []
        print(f"  [Outlet] Created LSL outlet: {self.STREAM_NAME}")


    def push_entries(self, entries: List[str], interval: float = PUSH_INTERVAL) -> None:
        """Push each entry as a string marker sample with a short delay between."""
        for entry in entries:
            ts = pylsl.local_clock()
            self.outlet.push_sample([entry], timestamp=ts)
            self.pushed_timestamps.append(ts)
            print(f"  [Outlet] Pushed: {entry!r}  (t={ts:.4f})")
            time.sleep(interval)


# ---------------------------------------------------------------------------
# External C++ LabRecorder helpers
# ---------------------------------------------------------------------------

def start_external_cpp_recorder(external_path: Path, exe_path: Path, base_config_path: Path) -> Tuple[Optional[subprocess.Popen], int, Optional[Path]]:
    """Launch the C++ LabRecorder, set filename, discover streams, and start recording.

    Returns ``(proc, rcs_port, temp_config_path)``.  The caller is responsible for
    stopping via RCS and (optionally) terminating the process.  On failure returns
    ``(None, 0, None)`` after printing the error.
    """
    port = ExternalLabRecorderInstance.choose_free_port(DEFAULT_HOST)
    temp_config_path = ExternalLabRecorderInstance.build_temp_config(base_config_path, port)
    try:
        proc = ExternalLabRecorderInstance.launch_labrecorder(exe_path, temp_config_path)
        print(f"  [External] C++ LabRecorder launched (pid={proc.pid}), waiting for RCS on :{port} ...")
        ExternalLabRecorderInstance.wait_for_rcs(DEFAULT_HOST, port, timeout_s=30.0, poll_interval_s=0.5)
        print("  [External] RCS ready.")

        try:
            # C++ LabRecorder rcsUpdateFilename only parses {key:value} tokens; a bare
            # path is silently ignored.  Split into {root:...}{template:...} instead.
            rcs_filename_cmd = f"filename {{root:{external_path.parent.as_posix()}}}{{template:{external_path.name}}}"
            resp = ExternalLabRecorderInstance.send_rcs_command(DEFAULT_HOST, port, rcs_filename_cmd)
            print(f"  [External] filename -> {resp.strip()}")
        except RuntimeError as exc:
            print(f"  [External] WARNING: filename command not acknowledged ({exc}); path may follow config default.")

        resp = ExternalLabRecorderInstance.send_rcs_command(DEFAULT_HOST, port, "update")
        print(f"  [External] update -> {resp.strip()}")
        time.sleep(2.0)  # allow C++ LabRecorder to enumerate available streams

        resp = ExternalLabRecorderInstance.send_rcs_command(DEFAULT_HOST, port, "select all")
        print(f"  [External] select all -> {resp.strip()}")

        resp = ExternalLabRecorderInstance.send_rcs_command(DEFAULT_HOST, port, "start")
        print(f"  [External] start -> {resp.strip()}")
        return proc, port, temp_config_path
    except Exception as exc:
        print(f"  [External] ERROR: {exc}")
        ExternalLabRecorderInstance.cleanup_temp_config(temp_config_path)
        return None, 0, None


def stop_external_cpp_recorder(proc: Optional[subprocess.Popen], rcs_port: int, temp_config_path: Optional[Path], terminate: bool = False) -> None:
    """Send RCS stop, clean up the temp config, and optionally terminate the process."""
    if rcs_port:
        ExternalLabRecorderInstance.request_labrecorder_stop_via_rcs(DEFAULT_HOST, rcs_port)
        print("  [External] RCS stop sent; XDF finalising inside C++ LabRecorder.")
    ExternalLabRecorderInstance.cleanup_temp_config(temp_config_path)
    if terminate and proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5.0)
            print(f"  [External] Process terminated (pid={proc.pid}).")
        except Exception as exc:
            print(f"  [External] Warning: could not terminate process: {exc}")


# ---------------------------------------------------------------------------
# Parallel dual recording — single push feeds both recorders
# ---------------------------------------------------------------------------

def run_parallel_dual_recording(builtin_path: Path, external_path: Path, outlet: TestMarkerOutlet, exe_path: Path, base_config_path: Path) -> Tuple[bool, bool]:
    """Start both recorders, push ``TEST_LOG_ENTRIES`` once, then stop both.

    The external C++ recorder is brought to "recording" first because its startup
    (process launch + RCS negotiation + stream discovery) is slower.  The Python
    recorder is started last, just before the push, so both inlets are open for
    the entire data window.

    Returns ``(internal_ok, external_ok)``.
    """
    from labrecorder import LabRecorder

    print("\n=== Starting External C++ LabRecorder ===")
    print(f"  Executable: {exe_path}")
    print(f"  Config:     {base_config_path}")
    print(f"  Output:     {external_path}")

    proc, rcs_port, temp_cfg = start_external_cpp_recorder(external_path, exe_path, base_config_path)
    external_started = proc is not None

    print("\n=== Starting Internal Python LabRecorder ===")
    print(f"  Output: {builtin_path}")

    recorder = LabRecorder(filename=str(builtin_path), enable_remote_control=False)
    recorder.config.set("streams.watch_for_new_streams", False)
    recorder.config.set("recording.boundary_interval", 60.0)
    recorder.config.set("recording.clock_sync_interval", 60.0)

    streams = recorder.find_streams(timeout=5.0)
    target_streams = [s for s in streams if s.name() == TestMarkerOutlet.STREAM_NAME]
    if not target_streams:
        print("  [Internal] ERROR: Could not find test marker stream!")
        if external_started:
            stop_external_cpp_recorder(proc, rcs_port, temp_cfg, terminate=True)
        return False, False

    recorder.start_recording(filename=str(builtin_path), streams=target_streams)
    print("  [Internal] Recording started.")

    # --- Both recorders are now active — push test data exactly once ---
    print("\n=== Pushing test data (both recorders active) ===")
    outlet.push_entries(TEST_LOG_ENTRIES)

    # Give writer threads time to flush the last samples
    print("  Flushing ...")
    time.sleep(1.0)

    # --- Stop internal recorder ---
    recorder.stop_recording()
    recorder.cleanup()
    internal_ok = builtin_path.exists() and builtin_path.stat().st_size > 0
    if internal_ok:
        print(f"  [Internal] Saved: {builtin_path}  ({builtin_path.stat().st_size} bytes)")
    else:
        print("  [Internal] ERROR: output file missing or empty.")

    # --- Stop external recorder ---
    if external_started:
        stop_external_cpp_recorder(proc, rcs_port, temp_cfg, terminate=False)
        time.sleep(0.5)  # brief wait for C++ writer to finalise
        external_ok = external_path.exists() and external_path.stat().st_size > 0
        if external_ok:
            print(f"  [External] Saved: {external_path}  ({external_path.stat().st_size} bytes)")
        else:
            print("  [External] ERROR: output file missing or empty.")
    else:
        external_ok = False

    return internal_ok, external_ok


# ---------------------------------------------------------------------------
# Validation — read both files with pyxdf and compare
# ---------------------------------------------------------------------------

def validate_xdf(path: Path, label: str) -> bool:
    """Load an XDF file with pyxdf and print summary info."""
    try:
        import pyxdf
    except ImportError:
        print(f"  [SKIP] pyxdf not installed — cannot validate {label}")
        return True  # soft skip

    print(f"\n--- Validating {label}: {path.name} ---")
    if not path.exists():
        print("  ERROR: file does not exist")
        return False

    try:
        streams, header = pyxdf.load_xdf(str(path))
    except Exception as exc:
        print(f"  ERROR loading XDF: {exc}")
        return False

    print(f"  Header: {header}")
    print(f"  Streams found: {len(streams)}")

    for i, stream in enumerate(streams):
        info = stream["info"]
        name = info["name"][0] if "name" in info else "?"
        stype = info["type"][0] if "type" in info else "?"
        n_samples = len(stream["time_stamps"])
        print(f"    Stream {i}: name={name!r}  type={stype!r}  samples={n_samples}")

        if stream["time_series"] is not None:
            for j, (sample, ts) in enumerate(zip(stream["time_series"], stream["time_stamps"])):
                sample_text = sample[0] if isinstance(sample, list) else sample
                print(f"      [{j:3d}] t={float(ts):.4f}  -> {sample_text!r}")

    if len(streams) == 0:
        print("  ERROR: no streams in XDF file")
        return False

    marker_stream = None
    for stream in streams:
        if stream["info"].get("name", [""])[0] == TestMarkerOutlet.STREAM_NAME:
            marker_stream = stream
            break

    if marker_stream is None:
        print(f"  ERROR: could not find {TestMarkerOutlet.STREAM_NAME!r} stream")
        return False

    n = len(marker_stream["time_stamps"])
    expected = len(TEST_LOG_ENTRIES)
    if n != expected:
        print(f"  WARNING: expected {expected} samples, got {n}")
    else:
        print(f"  Sample count matches: {n}")

    return True


def _extract_marker_samples(path: Path) -> Optional[List[str]]:
    """Return the ordered list of string samples from ``TestMarkerOutlet.STREAM_NAME`` in *path*, or ``None`` on failure."""
    try:
        import pyxdf
        streams, _ = pyxdf.load_xdf(str(path))
    except ImportError:
        print("  [Compare] pyxdf not installed — skipping comparison.")
        return None
    except Exception as exc:
        print(f"  [Compare] ERROR loading {path.name}: {exc}")
        return None

    for stream in streams:
        if stream["info"].get("name", [""])[0] == TestMarkerOutlet.STREAM_NAME:
            return [s[0] if isinstance(s, list) else s for s in stream["time_series"]]
    return None


def compare_marker_streams(path_a: Path, label_a: str, path_b: Path, label_b: str) -> bool:
    """Compare the ordered marker string sequences from two XDF files.

    The files are expected to be logically identical (same strings in the same
    order).  Timestamps are intentionally not compared — clock-offset handling
    and chunk timing differ between the Python and C++ implementations.

    Returns ``True`` if sequences match.
    """
    print("\n--- Comparing marker streams ---")
    samples_a = _extract_marker_samples(path_a)
    samples_b = _extract_marker_samples(path_b)

    if samples_a is None:
        print(f"  ERROR: could not extract markers from {label_a} ({path_a.name})")
        return False
    if samples_b is None:
        print(f"  ERROR: could not extract markers from {label_b} ({path_b.name})")
        return False

    print(f"  {label_a}: {len(samples_a)} markers")
    print(f"  {label_b}: {len(samples_b)} markers")

    if len(samples_a) != len(samples_b):
        print(f"  MISMATCH: sample counts differ ({len(samples_a)} vs {len(samples_b)})")
        return False

    mismatches = [(i, a, b) for i, (a, b) in enumerate(zip(samples_a, samples_b)) if a != b]
    if mismatches:
        for i, a, b in mismatches:
            print(f"  MISMATCH at [{i}]: {a!r} vs {b!r}")
        return False

    print(f"  Marker sequences match ({len(samples_a)} samples).")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("  Dual XDF Recording Test (parallel)")
    print(f"  {datetime.now().isoformat()}")
    print("=" * 70)

    exe_path = ExternalLabRecorderInstance.resolve_exe_path()
    base_config_path = ExternalLabRecorderInstance.resolve_config_path()

    if not exe_path.exists():
        print(f"\nERROR: C++ LabRecorder executable not found: {exe_path}")
        print("  Set LABRECORDER_EXE or update DEFAULT_EXE_PATH in launch_and_control_external_cpp_labrecorder_app.py")
        return 1
    if not base_config_path.exists():
        print(f"\nERROR: LabRecorder config not found: {base_config_path}")
        print("  Set LABRECORDER_CONFIG to a valid App-LabRecorder .cfg, or ensure labrecorder/default_external_labrecorder.cfg is present.")
        return 1

    TEMP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    builtin_path = TEMP_OUTPUT_DIR / f"internal_{timestamp}.xdf"
    external_path = TEMP_OUTPUT_DIR / f"external_{timestamp}.xdf"

    print(f"\nOutput directory: {TEMP_OUTPUT_DIR}")
    print(f"  Internal file:  {builtin_path.name}")
    print(f"  External file:  {external_path.name}")
    print(f"\nExecutable: {exe_path}")
    print(f"Config:     {base_config_path}")

    # Create the shared LSL outlet before launching either recorder
    print("\n--- Creating LSL outlet ---")
    outlet = TestMarkerOutlet()

    print("  Waiting for outlet to become discoverable ...")
    time.sleep(2.0)

    # Start both recorders in parallel with a single push
    internal_ok, external_ok = run_parallel_dual_recording(builtin_path, external_path, outlet, exe_path, base_config_path)

    # Validate both files
    print("\n" + "=" * 70)
    print("  Validation")
    print("=" * 70)
    v1 = validate_xdf(builtin_path, "Internal Python LabRecorder") if internal_ok else False
    v2 = validate_xdf(external_path, "External C++ LabRecorder") if external_ok else False

    # Compare marker sequences
    compare_ok = compare_marker_streams(builtin_path, "Internal", external_path, "External") if (v1 and v2) else False

    # Summary
    print("\n" + "=" * 70)
    print("  Summary")
    print("=" * 70)
    print(f"  Internal (Python library):  {'PASS' if (internal_ok and v1) else 'FAIL'}")
    print(f"  External (C++ subprocess):  {'PASS' if (external_ok and v2) else 'FAIL'}")
    print(f"  Marker sequences match:     {'PASS' if compare_ok else ('SKIP' if not (v1 and v2) else 'FAIL')}")
    print(f"\n  Output files in: {TEMP_OUTPUT_DIR}")
    if builtin_path.exists():
        print(f"    {builtin_path.name}  ({builtin_path.stat().st_size} bytes)")
    if external_path.exists():
        print(f"    {external_path.name}  ({external_path.stat().st_size} bytes)")
    print("=" * 70)

    return 0 if (internal_ok and v1 and external_ok and v2 and compare_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
