# Install Emotiv LabRecorder Windows service and Start Menu tray shortcut.
# Run from an elevated PowerShell prompt.

param(
    [string]$LabRecorderExe = $env:LABRECORDER_EXE,
    [string]$LabRecorderConfig = $env:LABRECORDER_CONFIG
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

if (-not $LabRecorderExe) {
    Write-Error "Set LABRECORDER_EXE to your App-LabRecorder LabRecorder.exe path before installing."
}

Push-Location $RepoRoot
try {
    uv sync --extra service
    if ($LabRecorderExe) { $env:LABRECORDER_EXE = $LabRecorderExe }
    if ($LabRecorderConfig) { $env:LABRECORDER_CONFIG = $LabRecorderConfig }
    uv run labrecorder-service install
    uv run labrecorder-service start

    $TrayExe = Join-Path $RepoRoot ".venv\Scripts\labrecorder-tray.exe"
    if (-not (Test-Path $TrayExe)) { $TrayExe = "labrecorder-tray" }
    $StartMenu = [Environment]::GetFolderPath("Programs")
    $ShortcutPath = Join-Path $StartMenu "LabRecorder Tray.lnk"
    $Wsh = New-Object -ComObject WScript.Shell
    $Shortcut = $Wsh.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TrayExe
    $Shortcut.WorkingDirectory = $RepoRoot
    $Shortcut.Description = "Control Emotiv LabRecorder recording service"
    $Shortcut.Save()
    Write-Host "Installed service and Start Menu shortcut: $ShortcutPath"
}
finally {
    Pop-Location
}
