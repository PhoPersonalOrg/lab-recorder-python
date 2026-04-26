import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from labrecorder.xdf.inspector import inspect_xdf_file
from labrecorder.xdf.writer import SimpleXDFWriter
from tests.xdf_test_utils import MINIMAL_EEG_CLOCK_TIMES, MINIMAL_EEG_CLOCK_VALUES, MINIMAL_EEG_SAMPLES, MINIMAL_EEG_TIMESTAMPS, MINIMAL_EEG_WRITER_TIMESTAMPS, MINIMAL_MARKER_SAMPLES, MINIMAL_MARKER_TIMESTAMPS, MINIMAL_MARKER_WRITER_SAMPLES, MINIMAL_REFERENCE_FOOTER_XML, assert_xdf_matches_fixture, build_minimal_stream_infos, pyxdf


@unittest.skipIf(pyxdf is None, "pyxdf is required for round-trip validation")
class TestXDFRoundTrip(unittest.TestCase):
    def test_writer_produces_pyxdf_readable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "roundtrip.xdf"
            self._write_minimal_fixture_equivalent(output_path)

            self.assertTrue(inspect_xdf_file(str(output_path)))
            streams, header = pyxdf.load_xdf(str(output_path))

            self.assertEqual(len(streams), 2)
            self.assertIn("info", streams[0])
            stream_names = {stream["info"]["name"][0] for stream in streams}
            self.assertEqual(stream_names, {"SendDataC", "SendDataString"})
            self.assertIsInstance(header, dict)

            eeg_stream = next(stream for stream in streams if stream["info"]["name"][0] == "SendDataC")
            marker_stream = next(stream for stream in streams if stream["info"]["name"][0] == "SendDataString")

            self.assertEqual(eeg_stream["time_series"].tolist(), MINIMAL_EEG_SAMPLES)
            self.assertEqual([round(float(value), 6) for value in eeg_stream["time_stamps"]], MINIMAL_EEG_TIMESTAMPS)
            self.assertEqual([round(float(value), 6) for value in eeg_stream["clock_times"]], MINIMAL_EEG_CLOCK_TIMES)
            self.assertEqual([round(float(value), 6) for value in eeg_stream["clock_values"]], MINIMAL_EEG_CLOCK_VALUES)
            self.assertEqual(marker_stream["time_series"], MINIMAL_MARKER_SAMPLES)
            self.assertEqual([round(float(value), 6) for value in marker_stream["time_stamps"]], MINIMAL_MARKER_TIMESTAMPS)
            self.assertEqual(eeg_stream["footer"]["info"]["sample_count"][0], "9")
            self.assertEqual(marker_stream["footer"]["info"]["sample_count"][0], "9")
            self.assertEqual(marker_stream["time_series"][0][0], MINIMAL_REFERENCE_FOOTER_XML)

            assert_xdf_matches_fixture(self, output_path, include_clock_offsets=False)


    def test_lsl_list_shaped_string_samples_round_trip(self) -> None:
        """Samples that arrive from pylsl as ['text'] (list-wrapped) must be stored as plain strings in XDF, matching C++ LabRecorder behaviour."""
        lsl_samples = [["Session started"], ["Event: A"], ["Event: B"], ["Session ended"]]
        timestamps = [1.0, 2.0, 3.0, 4.0]

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "lsl_string_regression.xdf"
            _, marker_info = build_minimal_stream_infos()
            writer = SimpleXDFWriter(str(output_path))
            writer.open()
            writer.add_stream(cast(Any, marker_info), stream_key=marker_info.uid())
            writer.write_samples(marker_info.uid(), lsl_samples, timestamps)
            writer.write_stream_footer(marker_info.uid())
            writer.close()

            streams, _ = pyxdf.load_xdf(str(output_path))
            marker_stream = next(s for s in streams if s["info"]["name"][0] == "SendDataString")
            for i, (expected, row) in enumerate(zip(lsl_samples, marker_stream["time_series"])):
                stored = row[0] if isinstance(row, list) else row
                self.assertEqual(stored, expected[0], f"Sample {i}: expected {expected[0]!r}, got {stored!r} (list repr leak)")


    def _write_minimal_fixture_equivalent(self, output_path: Path) -> None:
        writer = SimpleXDFWriter(str(output_path))
        eeg_info, marker_info = build_minimal_stream_infos()

        writer.open()
        writer.add_stream(cast(Any, eeg_info), stream_key=eeg_info.uid())
        writer.add_stream(cast(Any, marker_info), stream_key=marker_info.uid())
        writer.write_clock_offset(eeg_info.uid(), MINIMAL_EEG_CLOCK_TIMES[0], MINIMAL_EEG_CLOCK_VALUES[0])
        writer.write_samples(eeg_info.uid(), MINIMAL_EEG_SAMPLES, MINIMAL_EEG_WRITER_TIMESTAMPS)
        writer.write_samples(marker_info.uid(), MINIMAL_MARKER_WRITER_SAMPLES, MINIMAL_MARKER_TIMESTAMPS)
        writer.write_clock_offset(eeg_info.uid(), MINIMAL_EEG_CLOCK_TIMES[1], MINIMAL_EEG_CLOCK_VALUES[1])
        writer.write_boundary_chunk()
        writer.write_stream_footer(eeg_info.uid(), xml_content=MINIMAL_REFERENCE_FOOTER_XML)
        writer.write_stream_footer(marker_info.uid(), xml_content=MINIMAL_REFERENCE_FOOTER_XML)
        writer.close()


if __name__ == "__main__":
    unittest.main()
