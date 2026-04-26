---
name: Fix LabRecorder launcher file
overview: Repair broken references in the external C++ LabRecorder bridge (classmethod call, wrong module name, free functions in `main`), remove the dead `run_main` orchestrator block and tidy imports that only served it—without changing behavior of the LabRecorder RCS flow itself.
todos:
  - id: fix-cls-rcs-stop
    content: Fix request_labrecorder_stop_via_rcs to call cls.send_rcs_command
    status: completed
  - id: fix-start-status-host
    content: Replace launch_labrecorder_capture.DEFAULT_HOST with DEFAULT_HOST; soften error log
    status: completed
  - id: fix-main-delegation
    content: Wire main() to ExternalLabRecorderInstance.resolve_* and run_labrecorder_capture
    status: completed
  - id: remove-run-main
    content: Delete run_main method entirely
    status: completed
  - id: imports-docstrings
    content: Drop duplicate/unused imports; fix module + main docstrings minimally
    status: completed
isProject: false
---

# Fix external LabRecorder launcher module

## Root cause

[`labrecorder/launch_and_control_external_cpp_labrecorder_app.py`](C:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\lab-recorder-python\labrecorder\launch_and_control_external_cpp_labrecorder_app.py) mixes two origins: a coherent `ExternalLabRecorderInstance` + `parse_args` / `main` CLI, and a pasted **`run_main`** body from another app (Emotiv orchestrator). The latter references symbols that **do not exist** in this file (`_parse_args`, `CAPTURE_MODE_*`, `stats`, `ScreenshotHelper`, `start_external_lsl_capture` as a global, `signal`, `json`, `run_dashboard`, `_shutdown_event`, etc.), so it is dead/broken code unrelated to launching/controlling the external LabRecorder.

## Targeted fixes (broken variables / calls)

| Location | Issue | Fix |
|----------|--------|-----|
| `request_labrecorder_stop_via_rcs` (~154) | Calls `send_rcs_command(...)` as if it were a module function | Use `cls.send_rcs_command(host, port, "stop", timeout_s=timeout_s)` |
| `start_external_lsl_capture` (~231) | `launch_labrecorder_capture.DEFAULT_HOST` is undefined | Use module-level `DEFAULT_HOST` |
| `main()` (~398–401) | `resolve_exe_path`, `resolve_config_path`, `run_labrecorder_capture` are not defined at module scope | Delegate to the class: `ExternalLabRecorderInstance.resolve_exe_path(...)`, `resolve_config_path(...)`, `run_labrecorder_capture(...)` |
| `start_external_lsl_capture` error log (~226) | Text mentions “screenshots and Cortex capture” which this module no longer performs after removing `run_main` | Shorten to a neutral message (e.g. capture failed; no follow-on systems in this module)—one line, no new features |

## Remove unrelated dead code

- **Delete the entire `run_main` method** on `ExternalLabRecorderInstance` (the block starting ~252 through ~373). Nothing else in the repo references it (grep only finds the definition).

## Import / docstring cleanup (only what becomes unused or redundant)

- Remove **duplicate** `import time` and second `from pathlib import Path`.
- Remove imports that exist **only** for `run_main`: `threading`, `datetime`, and `Dict`, `List`, `Optional` from `typing` if no longer referenced.
- Remove `from dotenv import load_dotenv` — `load_dotenv` is never called.
- Optionally fix the **module docstring** run command (~3) to the real module path, e.g. `uv run python -m labrecorder.launch_and_control_external_cpp_labrecorder_app`, and trim the stray **triple-quoted junk** inside `main()`’s docstring (~392–395) to a single normal docstring line (no behavior change).

## Explicitly out of scope (per “change nothing else”)

- Do not alter `pyproject.toml`, default exe/config paths, RCS protocol logic, `LabRecorderCaptureResult`, or CLI flags beyond wiring `main()` to the class.
- Do not add new features (e.g. dotenv loading) unless you ask to expand scope.

## Verification after implementation

- Run: `uv run python -m labrecorder.launch_and_control_external_cpp_labrecorder_app --help` (should import cleanly).
- Optional: `ruff check` / IDE diagnostics on this file only if already part of your workflow.
