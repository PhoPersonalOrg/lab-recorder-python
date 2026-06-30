# LabRecorderService (Windows)

Supervises a single **App-LabRecorder** (`LabRecorder.exe`) instance via TCP **RCS**, with a separate **system tray** controller.

## Components

| Component | Command | Role |
|-----------|---------|------|
| Windows SCM service | `labrecorder-service` | Session-aware supervisor, health/restart loop |
| Tray app | `labrecorder-tray` | Start Menu / notification area control |
| State file | `%ProgramData%\LabRecorderService\state.json` | RCS host/port, PID, status |

## Setup

1. Build App-LabRecorder with `recordingpath` RCS support.
2. Set environment variables:
   - `LABRECORDER_EXE` — path to `LabRecorder.exe`
   - `LABRECORDER_CONFIG` — optional base `.cfg` (defaults to package template)
3. Install (elevated PowerShell):

```powershell
$env:LABRECORDER_EXE = "C:\path\to\LabRecorder.exe"
.\scripts\install_labrecorder_service.ps1
```

Or manually:

```powershell
uv sync --extra service
uv run labrecorder-service install
uv run labrecorder-service start
uv run labrecorder-tray
```

## RCS (port 22345 by default)

Commands match App-LabRecorder: `update`, `select all`, `filename {root:...}{template:...}`, `start`, `stop`, `recordingpath`.

**Note:** C++ LabRecorder always replies `OK` for recognised commands; verify state with `recordingpath` and the tray status.

## PhoLog integration

When `state.json` reports `ready` or `recording`, PhoLog uses RCS instead of an in-process `LabRecorder`.

## Development (no SCM)

```powershell
$env:LABRECORDER_EXE = "C:\path\to\LabRecorder.exe"
uv run labrecorder-service foreground
```
