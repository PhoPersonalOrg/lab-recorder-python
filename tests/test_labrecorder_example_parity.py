import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from labrecorder import LabRecorder
from labrecorder.xdf.inspector import inspect_xdf_file
from tests.xdf_test_utils import FakeStreamInlet, MINIMAL_EEG_SAMPLES, MINIMAL_EEG_TIMESTAMPS, MINIMAL_EEG_WRITER_TIMESTAMPS, MINIMAL_MARKER_SAMPLES, MINIMAL_MARKER_TIMESTAMPS, MINIMAL_MARKER_WRITER_SAMPLES, assert_xdf_matches_fixture, build_minimal_stream_infos, pyxdf


@unittest.skipIf(pyxdf is None, "pyxdf is required for parity validation")
class TestLabRecorderExampleParity(unittest.TestCase):
    def test_labrecorder_produces_minimal_fixture_equivalent_streams(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "labrecorder-minimal.xdf"
            recorder = LabRecorder(filename=str(output_path), enable_remote_control=False)
            recorder.config.set("streams.watch_for_new_streams", False)
            recorder.config.set("recording.boundary_interval", 60.0)
            recorder.config.set("recording.clock_sync_interval", 60.0)
            eeg_info, marker_info = build_minimal_stream_infos()
            fake_inlets = self._build_fake_inlets(eeg_info, marker_info)

            def build_inlet(stream_info, max_buflen=None, recover=None):  # pragma: no cover - exercised via recorder internals
                _ = max_buflen
                _ = recover
                return fake_inlets[stream_info.uid()]


            with mock.patch("labrecorder.recorder.pylsl.StreamInlet", side_effect=build_inlet), mock.patch("labrecorder.recorder.pylsl.local_clock", side_effect=self._build_local_clock_side_effect([5.0, 5.1])):
                recorder.start_recording(streams=[eeg_info, marker_info])
                self._wait_for_inlets(fake_inlets)
                recorder.stop_recording()

            self.assertTrue(inspect_xdf_file(str(output_path)))
            streams, _ = pyxdf.load_xdf(str(output_path))
            eeg_stream = next(stream for stream in streams if stream["info"]["name"][0] == "SendDataC")
            marker_stream = next(stream for stream in streams if stream["info"]["name"][0] == "SendDataString")

            self.assertEqual(eeg_stream["time_series"].tolist(), MINIMAL_EEG_SAMPLES)
            self.assertEqual([round(float(value), 6) for value in eeg_stream["time_stamps"]], MINIMAL_EEG_TIMESTAMPS)
            self.assertEqual(marker_stream["time_series"], MINIMAL_MARKER_SAMPLES)
            self.assertEqual([round(float(value), 6) for value in marker_stream["time_stamps"]], MINIMAL_MARKER_TIMESTAMPS)
            self.assertEqual(eeg_stream["footer"]["info"]["sample_count"][0], "9")
            self.assertEqual(marker_stream["footer"]["info"]["sample_count"][0], "9")

            assert_xdf_matches_fixture(self, output_path, include_clock_offsets=False)


    def _build_fake_inlets(self, eeg_info, marker_info):
        return {
            eeg_info.uid(): FakeStreamInlet(stream_info=eeg_info, first_sample=MINIMAL_EEG_SAMPLES[0], first_timestamp=MINIMAL_EEG_WRITER_TIMESTAMPS[0], chunk_batches=[(MINIMAL_EEG_SAMPLES[1:], MINIMAL_EEG_WRITER_TIMESTAMPS[1:])], time_correction_value=-0.1),
            marker_info.uid(): FakeStreamInlet(stream_info=marker_info, first_sample=MINIMAL_MARKER_WRITER_SAMPLES[0], first_timestamp=MINIMAL_MARKER_TIMESTAMPS[0], chunk_batches=[(MINIMAL_MARKER_WRITER_SAMPLES[1:], MINIMAL_MARKER_TIMESTAMPS[1:])], time_correction_value=0.0),
        }


    def _wait_for_inlets(self, fake_inlets, timeout_seconds: float = 2.0) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if all(inlet.is_drained() for inlet in fake_inlets.values()):
                time.sleep(0.1)
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for fake LSL inlets to drain")


    def _build_local_clock_side_effect(self, values):
        remaining_values = list(values)

        def next_value():
            if remaining_values:
                return remaining_values.pop(0)
            return values[-1]

        return next_value
