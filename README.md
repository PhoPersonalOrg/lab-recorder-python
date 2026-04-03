# Lab Recorder Python

A Python implementation of a Lab Streaming Layer (LSL) recorder that saves multiple data streams to XDF files.

## What it does

Records data from LSL streams (EEG, markers, etc.) to XDF files with proper synchronization. Includes remote control via TCP commands for integration with experiments.

## XDF Parity Status

The Python recorder now targets real XDF 1.0 output that is compatible with the official XDF layout used by `App-LabRecorder` and expected by `pyxdf`.

Current status:
- Writes spec-compliant XDF chunk headers.
- Writes `FileHeader`, `StreamHeader`, `Samples`, `ClockOffset`, `Boundary`, and `StreamFooter` chunks.
- Preserves full LSL stream XML when available from the inlet.
- Tracks `first_timestamp`, `last_timestamp`, `sample_count`, and clock offset history in stream footers.
- Supports both `.xdf` and `.xdfz` output.
- Includes round-trip validation against `pyxdf`.

## Potential Differences From `App-LabRecorder`

This implementation aims for functional parity, but it is not guaranteed to be byte-for-byte identical to the C++ recorder.

Known or likely differences:
- The Python recorder does not reproduce the exact C++ thread structure or internal phase coordination model.
- Late stream attachment is implemented through ongoing discovery plus selected-stream attachment, not the same watchlist query thread model used in the C++ app.
- The Python implementation does not currently mirror every `syncOptions` or GUI-driven behavior from `App-LabRecorder`.
- Exact chunk interleaving during live recording may differ even when the resulting file is structurally valid.
- Desktop application features outside core recording and XDF writing are still narrower than the full C++ app.
- Interoperability is validated against `pyxdf`, but other downstream XDF tools should still be verified in your workflow.

## Installation

```bash
git clone https://github.com/your-username/lab-recorder-python.git
cd lab-recorder-python

# Install dependencies
uv sync --all-extras
```

## Basic Usage

### Simple recording

Start recording from all available streams:

```bash
python main.py -f my_recording.xdf
```

The recorder will:
1. Find all LSL streams on the network
2. Select all of them for recording  
3. Start recording immediately
4. Save to `my_recording.xdf`

Stop with Ctrl+C.

### With remote control

```bash
# Start recorder with remote control enabled
python main.py -f experiment.xdf

# In another terminal, control the recording
python tools/remote_client.py start
python tools/remote_client.py stop
```

### Testing with dummy data

```bash
# Terminal 1: Start test streams
uv run python tools/dummy_sender.py

# Terminal 2: Record the test data  
uv run python main.py -f test.xdf

# Terminal 3: Verify the recording
uv run python tools/inspect_xdf.py test.xdf
```

## Remote Control Commands

Connect to `localhost:22345` and send these commands:

- `status` - Get current recorder state
- `streams` - List available streams
- `start` - Begin recording
- `stop` - Stop recording  
- `select all` - Select all streams
- `filename newname.xdf` - Change output file

## File Structure

```
├── main.py                 # Main application entry point
├── labrecorder/           # Core recorder modules
│   ├── recorder.py        # Main recorder class
│   ├── streams/           # Stream management
│   ├── xdf/              # XDF file writing
│   ├── remote_control/   # TCP remote control
│   └── utils/            # Configuration and utilities
├── tests/                 # Round-trip and validation tests
└── tools/                # Testing and utility scripts
    ├── dummy_sender.py   # Generate test LSL streams
    ├── dummy_receiver.py # Receive dummy LSL streams
    ├── inspect_xdf.py    # Examine XDF files
    └── remote_client.py  # Command-line remote control
```

## Command Line Options

```bash
python main.py [options]

Options:
  -f, --filename FILE     Output XDF filename (default: recording.xdf)
  -p, --port PORT        Remote control port (default: 22345)
  --no-remote           Disable remote control
  --config FILE         Use configuration file
```

## Troubleshooting

**No streams found**: Check that LSL streams are running on your network. Test with `python tools/streamer.py`.

**Port already in use**: Stop other recorder instances with `./cleanup.sh` or kill processes using port 22345.

**Recording issues**: Check that you have write permissions in the output directory and enough disk space.

**Cross-compatibility issues**: Use `uv run python tools/inspect_xdf.py filename.xdf` to inspect the file structure, and run `uv run python -m unittest tests.test_xdf_roundtrip` to verify local `pyxdf` round-trip behavior.

## Requirements

- Python 3.8+
- pylsl (Lab Streaming Layer)
- numpy
- pyxdf (for file verification)


