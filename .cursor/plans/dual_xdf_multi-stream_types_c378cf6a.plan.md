---
name: Dual XDF multi-stream types
overview: Extend [tests/test_dual_xdf_recording.py](tests/test_dual_xdf_recording.py) with additional LSL outlets (periodic multi-channel float32 at a fixed nominal rate, plus one compact non-string type pushed in lockstep with markers), register all test stream names with the Python recorder, and add pyxdf-based comparisons that match the existing philosophy (exact strings; numeric payloads with numpy; timestamps optional/tolerant).
todos:
  - id: outlets
    content: Add TestPeriodicFloatOutlet (float32, fixed srate, thread producer) and TestIrregularInt16Outlet (int16, irregular); centralize TEST_STREAM_NAMES
    status: completed
  - id: dual-run
    content: "Update run_parallel_dual_recording: select all test streams by name; orchestrate float thread + marker/int16 pushes"
    status: completed
  - id: validate-compare
    content: Tune validate_xdf logging per dtype; add numeric stream extract/compare (numpy.allclose on time_series); wire main + exit code
    status: completed
isProject: false
---

# Dual XDF test: additional stream types and comparisons

## Current behavior

- One outlet: [`TestMarkerOutlet`](tests/test_dual_xdf_recording.py) — `cf_string`, `IRREGULAR_RATE`, pushes [`TEST_LOG_ENTRIES`](tests/test_dual_xdf_recording.py) with [`PUSH_INTERVAL`](tests/test_dual_xdf_recording.py).
- External C++ LabRecorder: RCS `select all` (already records every resolved stream).
- Internal [`LabRecorder.start_recording(..., streams=...)`](labrecorder/recorder.py): only streams whose `name()` equals `TestMarkerOutlet.STREAM_NAME` are passed today (lines 195–203), so **any new outlet is invisible to the Python recorder unless explicitly included**.

## Proposed stream types

1. **Periodic float32, fixed nominal rate (primary)**  
   - New class e.g. `TestPeriodicFloatOutlet`: `cf_float32`, `channel_count` 3 (matches existing minimal EEG shape in [`tests/test_xdf_roundtrip.py`](tests/test_xdf_roundtrip.py)), `nominal_srate` e.g. `10.0`, distinct `STREAM_NAME` / `SOURCE_ID`.  
   - **Producer**: a daemon thread started immediately before `outlet.push_entries(...)` and stopped **after** `push_entries` returns (event + `join`), so float samples cover the same wall-clock window as marker pushes. Loop: `push_sample([v0,v1,v2], timestamp=pylsl.local_clock())` then `stop_event.wait(1.0 / nominal_srate)` (same pattern as common LSL demos). Use a simple deterministic ramp (e.g. increment a counter per sample) so expected `time_series` rows are reproducible across runs for comparison logic.

2. **Second non-string type (compact, no second thread)**  
   - New outlet e.g. `TestIrregularInt16Outlet`: `cf_int16`, `IRREGULAR_RATE`, 2 channels, unique name.  
   - In the same loop as text markers (extend `push_entries` or add a small orchestrator), push one int16 sample per log line with predictable values (e.g. `[index, len(entry)]` clipped to int16 range). This exercises irregular timing + integer packing in [`SimpleXDFWriter._encode_sample`](labrecorder/xdf/writer.py) alongside strings.

Strings remain the main “slow” stream; floats fill the recording window; int16 adds a second format with trivial overhead.

## Code changes (all in [`tests/test_dual_xdf_recording.py`](tests/test_dual_xdf_recording.py))

| Area | Change |
|------|--------|
| Constants | Centralize `TEST_STREAM_NAMES: FrozenSet[str]` (or tuple of the three `STREAM_NAME` constants). |
| `run_parallel_dual_recording` | Accept the extra outlets / a small “session” object; resolve `target_streams = [s for s in streams if s.name() in TEST_STREAM_NAMES]`; start float producer thread → `push_entries` → signal stop → `join`; keep existing flush/stop order. |
| `validate_xdf` | Per stream: keep detailed per-sample print for the string stream only; for numeric streams print `shape`, `dtype`, and first/last row (avoid flooding the console). |
| Comparison | Keep [`compare_marker_streams`](tests/test_dual_xdf_recording.py) as-is for text. Add helpers e.g. `_load_stream_by_name(path, name) -> (time_series, time_stamps)` using pyxdf, then `compare_numeric_stream(path_a, path_b, name, rtol, atol)` requiring **equal sample count** and `numpy.allclose` on `time_series` (after casting to `float64` for int16 if needed). **Do not require timestamp equality** between internal and external (same rationale as markers); optionally assert counts and payloads only, or use very loose timestamp check if you want a sanity print. |
| `main` | Construct all outlets after the 2 s discovery wait; pass into `run_parallel_dual_recording`; run new comparisons in the Validation section (all must pass for exit 0). |

## Dependencies

- [`numpy`](pyproject.toml) is already a project dependency — use it for `allclose` / array shaping in the test script only.

## Risks / mitigations

- **Stop-time race**: Internal vs external may differ by ±1 sample on the fastest stream. If flaky, widen flush slightly or allow `abs(n_a - n_b) <= 1` only for the periodic float stream (document why). Start with strict equality + existing 1 s flush.  
- **LSL discovery**: After creating new outlets, keep or slightly extend the pre-recording sleep so `find_streams` sees all streams before `start_recording`.

## Non-goals

- No production code changes unless a new bug appears; this is test harness + assertions only.  
- No pytest refactor unless you explicitly want this script converted to `test_*` functions later.
