#!/usr/bin/env python3
"""
generate_sequential_debug_xdfs.py — produce three sequential debug XDF files

Runs three recording sessions in sequence, writing one .xdf per session.
Each session opens an IRREGULAR_RATE string LSL outlet, pushes a series of
debug messages spaced ~10 s apart (uniform jitter ±5 s), then saves the
recording and tears down.  A configurable gap (default 90 s) separates
consecutive sessions so the XDF timestamps span clearly distinct intervals.

Example usage (from repo root):
    uv run python scripts/generate_sequential_debug_xdfs.py
    uv run python scripts/generate_sequential_debug_xdfs.py --num-messages 6 --gap-seconds 10 --output-dir data/debug_xdfs
    uv run python scripts/generate_sequential_debug_xdfs.py --seed 42

Parameters:
    --output-dir    Directory for output .xdf files.  Created if absent.
                    Default: data/debug_xdfs relative to repo root.
    --num-messages  Number of debug messages pushed per session.  Default: 12
                    (≈ 2 min per session at 10 s mean spacing).
    --gap-seconds   Wall-clock pause between sessions.  Default: 90.
    --seed          Optional integer seed for the jitter RNG.  Omit for
                    non-deterministic timing.
    --no-validate   Skip post-run pyxdf summary print.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pylsl

# Repo root is one level above this file's parent directory (scripts/)
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from labrecorder import LabRecorder

STREAM_NAME = "SeqDebugText"
STREAM_TYPE = "Markers"
JITTER_LOW = 5.0
JITTER_HIGH = 15.0
OUTLET_SETTLE_S = 1.5
POST_PUSH_FLUSH_S = 1.5


def _make_outlet(file_idx: int) -> pylsl.StreamOutlet:
    source_id = f"seq_debug_xdf_{file_idx}"
    info = pylsl.StreamInfo(name=STREAM_NAME, type=STREAM_TYPE, channel_count=1, nominal_srate=pylsl.IRREGULAR_RATE, channel_format=pylsl.cf_string, source_id=source_id)
    info.desc().append_child_value("description", f"Sequential debug text stream, session {file_idx}")
    now_utc = datetime.now(timezone.utc)
    lsl_offset = pylsl.local_clock()
    phopylsl = info.desc().append_child("phopylslhelper")
    phopylsl.append_child_value("version", "1.0.3")
    phopylsl.append_child_value("stream_start_datetime", now_utc.strftime("%Y-%m-%d %I:%M:%S %p"))
    phopylsl.append_child_value("stream_start_lsl_local_offset_seconds", str(lsl_offset))
    outlet = pylsl.StreamOutlet(info)
    print(f"  [Session {file_idx}] Outlet created  source_id={source_id!r}  stream_start={now_utc.strftime('%Y-%m-%d %I:%M:%S %p')} UTC  lsl_offset={lsl_offset:.3f}")
    return outlet


def _push_messages(outlet: pylsl.StreamOutlet, file_idx: int, num_messages: int, rng: random.Random) -> List[str]:
    pushed: List[str] = []
    for i in range(num_messages):
        msg = f"xdf[{file_idx}]: dbg message {i}"
        ts = pylsl.local_clock()
        outlet.push_sample([msg], timestamp=ts)
        pushed.append(msg)
        print(f"  [Session {file_idx}] [{i+1:>{len(str(num_messages))}}/{num_messages}] t={ts:.3f}  {msg!r}")
        if i < num_messages - 1:
            sleep_s = rng.uniform(JITTER_LOW, JITTER_HIGH)
            time.sleep(sleep_s)
    return pushed


def _run_session(file_idx: int, output_path: Path, num_messages: int, rng: random.Random) -> bool:
    outlet = _make_outlet(file_idx)
    time.sleep(OUTLET_SETTLE_S)

    recorder = LabRecorder(filename=str(output_path), enable_remote_control=False)
    recorder.config.set("streams.watch_for_new_streams", False)
    recorder.config.set("recording.boundary_interval", 60.0)
    recorder.config.set("recording.clock_sync_interval", 60.0)

    streams = recorder.find_streams(timeout=5.0)
    target = [s for s in streams if s.name() == STREAM_NAME and s.source_id() == f"seq_debug_xdf_{file_idx}"]
    if not target:
        print(f"  [Session {file_idx}] ERROR: could not find outlet in LSL resolver — skipping.")
        recorder.cleanup()
        del outlet
        return False

    recorder.start_recording(filename=str(output_path), streams=target)
    print(f"  [Session {file_idx}] Recording started -> {output_path.name}")

    _push_messages(outlet, file_idx, num_messages, rng)
    time.sleep(POST_PUSH_FLUSH_S)

    recorder.stop_recording()
    recorder.cleanup()
    del outlet

    ok = output_path.exists() and output_path.stat().st_size > 0
    if ok:
        print(f"  [Session {file_idx}] Saved: {output_path}  ({output_path.stat().st_size:,} bytes)")
    else:
        print(f"  [Session {file_idx}] ERROR: output file missing or empty.")
    return ok


def _validate_files(paths: List[Path]) -> None:
    try:
        import pyxdf
    except ImportError:
        print("\n[Validate] pyxdf not installed — skipping summary.")
        return

    print("\n=== XDF Summary ===")
    for path in paths:
        if not path.exists():
            print(f"  {path.name}: NOT FOUND")
            continue
        try:
            streams, _ = pyxdf.load_xdf(str(path))
        except Exception as exc:
            print(f"  {path.name}: load error — {exc}")
            continue

        for stream in streams:
            info = stream["info"]
            name = info.get("name", ["?"])[0]
            srate = info.get("nominal_srate", ["?"])[0]
            n = len(stream["time_stamps"])
            ts_arr = stream["time_stamps"]
            series = stream["time_series"]
            first_msg = (series[0][0] if isinstance(series[0], list) else series[0]) if n > 0 else ""
            last_msg = (series[-1][0] if isinstance(series[-1], list) else series[-1]) if n > 0 else ""
            t_span = f"t=[{float(ts_arr[0]):.2f}, {float(ts_arr[-1]):.2f}]" if n > 1 else ""
            print(f"  {path.name}  stream={name!r}  srate={srate}  n={n}  {t_span}")
            print(f"    first: {first_msg!r}")
            print(f"    last:  {last_msg!r}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate three sequential debug XDF files via pylsl + LabRecorder.", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "data" / "debug_xdfs", help="Directory for output .xdf files.")
    parser.add_argument("--num-messages", type=int, default=12, help="Debug messages pushed per session (~10 s spacing).")
    parser.add_argument("--gap-seconds", type=float, default=90.0, help="Pause between sessions (seconds).")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for deterministic jitter.")
    parser.add_argument("--no-validate", action="store_true", help="Skip pyxdf summary after recording.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rng = random.Random(args.seed)

    print(f"Output dir : {output_dir}")
    print(f"Messages   : {args.num_messages} per session")
    print(f"Gap        : {args.gap_seconds} s between sessions")
    print(f"Seed       : {args.seed}")
    print(f"Est. total : ~{3 * args.num_messages * 10 / 60 + 2 * args.gap_seconds / 60:.1f} min\n")

    paths = []
    results = []
    for file_idx in range(3):
        if file_idx > 0:
            print(f"\n--- Gap: sleeping {args.gap_seconds:.0f} s before session {file_idx} ---")
            time.sleep(args.gap_seconds)

        session_dt_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        output_path = output_dir / f"seq_debug_{file_idx}_{session_dt_str}.xdf"
        paths.append(output_path)

        print(f"\n=== Session {file_idx} ===")
        ok = _run_session(file_idx, output_path, args.num_messages, rng)
        results.append(ok)

    print(f"\n=== Done: {sum(results)}/3 sessions succeeded ===")
    for i, (path, ok) in enumerate(zip(paths, results)):
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {path}")

    if not args.no_validate:
        _validate_files(paths)

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
