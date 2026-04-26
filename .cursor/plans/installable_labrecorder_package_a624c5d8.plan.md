---
name: Installable labrecorder package
overview: The failure happens because the repo is configured as a uv **virtual project** (no `[build-system]`), so the local `labrecorder` package is never installed into `.venv`. Adding a standard build backend makes `uv sync` perform an editable install and fixes `import labrecorder` for direct script runs and tools.
todos:
  - id: add-build-backend
    content: Add [build-system] + hatchling [tool.hatch.build.targets.wheel] packages = ["labrecorder"] to pyproject.toml
    status: completed
  - id: refresh-lock-sync
    content: Run uv lock && uv sync --all-extras; confirm uv.lock uses editable local project
    status: completed
  - id: verify-imports
    content: Re-run test_dual_xdf_recording.py via venv python and uv run
    status: completed
isProject: false
---

# Fix `ModuleNotFoundError: No module named 'labrecorder'`

## Root cause

[`pyproject.toml`](C:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\lab-recorder-python\pyproject.toml) defines only `[project]` metadata and dependencies. There is **no** `[build-system]`, so uv treats the workspace as a **virtual package** (see [`uv.lock`](C:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\lab-recorder-python\uv.lock) lines 31–34: `source = { virtual = "." }`). In that mode, `uv sync` resolves and installs **third-party** dependencies but does **not** install your own code into `site-packages` (or as an editable link). Running the interpreter with an explicit path to a test file under `tests/` therefore has no `labrecorder` on `sys.path`.

The test’s own usage doc ([`tests/test_dual_xdf_recording.py`](C:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\lab-recorder-python\tests\test_dual_xdf_recording.py) lines 21–23) expects `uv run python tests/test_dual_xdf_recording.py` to work; that also requires the project to be installable.

## Recommended fix

Make the project a normal, installable package by adding a PEP 517 build backend. **Hatchling** is a common, lightweight choice and pairs well with uv.

1. **Extend [`pyproject.toml`](C:\Users\pho\repos\EmotivEpoc\ACTIVE_DEV\lab-recorder-python\pyproject.toml)** with:
   - `[build-system]`  
     - `requires = ["hatchling>=1.24.0"]` (or a pinned minor range you prefer)  
     - `build-backend = "hatchling.build"`
   - `[tool.hatch.build.targets.wheel]`  
     - `packages = ["labrecorder"]`  
     This is explicit and avoids any ambiguity with other top-level folders (e.g. `tests/`, `main.py`).

2. **Refresh the lockfile and environment** (after the edit is applied):
   - `uv lock`
   - `uv sync --all-extras`  
   Confirm `uv.lock` changes the local package from `virtual` to an **editable** local source (uv’s usual behavior for a real project).

3. **Verify** the same command that failed:
   - `.venv/Scripts/python.exe tests/test_dual_xdf_recording.py`  
   and the documented:
   - `uv run python tests/test_dual_xdf_recording.py`

No changes are required inside `labrecorder/` or the test file for imports once the package is installed.

## Optional (out of scope unless you want it)

- Add a `[dependency-groups]` / dev group with `pytest` if you want a single documented command for the whole suite; not required to fix this import error.
