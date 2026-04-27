---
name: Add phopylslhelper sync headers
overview: Patch `_make_outlet()` in `scripts/generate_sequential_debug_xdfs.py` to inject the `phopylslhelper` XML block that `PhoPyMNEHelper` / `xdf_files.py` requires before it can load files in full-data mode.
todos:
  - id: patch-make-outlet
    content: In _make_outlet(), add phopylslhelper XML child with version, stream_start_datetime, stream_start_lsl_local_offset_seconds before StreamOutlet is created; add datetime/timezone import
    status: completed
  - id: smoke-verify
    content: Verify imports parse cleanly with --help check after edit
    status: completed
isProject: false
---

# Add phopylslhelper sync metadata to generated XDF streams

## Root cause

`xdf_files.py` line 940 asserts `stream_start_datetime` must exist in the `stream_infos` DataFrame when `should_load_full_file_data=True`. This column is populated by `parse_and_add_lsl_outlet_info_from_desc` reading a **`phopylslhelper`** child element from each stream's `info.desc()` XML. The current generator never adds that element, so the column is absent and the assertion fires.

## What must be written into `info.desc()` XML

Under a child element named **`phopylslhelper`**, three children are required:

- `version` → `"1.0.3"`
- `stream_start_datetime` → `datetime.now(UTC).strftime("%Y-%m-%d %I:%M:%S %p")` — same UTC format as `readable_dt_str` in `PhoPyLSLhelper/src/phopylslhelper/general_helpers.py`
- `stream_start_lsl_local_offset_seconds` → `str(pylsl.local_clock())` at the same instant

This mirrors what `EasyTimeSyncParsingMixin_add_lsl_outlet_info` does, without needing to import `phopylslhelper`.

## Change — `scripts/generate_sequential_debug_xdfs.py`

One function only: **`_make_outlet`**. After `info = pylsl.StreamInfo(...)` and before `pylsl.StreamOutlet(info)`, capture the sync point and append the XML block:

```python
now_utc = datetime.now(timezone.utc)
lsl_offset = pylsl.local_clock()
phopylsl = info.desc().append_child("phopylslhelper")
phopylsl.append_child_value("version", "1.0.3")
phopylsl.append_child_value("stream_start_datetime", now_utc.strftime("%Y-%m-%d %I:%M:%S %p"))
phopylsl.append_child_value("stream_start_lsl_local_offset_seconds", str(lsl_offset))
```

Also add `from datetime import datetime, timezone` to the imports (replacing the bare `import datetime` that would otherwise be needed).

No other files touched. No `phopylslhelper` import required.
