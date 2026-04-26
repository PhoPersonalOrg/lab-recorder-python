---
name: Fix string XDF normalization
overview: "The dual-recorder “mismatch” is not two valid XDF encodings: the Python writer incorrectly stringifies whole `list` samples from pylsl for `cf_string` streams. C++ LabRecorder is correct; fix `_normalize_sample` in `SimpleXDFWriter` so one-channel string samples that arrive as `['text']` unpack to the channel string like numeric streams."
todos:
  - id: fix-normalize-sample
    content: In labrecorder/xdf/writer.py _normalize_sample, remove is_string_stream from the first-branch condition so list/tuple samples unpack for cf_string streams.
    status: completed
  - id: regression-test
    content: "Add unit test: write_samples with list-shaped string samples (LSL style), load with pyxdf, assert inner strings match plain text (not repr of list)."
    status: in_progress
isProject: false
---

# Fix Python XDF string samples vs LabRecorder

## What the test actually shows

- **External (C++ LabRecorder):** pyxdf reports each marker as a plain string, e.g. `'Session started: ...'`.
- **Internal (Python):** pyxdf reports each marker as a string that looks like a Python list repr, e.g. `"['Session started: ...']"`.

Timestamps and sample **counts** match; only the **payload** differs. So this is a **serialization bug** in the Python path, not a fundamental format disagreement.

## Root cause (code)

In [`labrecorder/xdf/writer.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/labrecorder/xdf/writer.py), `_encode_sample` calls `_normalize_sample`, which currently does:

```247:260:labrecorder/xdf/writer.py
    def _normalize_sample(self, sample: Any, channel_count: int, is_string_stream: bool) -> List[Any]:
        if channel_count == 1 and (is_string_stream or not isinstance(sample, (list, tuple))):
            values = [sample]
        elif isinstance(sample, (list, tuple)):
            values = list(sample)
        else:
            values = [sample]

        if len(values) != channel_count:
            raise ValueError(f"Expected {channel_count} values but received {len(values)}: {sample}")

        if is_string_stream:
            return ["" if value is None else str(value) for value in values]
```

For **string** streams, `is_string_stream` is always true, so for **any** `sample` (including what pylsl returns for a 1-channel string inlet: **`['Session started: ...']`**), the first branch is taken and `values = [sample]` — i.e. **one “channel” whose value is the whole list**.

The string path then does `str(value)` on that list, producing **`"['Session started: ...']"`**, which is exactly what gets length-prefixed and written as UTF-8 in XDF. LabRecorder writes the **per-channel** string from the LSL binary layout, i.e. the inner `'Session started: ...'`.

**Data path confirmation:** [`labrecorder/streams/acquisition.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/labrecorder/streams/acquisition.py) uses `pull_chunk`; [`labrecorder/recorder.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/labrecorder/recorder.py) buffers `first_sample` from `pull_sample` the same way — both yield list-shaped samples for string markers, consistent with the outlet in [`tests/test_dual_xdf_recording.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/tests/test_dual_xdf_recording.py) (`push_sample([entry], ...)`).

## Why existing round-trip tests did not catch this

[`tests/xdf_test_utils.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/tests/xdf_test_utils.py) passes **`MINIMAL_MARKER_WRITER_SAMPLES`** as **bare strings** into `write_samples` (not list-wrapped). For a bare string, the buggy branch still produces `values = [sample]` and `str('Hello') == 'Hello'`, so [`tests/test_xdf_roundtrip.py`](c:/Users/pho/repos/EmotivEpoc/ACTIVE_DEV/lab-recorder-python/tests/test_xdf_roundtrip.py) stays green while real LSL data fails.

## Recommended fix (minimal, aligned with LabRecorder)

1. **Change `_normalize_sample`** so the “wrap non-list in a single-element list” branch applies only when the sample is **not** already a `list`/`tuple` — i.e. **remove `is_string_stream` from the condition**:

   - Use: `if channel_count == 1 and not isinstance(sample, (list, tuple)): values = [sample]`
   - Keep the `elif isinstance(sample, (list, tuple))` branch for unpacking.

   That matches how 1-channel **numeric** samples are handled when pylsl returns `[float]` vs `float`, and matches what C++ LabRecorder effectively does (one XDF string field per LSL string channel).

2. **Add a regression test** (small, next to existing XDF tests): call `write_samples` for a `cf_string` stream with samples shaped like LSL output, e.g. each sample `['marker text']` (or a short sequence from `MINIMAL_MARKER_SAMPLES`-style rows), then `pyxdf.load_xdf` and assert `time_series[i][0] == 'marker text'` without list repr pollution. Optionally assert dual test parity once the writer is fixed (existing `compare_marker_streams` should then pass).

## Scope / non-goals

- No change to XDF chunk layout, tags, or pyxdf — the binary structure is already consistent; only the **string payload** was wrong.
- No need to change the test’s comparison logic; fixing the writer is the correct fix.
