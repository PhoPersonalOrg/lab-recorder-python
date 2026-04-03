from __future__ import annotations

import gzip
import struct
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Union

import pylsl
import xml.etree.ElementTree as ET
from pylsl import cf_double64, cf_float32, cf_int16, cf_int32, cf_int64, cf_int8, cf_string

XDF_MAGIC = b"XDF:"

FILE_HEADER_TAG = 1
STREAM_HEADER_TAG = 2
SAMPLES_TAG = 3
CLOCK_OFFSET_TAG = 4
BOUNDARY_TAG = 5
STREAM_FOOTER_TAG = 6

_BOUNDARY_UUID = bytes([0x43, 0xA5, 0x46, 0xDC, 0xCB, 0xF5, 0x41, 0x0F, 0xB3, 0x0E, 0xD5, 0x46, 0x73, 0x83, 0xCB, 0xE4])

LSL_CF_TO_STRING = {
    cf_float32: "float32",
    cf_double64: "double64",
    cf_string: "string",
    cf_int32: "int32",
    cf_int16: "int16",
    cf_int8: "int8",
    cf_int64: "int64",
}

try:
    from pylsl import cf_undefined

    LSL_CF_TO_STRING[cf_undefined] = "undefined"
except (ImportError, AttributeError):
    LSL_CF_TO_STRING[0] = "undefined"

LSL_STRING_TO_STRUCT_FORMAT = {
    "float32": "f",
    "double64": "d",
    "int32": "i",
    "int16": "h",
    "int8": "b",
    "int64": "q",
}


@dataclass
class XDFStreamState:
    stream_key: str
    lsl_info: pylsl.StreamInfo
    xdf_stream_id: int
    lsl_channel_format_str: str
    channel_count: int
    nominal_srate: float
    stream_header_xml: str
    struct_format_char: Optional[str] = None
    sample_count: int = 0
    first_timestamp: Optional[float] = None
    last_timestamp: Optional[float] = None
    implied_next_timestamp: Optional[float] = None
    clock_offsets: List[tuple[float, float]] = field(default_factory=list)


class SimpleXDFWriter:
    def __init__(self, filename: str):
        self.filename = filename
        self.file = None
        self.stream_id_counter = 0
        self.stream_info_map: Dict[str, XDFStreamState] = {}
        self.stream_ids: Dict[int, XDFStreamState] = {}
        self.write_lock = threading.Lock()


    def open(self) -> None:
        if self.filename.lower().endswith(".xdfz"):
            self.file = gzip.open(self.filename, "wb")
        else:
            self.file = open(self.filename, "wb")
        self.file.write(XDF_MAGIC)
        self._write_chunk(FILE_HEADER_TAG, self._build_file_header_xml())
        self.file.flush()
        print(f"XDF file {self.filename} opened and FileHeader written.")


    def add_stream(self, lsl_stream_info: pylsl.StreamInfo, stream_key: Optional[str] = None) -> int:
        self._ensure_open()
        resolved_stream_key = stream_key or self._get_stream_key(lsl_stream_info)
        if resolved_stream_key in self.stream_info_map:
            return self.stream_info_map[resolved_stream_key].xdf_stream_id

        self.stream_id_counter += 1
        lsl_channel_format_str = LSL_CF_TO_STRING.get(lsl_stream_info.channel_format(), "undefined")
        stream_state = XDFStreamState(
            stream_key=resolved_stream_key,
            lsl_info=lsl_stream_info,
            xdf_stream_id=self.stream_id_counter,
            lsl_channel_format_str=lsl_channel_format_str,
            channel_count=int(lsl_stream_info.channel_count()),
            nominal_srate=float(lsl_stream_info.nominal_srate()),
            stream_header_xml=self._get_stream_header_xml(lsl_stream_info),
            struct_format_char=LSL_STRING_TO_STRUCT_FORMAT.get(lsl_channel_format_str),
        )
        self.stream_info_map[resolved_stream_key] = stream_state
        self.stream_ids[stream_state.xdf_stream_id] = stream_state
        self._write_chunk(STREAM_HEADER_TAG, stream_state.stream_header_xml, stream_id=stream_state.xdf_stream_id)
        print(f"Added stream to XDF: ID {stream_state.xdf_stream_id}, Name: {lsl_stream_info.name()}, Key: {resolved_stream_key}, Format: {lsl_channel_format_str}")
        return stream_state.xdf_stream_id


    def write_samples(self, stream_key: str, samples: Sequence[Any], timestamps: Sequence[float]) -> int:
        self._ensure_open()
        stream_state = self._require_stream(stream_key)
        if not samples or not timestamps:
            return 0
        if len(samples) != len(timestamps):
            raise ValueError(f"Sample/timestamp mismatch for stream {stream_key}: {len(samples)} != {len(timestamps)}")

        payload = bytearray()
        payload.extend(self._encode_fixlen_int(len(samples), 4))
        written_count = 0

        for sample, timestamp in zip(samples, timestamps):
            normalized_timestamp = float(timestamp)
            payload.extend(self._encode_timestamp(normalized_timestamp))
            payload.extend(self._encode_sample(sample, stream_state))
            self._update_stream_stats(stream_state, normalized_timestamp)
            written_count += 1

        if written_count == 0:
            return 0

        self._write_chunk(SAMPLES_TAG, payload, stream_id=stream_state.xdf_stream_id)
        return written_count


    def write_stream_footer(self, stream_key: str, xml_content: Optional[str] = None) -> None:
        self._ensure_open()
        stream_state = self._require_stream(stream_key)
        footer_xml = xml_content or self._build_stream_footer_xml(stream_state)
        self._write_chunk(STREAM_FOOTER_TAG, footer_xml, stream_id=stream_state.xdf_stream_id)
        print(f"Wrote StreamFooter for stream {stream_key} (ID {stream_state.xdf_stream_id})")


    def write_clock_offset(self, stream_ref: Union[str, int], collection_time: float, offset_value: float) -> None:
        self._ensure_open()
        stream_state = self._require_stream_ref(stream_ref)
        stream_state.clock_offsets.append((float(collection_time), float(offset_value)))
        payload = struct.pack("<dd", float(collection_time), float(offset_value))
        self._write_chunk(CLOCK_OFFSET_TAG, payload, stream_id=stream_state.xdf_stream_id)


    def write_boundary_chunk(self) -> None:
        self._ensure_open()
        self._write_chunk(BOUNDARY_TAG, _BOUNDARY_UUID)


    def close(self) -> None:
        if self.file:
            self.file.flush()
            self.file.close()
            self.file = None
            print(f"XDF file {self.filename} closed.")


    def _ensure_open(self) -> None:
        if not self.file:
            raise IOError("File not open. Call open() first.")


    def _require_stream(self, stream_key: str) -> XDFStreamState:
        if stream_key not in self.stream_info_map:
            raise KeyError(f"Stream key {stream_key} not found")
        return self.stream_info_map[stream_key]


    def _require_stream_ref(self, stream_ref: Union[str, int]) -> XDFStreamState:
        if isinstance(stream_ref, str):
            return self._require_stream(stream_ref)
        if stream_ref not in self.stream_ids:
            raise KeyError(f"Stream ID {stream_ref} not found")
        return self.stream_ids[stream_ref]


    def _write_chunk(self, tag: int, content: Union[str, bytes, bytearray], stream_id: Optional[int] = None) -> None:
        content_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        with self.write_lock:
            self._write_chunk_header(tag, len(content_bytes), stream_id=stream_id)
            self.file.write(content_bytes)


    def _write_chunk_header(self, tag: int, content_length: int, stream_id: Optional[int] = None) -> None:
        total_length = content_length + 2 + (4 if stream_id is not None else 0)
        self.file.write(self._encode_varlen_int(total_length))
        self.file.write(struct.pack("<H", tag))
        if stream_id is not None:
            self.file.write(struct.pack("<I", int(stream_id)))


    def _encode_varlen_int(self, value: int) -> bytes:
        if value < 0:
            raise ValueError("Variable-length integers must be non-negative")
        if value < 256:
            return bytes([1, value])
        if value <= 0xFFFFFFFF:
            return bytes([4]) + struct.pack("<I", value)
        return bytes([8]) + struct.pack("<Q", value)


    def _encode_fixlen_int(self, value: int, width: int) -> bytes:
        if width == 1:
            return bytes([1, value])
        if width == 4:
            return bytes([4]) + struct.pack("<I", value)
        if width == 8:
            return bytes([8]) + struct.pack("<Q", value)
        raise ValueError(f"Unsupported fixed integer width: {width}")


    def _encode_timestamp(self, timestamp: float) -> bytes:
        if timestamp == 0:
            return b"\x00"
        return b"\x08" + struct.pack("<d", timestamp)


    def _encode_sample(self, sample: Any, stream_state: XDFStreamState) -> bytes:
        normalized_sample = self._normalize_sample(sample, stream_state.channel_count, stream_state.lsl_channel_format_str == "string")
        if stream_state.lsl_channel_format_str == "string":
            encoded_sample = bytearray()
            for value in normalized_sample:
                encoded_value = str(value).encode("utf-8")
                encoded_sample.extend(self._encode_varlen_int(len(encoded_value)))
                encoded_sample.extend(encoded_value)
            return bytes(encoded_sample)

        if not stream_state.struct_format_char:
            raise ValueError(f"Unsupported channel format for stream {stream_state.stream_key}: {stream_state.lsl_channel_format_str}")

        pack_string = f"<{stream_state.channel_count}{stream_state.struct_format_char}"
        return struct.pack(pack_string, *normalized_sample)


    def _normalize_sample(self, sample: Any, channel_count: int, is_string_stream: bool) -> List[Any]:
        if channel_count == 1 and (is_string_stream or not isinstance(sample, (list, tuple))):
            values = [sample]
        elif isinstance(sample, (list, tuple)):
            values = list(sample)
        else:
            values = [sample]

        if len(values) != channel_count:
            raise ValueError(f"Expected {channel_count} values but received {len(values)}: {sample}")

        if is_string_stream:
            return ["" if value is None else str(value) for value in values]
        return values


    def _update_stream_stats(self, stream_state: XDFStreamState, timestamp: float) -> None:
        if timestamp > 0:
            if stream_state.first_timestamp is None:
                stream_state.first_timestamp = timestamp
            stream_state.last_timestamp = timestamp
            if stream_state.nominal_srate > 0:
                stream_state.implied_next_timestamp = timestamp + (1.0 / stream_state.nominal_srate)
        elif stream_state.implied_next_timestamp is not None:
            inferred_timestamp = stream_state.implied_next_timestamp
            if stream_state.first_timestamp is None:
                stream_state.first_timestamp = inferred_timestamp
            stream_state.last_timestamp = inferred_timestamp
            stream_state.implied_next_timestamp = inferred_timestamp + (1.0 / stream_state.nominal_srate)
        stream_state.sample_count += 1


    def _build_file_header_xml(self) -> str:
        now = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        return f"<?xml version=\"1.0\"?>\n  <info>\n    <version>1.0</version>\n    <datetime>{now}</datetime>\n  </info>"


    def _get_stream_header_xml(self, lsl_stream_info: pylsl.StreamInfo) -> str:
        if hasattr(lsl_stream_info, "as_xml"):
            try:
                xml_text = lsl_stream_info.as_xml()
                if xml_text:
                    return xml_text
            except Exception:
                pass
        return self._build_fallback_stream_header_xml(lsl_stream_info)


    def _build_fallback_stream_header_xml(self, lsl_stream_info: pylsl.StreamInfo) -> str:
        root = ET.Element("info")
        ET.SubElement(root, "name").text = self._safe_call(lsl_stream_info, "name")
        ET.SubElement(root, "type").text = self._safe_call(lsl_stream_info, "type")
        ET.SubElement(root, "channel_count").text = str(self._safe_call(lsl_stream_info, "channel_count", 0))
        ET.SubElement(root, "nominal_srate").text = str(self._safe_call(lsl_stream_info, "nominal_srate", 0.0))
        ET.SubElement(root, "channel_format").text = LSL_CF_TO_STRING.get(self._safe_call(lsl_stream_info, "channel_format", 0), "undefined")
        ET.SubElement(root, "source_id").text = self._safe_call(lsl_stream_info, "source_id") or self._safe_call(lsl_stream_info, "uid")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


    def _build_stream_footer_xml(self, stream_state: XDFStreamState) -> str:
        footer = [
            "<?xml version=\"1.0\"?><info>",
            f"<first_timestamp>{self._format_float(stream_state.first_timestamp or 0.0)}</first_timestamp>",
            f"<last_timestamp>{self._format_float(stream_state.last_timestamp or 0.0)}</last_timestamp>",
            f"<sample_count>{stream_state.sample_count}</sample_count>",
            "<clock_offsets>",
        ]
        for time_value, offset_value in stream_state.clock_offsets:
            footer.append(f"<offset><time>{self._format_float(time_value)}</time><value>{self._format_float(offset_value)}</value></offset>")
        footer.append("</clock_offsets></info>")
        return "".join(footer)


    def _get_stream_key(self, lsl_stream_info: pylsl.StreamInfo) -> str:
        uid = self._safe_call(lsl_stream_info, "uid")
        if uid:
            return uid
        source_id = self._safe_call(lsl_stream_info, "source_id")
        if source_id:
            return source_id
        return f"stream-{self.stream_id_counter + 1}"


    def _safe_call(self, obj: Any, attr_name: str, default: Any = "") -> Any:
        attr = getattr(obj, attr_name, None)
        if not callable(attr):
            return default
        try:
            return attr()
        except Exception:
            return default


    def _format_float(self, value: float) -> str:
        return f"{float(value):.16g}"
