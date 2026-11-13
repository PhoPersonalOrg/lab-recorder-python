from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import Any, List, cast

import pylsl

from labrecorder import LabRecorder
from labrecorder.xdf.writer import SimpleXDFWriter
from tests.xdf_test_utils import MINIMAL_MARKER_TIMESTAMPS, MINIMAL_MARKER_WRITER_SAMPLES, build_minimal_stream_infos, pyxdf


def _stream_names(streams: List[dict]) -> set:
    return {stream["info"]["name"][0] for stream in streams}


def _sample_count(stream: dict) -> int:
    series = stream["time_series"]
    return len(series)


@unittest.skipIf(pyxdf is None, "pyxdf is required for attach/rotate validation")
class TestXdfFlushAttachRotate(unittest.TestCase):
    def test_write_chunk_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "flush.xdf"
            writer = SimpleXDFWriter(str(output_path))
            writer.open()
            flush_count = {"n": 0}
            real_flush = writer.file.flush

            def counting_flush() -> None:
                flush_count["n"] += 1
                real_flush()

            writer.file.flush = counting_flush
            _eeg_info, marker_info = build_minimal_stream_infos()
            writer.add_stream(cast(Any, marker_info), stream_key=marker_info.uid())
            before = flush_count["n"]
            writer.write_samples(marker_info.uid(), MINIMAL_MARKER_WRITER_SAMPLES, MINIMAL_MARKER_TIMESTAMPS)
            self.assertGreater(flush_count["n"], before)
            writer.write_stream_footer(marker_info.uid())
            writer.close()

    def test_attach_streams_while_recording(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        name_a = f"AttachRotateA_{suffix}"
        name_b = f"AttachRotateB_{suffix}"
        info_a = pylsl.StreamInfo(name_a, "Markers", 1, 0, "string", f"src_a_{suffix}")
        info_b = pylsl.StreamInfo(name_b, "Markers", 1, 0, "string", f"src_b_{suffix}")
        outlet_a = pylsl.StreamOutlet(info_a)
        time.sleep(1.0)

        recorder = LabRecorder(enable_remote_control=False)
        recorder.config.set("streams.watch_for_new_streams", False)

        with tempfile.TemporaryDirectory() as temp_dir:
            xdf_path = os.path.join(temp_dir, "attach.xdf")
            resolved_a = pylsl.resolve_byprop("name", name_a, timeout=3.0)
            self.assertTrue(resolved_a, "first test stream was not discoverable")
            recorder.start_recording(xdf_path, resolved_a)
            time.sleep(1.0)
            outlet_a.push_sample(["before-attach"])
            time.sleep(0.3)

            outlet_b = pylsl.StreamOutlet(info_b)
            time.sleep(1.0)
            resolved_b = pylsl.resolve_byprop("name", name_b, timeout=3.0)
            self.assertTrue(resolved_b, "second test stream was not discoverable")
            attached = recorder.attach_streams(resolved_b)
            self.assertGreaterEqual(attached, 1)

            time.sleep(0.5)
            outlet_a.push_sample(["after-attach-a"])
            outlet_b.push_sample(["after-attach-b"])
            time.sleep(0.8)

            recorder.stop_recording()
            time.sleep(0.5)

            streams, _header = pyxdf.load_xdf(xdf_path)
            self.assertEqual(_stream_names(streams), {name_a, name_b})
            stream_a = next(s for s in streams if s["info"]["name"][0] == name_a)
            stream_b = next(s for s in streams if s["info"]["name"][0] == name_b)
            self.assertGreaterEqual(_sample_count(stream_a), 2)
            self.assertGreaterEqual(_sample_count(stream_b), 1)

        del outlet_a
        del outlet_b

    def test_rotate_file_writes_two_complete_xdfs(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        name = f"AttachRotateSplit_{suffix}"
        info = pylsl.StreamInfo(name, "Markers", 1, 0, "string", f"src_split_{suffix}")
        outlet = pylsl.StreamOutlet(info)
        time.sleep(1.0)

        recorder = LabRecorder(enable_remote_control=False)
        recorder.config.set("streams.watch_for_new_streams", False)

        with tempfile.TemporaryDirectory() as temp_dir:
            first_path = os.path.join(temp_dir, "part1.xdf")
            second_path = os.path.join(temp_dir, "part2.xdf")
            resolved = pylsl.resolve_byprop("name", name, timeout=3.0)
            self.assertTrue(resolved, "rotate test stream was not discoverable")
            recorder.start_recording(first_path, resolved)
            time.sleep(1.0)

            for idx in range(6):
                outlet.push_sample([f"pre-{idx}"])
                time.sleep(0.08)
            ## END for idx in range(6)....

            time.sleep(0.4)
            recorder.rotate_file(second_path)
            time.sleep(0.3)

            for idx in range(6):
                outlet.push_sample([f"post-{idx}"])
                time.sleep(0.08)
            ## END for idx in range(6)....

            time.sleep(0.5)
            recorder.stop_recording()
            time.sleep(0.5)

            streams1, _h1 = pyxdf.load_xdf(first_path)
            streams2, _h2 = pyxdf.load_xdf(second_path)
            self.assertEqual(_stream_names(streams1), {name})
            self.assertEqual(_stream_names(streams2), {name})
            stream1 = streams1[0]
            stream2 = streams2[0]
            self.assertGreaterEqual(_sample_count(stream1), 1)
            self.assertGreaterEqual(_sample_count(stream2), 1)
            self.assertGreaterEqual(_sample_count(stream1) + _sample_count(stream2), 10)
            self.assertIn("footer", stream1)
            self.assertIn("footer", stream2)

        del outlet

    def test_attach_streams_noop_when_not_recording(self) -> None:
        recorder = LabRecorder(enable_remote_control=False)
        recorder.config.set("streams.watch_for_new_streams", False)
        self.assertEqual(recorder.attach_streams([]), 0)


if __name__ == "__main__":
    unittest.main()
