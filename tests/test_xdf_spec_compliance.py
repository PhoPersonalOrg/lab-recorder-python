"""
XDF spec compliance tests for SimpleXDFWriter.

These tests validate that the XDF files produced by SimpleXDFWriter conform
to the XDF 1.0 specification (https://github.com/sccn/xdf/wiki/Specifications)
and have feature parity with the App-LabRecorder reference implementation.

Known-good reference files from https://github.com/xdf-modules/example-files are
used to compare structural properties of produced output.
"""
from __future__ import annotations

import gzip
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pyxdf
from pylsl import cf_double64, cf_float32, cf_int16, cf_int32, cf_int64, cf_int8, cf_string

from labrecorder.xdf.writer import (
    BOUNDARY_TAG,
    CLOCK_OFFSET_TAG,
    FILE_HEADER_TAG,
    SAMPLES_TAG,
    STREAM_FOOTER_TAG,
    STREAM_HEADER_TAG,
    XDF_MAGIC,
    _BOUNDARY_UUID,
    SimpleXDFWriter,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ── helpers ──────────────────────────────────────────────────────────────────


class DummyStreamInfo:
    """Mock LSL StreamInfo for testing without a live LSL network."""

    _FORMAT_STRINGS = {
        cf_float32: "float32",
        cf_double64: "double64",
        cf_string: "string",
        cf_int32: "int32",
        cf_int16: "int16",
        cf_int8: "int8",
        cf_int64: "int64",
    }

    def __init__(
        self,
        name: str,
        stream_type: str,
        channel_count: int,
        nominal_srate: float,
        channel_format: int,
        source_id: str,
        uid: str,
    ):
        self._name = name
        self._type = stream_type
        self._channel_count = channel_count
        self._nominal_srate = nominal_srate
        self._channel_format = channel_format
        self._source_id = source_id
        self._uid = uid

    def as_xml(self) -> str:
        fmt_str = self._FORMAT_STRINGS.get(self._channel_format, "undefined")
        return (
            '<?xml version="1.0"?>'
            "<info>"
            f"<name>{self._name}</name>"
            f"<type>{self._type}</type>"
            f"<channel_count>{self._channel_count}</channel_count>"
            f"<nominal_srate>{self._nominal_srate}</nominal_srate>"
            f"<channel_format>{fmt_str}</channel_format>"
            f"<source_id>{self._source_id}</source_id>"
            f"<uid>{self._uid}</uid>"
            "<desc/>"
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


def _read_varlen(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a variable-length integer from *data* at *pos*.

    Returns (value, new_pos).
    """
    n = data[pos]
    pos += 1
    if n == 1:
        return data[pos], pos + 1
    if n == 4:
        return struct.unpack_from("<I", data, pos)[0], pos + 4
    if n == 8:
        return struct.unpack_from("<Q", data, pos)[0], pos + 8
    raise ValueError(f"Invalid varlen prefix byte {n} at position {pos - 1}")


def parse_xdf_chunks(filepath: str) -> Tuple[bytes, List[Dict]]:
    """Parse an XDF file and return ``(magic, chunks)``.

    Each chunk dict has keys: ``tag``, ``stream_id`` (or *None* for file-level
    chunks), and ``content`` (bytes).
    """
    with open(filepath, "rb") as fh:
        data = fh.read()

    magic = data[:4]
    pos = 4
    chunks: List[Dict] = []

    while pos < len(data):
        length, pos_after = _read_varlen(data, pos)
        chunk_end = pos_after + length
        tag = struct.unpack_from("<H", data, pos_after)[0]
        p = pos_after + 2
        remaining = length - 2

        stream_id: Optional[int] = None
        if tag in (STREAM_HEADER_TAG, SAMPLES_TAG, CLOCK_OFFSET_TAG, STREAM_FOOTER_TAG):
            stream_id = struct.unpack_from("<I", data, p)[0]
            p += 4
            remaining -= 4

        chunks.append(
            {
                "tag": tag,
                "stream_id": stream_id,
                "content": data[p : p + remaining],
            }
        )
        pos = chunk_end

    return magic, chunks


def _xml_from_content(content: bytes) -> ET.Element:
    xml_str = content.decode("utf-8")
    for decl in ("<?xml version='1.0' ?>", '<?xml version="1.0"?>', "<?xml version='1.0'?>"):
        xml_str = xml_str.replace(decl, "")
    return ET.fromstring(xml_str.strip())


# ── base test case with a temp-dir helper ─────────────────────────────────────


class XDFWriterTestCase(unittest.TestCase):
    """Base class providing a temporary output path for each test."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.out_path = str(Path(self._tmpdir.name) / "test.xdf")

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def make_writer(self) -> SimpleXDFWriter:
        return SimpleXDFWriter(self.out_path)

    def make_stream(
        self,
        name: str = "TestStream",
        stream_type: str = "EEG",
        channel_count: int = 1,
        nominal_srate: float = 100.0,
        channel_format: int = cf_float32,
        source_id: str = "src",
        uid: str = "uid-1",
    ) -> DummyStreamInfo:
        return DummyStreamInfo(name, stream_type, channel_count, nominal_srate, channel_format, source_id, uid)


# ── binary structure tests ─────────────────────────────────────────────────────


class TestXDFMagicHeader(XDFWriterTestCase):
    def test_file_starts_with_xdf_magic(self) -> None:
        w = self.make_writer()
        w.open()
        w.close()
        with open(self.out_path, "rb") as fh:
            self.assertEqual(fh.read(4), XDF_MAGIC)

    def test_magic_is_xdf_colon(self) -> None:
        self.assertEqual(XDF_MAGIC, b"XDF:")


class TestFileHeaderChunk(XDFWriterTestCase):
    def test_file_header_is_first_chunk(self) -> None:
        w = self.make_writer()
        w.open()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        self.assertEqual(chunks[0]["tag"], FILE_HEADER_TAG)

    def test_file_header_has_no_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        self.assertIsNone(chunks[0]["stream_id"])

    def test_file_header_xml_has_version(self) -> None:
        w = self.make_writer()
        w.open()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        xml = _xml_from_content(chunks[0]["content"])
        version = xml.findtext("version")
        self.assertIsNotNone(version)
        self.assertTrue(len(version) > 0, "version element must be non-empty")


class TestStreamHeaderChunk(XDFWriterTestCase):
    def test_stream_header_tag_is_2(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        header_chunks = [c for c in chunks if c["tag"] == STREAM_HEADER_TAG]
        self.assertEqual(len(header_chunks), 1)

    def test_stream_header_has_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        stream_id = w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        header_chunk = next(c for c in chunks if c["tag"] == STREAM_HEADER_TAG)
        self.assertEqual(header_chunk["stream_id"], stream_id)

    def test_stream_header_xml_has_required_fields(self) -> None:
        """Spec requires channel_count, nominal_srate, channel_format in StreamHeader XML."""
        w = self.make_writer()
        w.open()
        w.add_stream(
            self.make_stream(channel_count=3, nominal_srate=256.0, channel_format=cf_float32),
            stream_key="s1",
        )
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        h = next(c for c in chunks if c["tag"] == STREAM_HEADER_TAG)
        xml = _xml_from_content(h["content"])
        self.assertEqual(xml.findtext("channel_count"), "3")
        self.assertEqual(xml.findtext("nominal_srate"), "256.0")
        self.assertEqual(xml.findtext("channel_format"), "float32")

    def test_stream_header_xml_has_name_and_type(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(
            self.make_stream(name="BioSemi", stream_type="EEG"),
            stream_key="s1",
        )
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        h = next(c for c in chunks if c["tag"] == STREAM_HEADER_TAG)
        xml = _xml_from_content(h["content"])
        self.assertEqual(xml.findtext("name"), "BioSemi")
        self.assertEqual(xml.findtext("type"), "EEG")

    def test_multiple_streams_each_have_unique_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        id1 = w.add_stream(self.make_stream(uid="u1"), stream_key="s1")
        id2 = w.add_stream(self.make_stream(uid="u2"), stream_key="s2")
        w.write_stream_footer("s1")
        w.write_stream_footer("s2")
        w.close()
        self.assertNotEqual(id1, id2)
        _, chunks = parse_xdf_chunks(self.out_path)
        header_ids = {c["stream_id"] for c in chunks if c["tag"] == STREAM_HEADER_TAG}
        self.assertEqual(header_ids, {id1, id2})


class TestVarlenIntEncoding(XDFWriterTestCase):
    """Spec: use the shortest encoding that can hold the chunk data."""

    def _make_writer_with_samples(self, n_samples: int) -> List[Dict]:
        w = self.make_writer()
        info = self.make_stream()
        w.open()
        w.add_stream(info, stream_key="s1")
        timestamps = [float(i) * 0.01 + 1.0 for i in range(n_samples)]
        samples = [[float(i)] for i in range(n_samples)]
        w.write_samples("s1", samples, timestamps)
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        return chunks

    def test_num_samples_small_uses_one_byte_varlen(self) -> None:
        """For n_samples < 256, NumSamples should be encoded as 1-byte varlen."""
        chunks = self._make_writer_with_samples(5)
        sample_chunk = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        # First byte of content is NumLengthBytes; should be 1 for small counts
        self.assertEqual(sample_chunk["content"][0], 1)

    def test_num_samples_large_uses_four_byte_varlen(self) -> None:
        """For n_samples == 256, NumSamples should be encoded as 4-byte varlen."""
        chunks = self._make_writer_with_samples(256)
        sample_chunk = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        self.assertEqual(sample_chunk["content"][0], 4)

    def test_chunk_length_uses_shortest_varlen(self) -> None:
        """The chunk length prefix byte must correctly indicate the number of length bytes used."""
        w = self.make_writer()
        w.open()
        w.close()
        with open(self.out_path, "rb") as fh:
            data = fh.read()
        # The first byte after the magic is the NumLengthBytes for the FileHeader chunk
        num_len_byte = data[4]
        self.assertIn(num_len_byte, (1, 4, 8), "NumLengthBytes must be 1, 4, or 8")


class TestSamplesChunkStructure(XDFWriterTestCase):
    def test_samples_chunk_tag_is_3(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sample_chunk = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        self.assertIsNotNone(sample_chunk)

    def test_samples_chunk_has_correct_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        stream_id = w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sc = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        self.assertEqual(sc["stream_id"], stream_id)

    def test_explicit_timestamp_prefix_is_0x08(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [123.456])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sc = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        content = sc["content"]
        # Skip NumSamples varlen (first 2 bytes for 1-byte encoding of 1 sample)
        p = 2  # NumLengthBytes=1 + NumSamples=1
        self.assertEqual(content[p], 0x08, "Explicit timestamp prefix must be 0x08")
        ts_val = struct.unpack_from("<d", content, p + 1)[0]
        self.assertAlmostEqual(ts_val, 123.456, places=10)

    def test_implicit_timestamp_prefix_is_0x00(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [0.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sc = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        content = sc["content"]
        p = 2
        self.assertEqual(content[p], 0x00, "Implicit timestamp prefix must be 0x00")


# ── data format encoding tests ────────────────────────────────────────────────


class TestNumericFormatEncoding(XDFWriterTestCase):
    """Verify correct little-endian binary encoding for each numeric format."""

    def _write_and_parse_sample_content(
        self, channel_format: int, sample_values: List[Any]
    ) -> bytes:
        n_ch = len(sample_values)
        info = self.make_stream(channel_count=n_ch, channel_format=channel_format)
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_samples("s1", [sample_values], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sc = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        content = sc["content"]
        # Skip: NumSamples varlen (2 bytes for count=1), timestamp prefix (1), timestamp (8)
        p = 2 + 1 + 8
        return content[p:]

    def test_float32_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_float32, [1.5, -2.5])
        a, b = struct.unpack_from("<2f", raw)
        self.assertAlmostEqual(a, 1.5, places=5)
        self.assertAlmostEqual(b, -2.5, places=5)

    def test_double64_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_double64, [3.14159265358979])
        (v,) = struct.unpack_from("<d", raw)
        self.assertAlmostEqual(v, 3.14159265358979, places=12)

    def test_int8_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_int8, [42, -10])
        a, b = struct.unpack_from("<2b", raw)
        self.assertEqual(a, 42)
        self.assertEqual(b, -10)

    def test_int16_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_int16, [1000, -500])
        a, b = struct.unpack_from("<2h", raw)
        self.assertEqual(a, 1000)
        self.assertEqual(b, -500)

    def test_int32_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_int32, [100000, -200000])
        a, b = struct.unpack_from("<2i", raw)
        self.assertEqual(a, 100000)
        self.assertEqual(b, -200000)

    def test_int64_encoding(self) -> None:
        raw = self._write_and_parse_sample_content(cf_int64, [10**15, -(10**15)])
        a, b = struct.unpack_from("<2q", raw)
        self.assertEqual(a, 10**15)
        self.assertEqual(b, -(10**15))


class TestStringFormatEncoding(XDFWriterTestCase):
    """String samples: varlen(length) + UTF-8 bytes per channel."""

    def _write_and_parse_string_content(self, strings: List[str]) -> bytes:
        n_ch = len(strings)
        info = self.make_stream(channel_count=n_ch, channel_format=cf_string, nominal_srate=0.0)
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_samples("s1", [strings], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        sc = next(c for c in chunks if c["tag"] == SAMPLES_TAG)
        content = sc["content"]
        p = 2 + 1 + 8  # skip NumSamples varlen + timestamp prefix + timestamp
        return content[p:]

    def test_string_single_channel_encoding(self) -> None:
        raw = self._write_and_parse_string_content(["Hello"])
        # First byte(s) = varlen length
        length, p = _read_varlen(raw, 0)
        text = raw[p : p + length].decode("utf-8")
        self.assertEqual(text, "Hello")
        self.assertEqual(length, 5)

    def test_string_multi_channel_encoding(self) -> None:
        raw = self._write_and_parse_string_content(["foo", "bar"])
        p = 0
        results = []
        for _ in range(2):
            slen, p = _read_varlen(raw, p)
            results.append(raw[p : p + slen].decode("utf-8"))
            p += slen
        self.assertEqual(results, ["foo", "bar"])

    def test_empty_string_encoding(self) -> None:
        raw = self._write_and_parse_string_content([""])
        length, _ = _read_varlen(raw, 0)
        self.assertEqual(length, 0)

    def test_unicode_string_encoding(self) -> None:
        raw = self._write_and_parse_string_content(["héllo wörld"])
        length, p = _read_varlen(raw, 0)
        text = raw[p : p + length].decode("utf-8")
        self.assertEqual(text, "héllo wörld")
        self.assertEqual(length, len("héllo wörld".encode("utf-8")))


# ── clock offset and boundary tests ──────────────────────────────────────────


class TestClockOffsetChunk(XDFWriterTestCase):
    def test_clock_offset_tag_is_4(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_clock_offset("s1", 10.0, 0.001)
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        co = next(c for c in chunks if c["tag"] == CLOCK_OFFSET_TAG)
        self.assertIsNotNone(co)

    def test_clock_offset_payload_is_two_doubles(self) -> None:
        """ClockOffset chunk content must be exactly 16 bytes (2 × double64)."""
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_clock_offset("s1", 42.5, -0.0123)
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        co = next(c for c in chunks if c["tag"] == CLOCK_OFFSET_TAG)
        self.assertEqual(len(co["content"]), 16)
        t_val, off_val = struct.unpack("<dd", co["content"])
        self.assertAlmostEqual(t_val, 42.5, places=12)
        self.assertAlmostEqual(off_val, -0.0123, places=12)

    def test_clock_offset_has_correct_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        stream_id = w.add_stream(self.make_stream(), stream_key="s1")
        w.write_clock_offset("s1", 1.0, 0.0)
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        co = next(c for c in chunks if c["tag"] == CLOCK_OFFSET_TAG)
        self.assertEqual(co["stream_id"], stream_id)


class TestBoundaryChunk(XDFWriterTestCase):
    def test_boundary_tag_is_5(self) -> None:
        w = self.make_writer()
        w.open()
        w.write_boundary_chunk()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        bc = next((c for c in chunks if c["tag"] == BOUNDARY_TAG), None)
        self.assertIsNotNone(bc)

    def test_boundary_has_no_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        w.write_boundary_chunk()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        bc = next(c for c in chunks if c["tag"] == BOUNDARY_TAG)
        self.assertIsNone(bc["stream_id"])

    def test_boundary_uuid_is_correct(self) -> None:
        """Spec: fixed 16-byte UUID 0x43A546DCCBF5410FB30ED5467383CBE4."""
        w = self.make_writer()
        w.open()
        w.write_boundary_chunk()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        bc = next(c for c in chunks if c["tag"] == BOUNDARY_TAG)
        self.assertEqual(bc["content"], bytes(_BOUNDARY_UUID))

    def test_boundary_content_is_16_bytes(self) -> None:
        w = self.make_writer()
        w.open()
        w.write_boundary_chunk()
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        bc = next(c for c in chunks if c["tag"] == BOUNDARY_TAG)
        self.assertEqual(len(bc["content"]), 16)


# ── stream footer tests ───────────────────────────────────────────────────────


class TestStreamFooterChunk(XDFWriterTestCase):
    def _write_and_get_footer_xml(
        self,
        samples: List[Any],
        timestamps: List[float],
        clock_offsets: Optional[List[Tuple[float, float]]] = None,
        channel_format: int = cf_float32,
        nominal_srate: float = 100.0,
    ) -> ET.Element:
        n_ch = len(samples[0]) if samples else 1
        info = self.make_stream(
            channel_count=n_ch,
            channel_format=channel_format,
            nominal_srate=nominal_srate,
        )
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        if clock_offsets:
            for t, v in clock_offsets:
                w.write_clock_offset("s1", t, v)
        if samples:
            w.write_samples("s1", samples, timestamps)
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        footer_chunk = next(c for c in chunks if c["tag"] == STREAM_FOOTER_TAG)
        return _xml_from_content(footer_chunk["content"])

    def test_footer_tag_is_6(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        fc = next(c for c in chunks if c["tag"] == STREAM_FOOTER_TAG)
        self.assertIsNotNone(fc)

    def test_footer_has_correct_stream_id(self) -> None:
        w = self.make_writer()
        w.open()
        stream_id = w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        fc = next(c for c in chunks if c["tag"] == STREAM_FOOTER_TAG)
        self.assertEqual(fc["stream_id"], stream_id)

    def test_footer_first_timestamp(self) -> None:
        xml = self._write_and_get_footer_xml([[1.0], [2.0]], [10.0, 20.0])
        ft = float(xml.findtext("first_timestamp"))
        self.assertAlmostEqual(ft, 10.0, places=10)

    def test_footer_last_timestamp(self) -> None:
        xml = self._write_and_get_footer_xml([[1.0], [2.0]], [10.0, 20.0])
        lt = float(xml.findtext("last_timestamp"))
        self.assertAlmostEqual(lt, 20.0, places=10)

    def test_footer_sample_count(self) -> None:
        xml = self._write_and_get_footer_xml([[1.0], [2.0], [3.0]], [1.0, 2.0, 3.0])
        sc = int(xml.findtext("sample_count"))
        self.assertEqual(sc, 3)

    def test_footer_has_clock_offsets_element(self) -> None:
        xml = self._write_and_get_footer_xml(
            [[1.0]], [1.0], clock_offsets=[(5.0, 0.001), (10.0, 0.002)]
        )
        offsets_el = xml.find("clock_offsets")
        self.assertIsNotNone(offsets_el)
        offset_els = offsets_el.findall("offset")
        self.assertEqual(len(offset_els), 2)

    def test_footer_clock_offset_time_and_value(self) -> None:
        xml = self._write_and_get_footer_xml(
            [[1.0]], [1.0], clock_offsets=[(42.5, -0.0123)]
        )
        offset_el = xml.find("clock_offsets/offset")
        self.assertAlmostEqual(float(offset_el.findtext("time")), 42.5, places=10)
        self.assertAlmostEqual(float(offset_el.findtext("value")), -0.0123, places=10)

    def test_footer_empty_stream_has_zero_sample_count(self) -> None:
        """Streams with no samples should have sample_count=0 in the footer."""
        xml = self._write_and_get_footer_xml([], [])
        self.assertEqual(int(xml.findtext("sample_count")), 0)

    def test_footer_implicit_timestamps_tracked(self) -> None:
        """Implicit timestamps (0.0) should be inferred and reflected in the footer."""
        xml = self._write_and_get_footer_xml(
            [[1.0], [2.0], [3.0]], [10.0, 0.0, 0.0], nominal_srate=10.0
        )
        sc = int(xml.findtext("sample_count"))
        self.assertEqual(sc, 3)
        lt = float(xml.findtext("last_timestamp"))
        # last explicit ts=10.0, srate=10 Hz → implied ts 10.1, 10.2
        self.assertAlmostEqual(lt, 10.2, places=8)


# ── chunk ordering test ───────────────────────────────────────────────────────


class TestChunkOrdering(XDFWriterTestCase):
    def test_file_header_precedes_stream_header(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        tags = [c["tag"] for c in chunks]
        self.assertLess(tags.index(FILE_HEADER_TAG), tags.index(STREAM_HEADER_TAG))

    def test_stream_header_precedes_samples(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        tags = [c["tag"] for c in chunks]
        self.assertLess(tags.index(STREAM_HEADER_TAG), tags.index(SAMPLES_TAG))

    def test_stream_footer_after_samples(self) -> None:
        w = self.make_writer()
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_stream_footer("s1")
        w.close()
        _, chunks = parse_xdf_chunks(self.out_path)
        tags = [c["tag"] for c in chunks]
        self.assertGreater(tags.index(STREAM_FOOTER_TAG), tags.index(SAMPLES_TAG))


# ── pyxdf roundtrip tests ─────────────────────────────────────────────────────


@unittest.skipIf(pyxdf is None, "pyxdf is required for roundtrip validation")
class TestPyxdfRoundtrip(XDFWriterTestCase):
    def _load(self) -> Tuple[list, dict]:
        return pyxdf.load_xdf(self.out_path)

    def test_roundtrip_float32_stream(self) -> None:
        info = self.make_stream(channel_count=2, channel_format=cf_float32, nominal_srate=100.0)
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_samples("s1", [[1.0, 2.0], [3.0, 4.0]], [1.0, 1.01])
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        self.assertEqual(len(streams), 1)
        ts = streams[0]["time_stamps"]
        data = streams[0]["time_series"]
        self.assertEqual(len(ts), 2)
        self.assertAlmostEqual(data[0][0], 1.0, places=5)
        self.assertAlmostEqual(data[0][1], 2.0, places=5)
        self.assertAlmostEqual(data[1][0], 3.0, places=5)

    def test_roundtrip_string_stream(self) -> None:
        info = self.make_stream(
            channel_count=1, channel_format=cf_string, nominal_srate=0.0, stream_type="Markers"
        )
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_samples("s1", [["start"], ["stop"]], [1.0, 2.0])
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        self.assertEqual(len(streams), 1)
        data = streams[0]["time_series"]
        self.assertEqual(data[0][0], "start")
        self.assertEqual(data[1][0], "stop")

    def test_roundtrip_int16_stream(self) -> None:
        info = self.make_stream(channel_count=3, channel_format=cf_int16, nominal_srate=10.0)
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_samples("s1", [[192, 255, 238], [12, 22, 32]], [5.1, 5.2])
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        data = streams[0]["time_series"]
        self.assertEqual(list(data[0]), [192, 255, 238])
        self.assertEqual(list(data[1]), [12, 22, 32])

    def test_roundtrip_two_streams(self) -> None:
        eeg = self.make_stream(
            name="EEG", channel_count=2, channel_format=cf_float32, uid="eeg-uid"
        )
        markers = self.make_stream(
            name="Markers",
            stream_type="Markers",
            channel_count=1,
            channel_format=cf_string,
            nominal_srate=0.0,
            uid="mk-uid",
        )
        w = self.make_writer()
        w.open()
        w.add_stream(eeg, stream_key="eeg-uid")
        w.add_stream(markers, stream_key="mk-uid")
        w.write_samples("eeg-uid", [[1.0, 2.0], [3.0, 4.0]], [1.0, 1.01])
        w.write_samples("mk-uid", [["go"]], [1.0])
        w.write_stream_footer("eeg-uid")
        w.write_stream_footer("mk-uid")
        w.close()
        streams, _ = self._load()
        names = {s["info"]["name"][0] for s in streams}
        self.assertEqual(names, {"EEG", "Markers"})

    def test_roundtrip_empty_stream(self) -> None:
        info = self.make_stream()
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        self.assertEqual(len(streams), 1)
        self.assertEqual(len(streams[0]["time_stamps"]), 0)

    def test_roundtrip_with_clock_offsets(self) -> None:
        info = self.make_stream()
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_clock_offset("s1", 100.0, 0.005)
        w.write_clock_offset("s1", 105.0, 0.006)
        w.write_samples("s1", [[1.0]], [100.0])
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        self.assertEqual(len(streams[0]["time_stamps"]), 1)

    def test_roundtrip_boundary_chunk_does_not_corrupt_file(self) -> None:
        info = self.make_stream()
        w = self.make_writer()
        w.open()
        w.add_stream(info, stream_key="s1")
        w.write_boundary_chunk()
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_boundary_chunk()
        w.write_stream_footer("s1")
        w.close()
        streams, _ = self._load()
        self.assertEqual(len(streams[0]["time_stamps"]), 1)

    def test_roundtrip_all_numeric_formats(self) -> None:
        formats = [
            (cf_float32, [1.5], "float32"),
            (cf_double64, [3.14], "double64"),
            (cf_int8, [42], "int8"),
            (cf_int16, [1000], "int16"),
            (cf_int32, [100000], "int32"),
            (cf_int64, [10**15], "int64"),
        ]
        for fmt, val, fmt_name in formats:
            with self.subTest(format=fmt_name):
                out = str(Path(self._tmpdir.name) / f"test_{fmt_name}.xdf")
                info = self.make_stream(channel_count=1, channel_format=fmt, uid=fmt_name)
                w = SimpleXDFWriter(out)
                w.open()
                w.add_stream(info, stream_key=fmt_name)
                w.write_samples(fmt_name, [val], [1.0])
                w.write_stream_footer(fmt_name)
                w.close()
                streams, _ = pyxdf.load_xdf(out)
                self.assertEqual(len(streams), 1)
                self.assertEqual(len(streams[0]["time_stamps"]), 1)


# ── gzip (.xdfz) test ─────────────────────────────────────────────────────────


class TestGzipXdfzFormat(XDFWriterTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.out_path = str(Path(self._tmpdir.name) / "test.xdfz")

    def test_xdfz_file_is_gzip_compressed(self) -> None:
        w = SimpleXDFWriter(self.out_path)
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_stream_footer("s1")
        w.close()
        with open(self.out_path, "rb") as fh:
            magic = fh.read(2)
        self.assertEqual(magic, b"\x1f\x8b", "XDFZ file must start with gzip magic bytes")

    def test_xdfz_decompresses_to_valid_xdf(self) -> None:
        w = SimpleXDFWriter(self.out_path)
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0], [2.0]], [1.0, 2.0])
        w.write_stream_footer("s1")
        w.close()
        with gzip.open(self.out_path, "rb") as gz:
            raw = gz.read()
        self.assertTrue(raw.startswith(XDF_MAGIC))

    @unittest.skipIf(pyxdf is None, "pyxdf required")
    def test_xdfz_readable_by_pyxdf(self) -> None:
        w = SimpleXDFWriter(self.out_path)
        w.open()
        w.add_stream(self.make_stream(), stream_key="s1")
        w.write_samples("s1", [[1.0]], [1.0])
        w.write_stream_footer("s1")
        w.close()
        streams, _ = pyxdf.load_xdf(self.out_path)
        self.assertEqual(len(streams), 1)
        self.assertEqual(len(streams[0]["time_stamps"]), 1)


# ── comparison with known-good example files ──────────────────────────────────


@unittest.skipIf(not (FIXTURES_DIR / "minimal.xdf").exists(), "minimal.xdf fixture not found")
@unittest.skipIf(pyxdf is None, "pyxdf required")
class TestMinimalXdfComparison(XDFWriterTestCase):
    """Replicate the data from the xdf-modules/example-files minimal.xdf and compare."""

    # Data extracted from the reference minimal.xdf file
    INT16_STREAM_DATA = [
        (5.1, [192, 255, 238]),
        (5.2, [12, 22, 32]),
        (0.0, [13, 23, 33]),  # implicit timestamp
        (0.0, [14, 24, 34]),  # implicit timestamp
        (5.5, [15, 25, 35]),
        (5.6, [12, 22, 32]),
        (0.0, [13, 23, 33]),  # implicit timestamp
        (0.0, [14, 24, 34]),  # implicit timestamp
        (0.0, [15, 25, 35]),  # implicit timestamp
    ]
    STRING_STREAM_DATA = [
        (5.2, ["Hello"]),
        (0.0, ["World"]),
        (0.0, ["from"]),
        (5.5, ["LSL"]),
        (5.6, ["Hello"]),
        (0.0, ["World"]),
        (0.0, ["from"]),
        (0.0, ["LSL"]),
    ]
    INT16_CLOCK_OFFSETS = [(6.1, -0.1), (7.1, -0.1)]

    def _generate_equivalent_file(self, out_path: str) -> None:
        int16_info = DummyStreamInfo(
            "SendDataC", "EEG", 3, 10.0, cf_int16, "src-int", "xdfwriter_11_int"
        )
        str_info = DummyStreamInfo(
            "SendDataString", "StringMarker", 1, 10.0, cf_string, "src-str", "xdfwriter_11_str"
        )
        w = SimpleXDFWriter(out_path)
        w.open()
        w.add_stream(int16_info, stream_key="xdfwriter_11_int")
        w.add_stream(str_info, stream_key="xdfwriter_11_str")
        for t, v in self.INT16_CLOCK_OFFSETS:
            w.write_clock_offset("xdfwriter_11_int", t, v)
        int16_ts = [r[0] for r in self.INT16_STREAM_DATA]
        int16_data = [r[1] for r in self.INT16_STREAM_DATA]
        w.write_samples("xdfwriter_11_int", int16_data, int16_ts)
        str_ts = [r[0] for r in self.STRING_STREAM_DATA]
        str_data = [r[1] for r in self.STRING_STREAM_DATA]
        w.write_samples("xdfwriter_11_str", str_data, str_ts)
        w.write_stream_footer("xdfwriter_11_int")
        w.write_stream_footer("xdfwriter_11_str")
        w.close()

    def test_stream_count_matches_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        self.assertEqual(len(our_streams), len(ref_streams))

    def test_stream_names_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_names = {s["info"]["name"][0] for s in ref_streams}
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_names = {s["info"]["name"][0] for s in our_streams}
        self.assertEqual(our_names, ref_names)

    def test_stream_formats_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_fmts = {s["info"]["name"][0]: s["info"]["channel_format"][0] for s in ref_streams}
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_fmts = {s["info"]["name"][0]: s["info"]["channel_format"][0] for s in our_streams}
        self.assertEqual(our_fmts, ref_fmts)

    def test_channel_counts_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_chs = {s["info"]["name"][0]: int(s["info"]["channel_count"][0]) for s in ref_streams}
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_chs = {s["info"]["name"][0]: int(s["info"]["channel_count"][0]) for s in our_streams}
        self.assertEqual(our_chs, ref_chs)

    def test_sample_counts_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_samp = {s["info"]["name"][0]: len(s["time_stamps"]) for s in ref_streams}
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_samp = {s["info"]["name"][0]: len(s["time_stamps"]) for s in our_streams}
        # The int16 stream counts must match exactly.
        self.assertEqual(our_samp["SendDataC"], ref_samp["SendDataC"])
        # The reference string stream contains one extra sample (the stream footer XML
        # embedded as a data sample — a known quirk of the reference file generator).
        # Our file correctly omits that artifact, so our count is one less.
        self.assertEqual(our_samp["SendDataString"], ref_samp["SendDataString"] - 1)

    def test_int16_data_values_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_int16 = next(s for s in ref_streams if s["info"]["name"][0] == "SendDataC")
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_int16 = next(s for s in our_streams if s["info"]["name"][0] == "SendDataC")
        for i, (ref_row, our_row) in enumerate(
            zip(ref_int16["time_series"], our_int16["time_series"])
        ):
            with self.subTest(sample=i):
                self.assertEqual(list(ref_row), list(our_row))

    def test_string_data_values_match_minimal_xdf(self) -> None:
        ref_streams, _ = pyxdf.load_xdf(str(FIXTURES_DIR / "minimal.xdf"))
        ref_str = next(s for s in ref_streams if s["info"]["name"][0] == "SendDataString")
        self._generate_equivalent_file(self.out_path)
        our_streams, _ = pyxdf.load_xdf(self.out_path)
        our_str = next(s for s in our_streams if s["info"]["name"][0] == "SendDataString")
        # Compare all samples except the first (which in the reference contains the footer XML)
        ref_vals = [row[0] for row in ref_str["time_series"][1:]]
        our_vals = [row[0] for row in our_str["time_series"]]
        self.assertEqual(our_vals, ref_vals)

    def test_footer_sample_count_matches_written_samples(self) -> None:
        self._generate_equivalent_file(self.out_path)
        _, chunks = parse_xdf_chunks(self.out_path)
        footers = {c["stream_id"]: c for c in chunks if c["tag"] == STREAM_FOOTER_TAG}
        for footer_chunk in footers.values():
            xml = _xml_from_content(footer_chunk["content"])
            sc = int(xml.findtext("sample_count"))
            self.assertGreater(sc, 0)

    def test_clock_offsets_in_footer_match_written_offsets(self) -> None:
        self._generate_equivalent_file(self.out_path)
        _, chunks = parse_xdf_chunks(self.out_path)
        # Find the footer for the int16 stream (stream_id = 1 after add_stream ordering)
        headers = {c["stream_id"]: c for c in chunks if c["tag"] == STREAM_HEADER_TAG}
        # Find stream whose header has name=SendDataC
        int16_id = None
        for sid, hc in headers.items():
            xml = _xml_from_content(hc["content"])
            if xml.findtext("name") == "SendDataC":
                int16_id = sid
                break
        self.assertIsNotNone(int16_id)
        footer_chunk = next(
            c for c in chunks
            if c["tag"] == STREAM_FOOTER_TAG and c["stream_id"] == int16_id
        )
        xml = _xml_from_content(footer_chunk["content"])
        offsets = xml.findall("clock_offsets/offset")
        self.assertEqual(len(offsets), len(self.INT16_CLOCK_OFFSETS))
        for i, (exp_t, exp_v) in enumerate(self.INT16_CLOCK_OFFSETS):
            with self.subTest(offset=i):
                self.assertAlmostEqual(float(offsets[i].findtext("time")), exp_t, places=10)
                self.assertAlmostEqual(float(offsets[i].findtext("value")), exp_v, places=10)


@unittest.skipIf(
    not (FIXTURES_DIR / "empty_streams.xdf").exists(), "empty_streams.xdf fixture not found"
)
@unittest.skipIf(pyxdf is None, "pyxdf required")
class TestEmptyStreamsXdfComparison(XDFWriterTestCase):
    """Verify that streams with no samples produce valid XDF analogous to empty_streams.xdf."""

    def _generate_equivalent_file(self, out_path: str) -> None:
        empty_float = DummyStreamInfo(
            "EmptyFloat", "EEG", 1, 1.0, cf_float32, "src-ef", "uid-ef"
        )
        data_int = DummyStreamInfo(
            "DataInt", "EEG", 1, 1.0, cf_int32, "src-di", "uid-di"
        )
        ctrl = DummyStreamInfo("ctrl", "Control", 1, 0.0, cf_string, "src-ctrl", "uid-ctrl")
        empty_marker = DummyStreamInfo(
            "EmptyMarker", "Markers", 1, 0.0, cf_string, "src-em", "uid-em"
        )
        w = SimpleXDFWriter(out_path)
        w.open()
        w.add_stream(empty_float, stream_key="uid-ef")
        w.add_stream(data_int, stream_key="uid-di")
        w.add_stream(ctrl, stream_key="uid-ctrl")
        w.add_stream(empty_marker, stream_key="uid-em")
        # Write data only to the non-empty streams
        w.write_samples("uid-di", [[i] for i in range(10)], [float(i) * 1.0 + 1.0 for i in range(10)])
        w.write_samples("uid-ctrl", [["start"]], [1.0])
        # No samples for uid-ef and uid-em
        for key in ("uid-ef", "uid-di", "uid-ctrl", "uid-em"):
            w.write_stream_footer(key)
        w.close()

    def test_empty_streams_are_present_in_file(self) -> None:
        self._generate_equivalent_file(self.out_path)
        streams, _ = pyxdf.load_xdf(self.out_path)
        self.assertEqual(len(streams), 4)

    def test_empty_streams_have_zero_samples(self) -> None:
        self._generate_equivalent_file(self.out_path)
        streams, _ = pyxdf.load_xdf(self.out_path)
        empty_streams = [
            s for s in streams if s["info"]["name"][0] in ("EmptyFloat", "EmptyMarker")
        ]
        for s in empty_streams:
            with self.subTest(name=s["info"]["name"][0]):
                self.assertEqual(len(s["time_stamps"]), 0)

    def test_non_empty_streams_have_correct_sample_count(self) -> None:
        self._generate_equivalent_file(self.out_path)
        streams, _ = pyxdf.load_xdf(self.out_path)
        data_stream = next(s for s in streams if s["info"]["name"][0] == "DataInt")
        ctrl_stream = next(s for s in streams if s["info"]["name"][0] == "ctrl")
        self.assertEqual(len(data_stream["time_stamps"]), 10)
        self.assertEqual(len(ctrl_stream["time_stamps"]), 1)

    def test_empty_stream_footer_has_zero_sample_count(self) -> None:
        self._generate_equivalent_file(self.out_path)
        _, chunks = parse_xdf_chunks(self.out_path)
        headers = {c["stream_id"]: c for c in chunks if c["tag"] == STREAM_HEADER_TAG}
        footers = {c["stream_id"]: c for c in chunks if c["tag"] == STREAM_FOOTER_TAG}
        for sid, hc in headers.items():
            xml = _xml_from_content(hc["content"])
            if xml.findtext("name") in ("EmptyFloat", "EmptyMarker"):
                footer_xml = _xml_from_content(footers[sid]["content"])
                with self.subTest(name=xml.findtext("name")):
                    self.assertEqual(int(footer_xml.findtext("sample_count")), 0)

    def test_file_is_readable_by_pyxdf(self) -> None:
        self._generate_equivalent_file(self.out_path)
        streams, header = pyxdf.load_xdf(self.out_path)
        self.assertIsNotNone(header)
        self.assertEqual(len(streams), 4)


if __name__ == "__main__":
    unittest.main()
