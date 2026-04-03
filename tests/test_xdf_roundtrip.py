import tempfile
import unittest
from pathlib import Path

from pylsl import cf_float32, cf_string

from labrecorder.xdf.inspector import inspect_xdf_file
from labrecorder.xdf.writer import SimpleXDFWriter

try:
    import pyxdf
except ImportError:  # pragma: no cover - handled via test skip
    pyxdf = None


class DummyStreamInfo:
    def __init__(self, name: str, stream_type: str, channel_count: int, nominal_srate: float, channel_format: int, source_id: str, uid: str):
        self._name = name
        self._type = stream_type
        self._channel_count = channel_count
        self._nominal_srate = nominal_srate
        self._channel_format = channel_format
        self._source_id = source_id
        self._uid = uid


    def as_xml(self) -> str:
        return (
            "<?xml version=\"1.0\"?>"
            "<info>"
            f"<name>{self._name}</name>"
            f"<type>{self._type}</type>"
            f"<channel_count>{self._channel_count}</channel_count>"
            f"<nominal_srate>{self._nominal_srate}</nominal_srate>"
            f"<channel_format>{'string' if self._channel_format == cf_string else 'float32'}</channel_format>"
            f"<source_id>{self._source_id}</source_id>"
            "</info>"
        )


    def name(self) -> str:
        return self._name


    def type(self) -> str:
        return self._type


    def channel_count(self) -> int:
        return self._channel_count


    def nominal_srate(self) -> float:
        return self._nominal_srate


    def channel_format(self) -> int:
        return self._channel_format


    def source_id(self) -> str:
        return self._source_id


    def uid(self) -> str:
        return self._uid


@unittest.skipIf(pyxdf is None, "pyxdf is required for round-trip validation")
class TestXDFRoundTrip(unittest.TestCase):
    def test_writer_produces_pyxdf_readable_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "roundtrip.xdf"
            writer = SimpleXDFWriter(str(output_path))
            eeg_info = DummyStreamInfo("TestEEG", "EEG", 2, 100.0, cf_float32, "eeg-source", "eeg-uid")
            marker_info = DummyStreamInfo("Markers", "Markers", 1, 0.0, cf_string, "marker-source", "marker-uid")

            writer.open()
            writer.add_stream(eeg_info, stream_key=eeg_info.uid())
            writer.add_stream(marker_info, stream_key=marker_info.uid())
            writer.write_clock_offset(eeg_info.uid(), 1.0, 0.001)
            writer.write_boundary_chunk()
            writer.write_samples(eeg_info.uid(), [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], [1.0, 0.0, 1.02])
            writer.write_samples(marker_info.uid(), [["A"], ["B"]], [1.5, 2.5])
            writer.write_stream_footer(eeg_info.uid())
            writer.write_stream_footer(marker_info.uid())
            writer.close()

            self.assertTrue(inspect_xdf_file(str(output_path)))
            streams, header = pyxdf.load_xdf(str(output_path))

            self.assertEqual(len(streams), 2)
            self.assertIn("info", streams[0])
            stream_names = {stream["info"]["name"][0] for stream in streams}
            self.assertEqual(stream_names, {"TestEEG", "Markers"})
            eeg_stream = next(stream for stream in streams if stream["info"]["name"][0] == "TestEEG")
            marker_stream = next(stream for stream in streams if stream["info"]["name"][0] == "Markers")
            self.assertEqual(len(eeg_stream["time_stamps"]), 3)
            self.assertEqual(len(marker_stream["time_stamps"]), 2)


if __name__ == "__main__":
    unittest.main()
