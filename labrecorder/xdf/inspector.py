#!/usr/bin/env python3
"""
Simple XDF File Inspector.
"""

from __future__ import annotations

import gzip
import struct
import sys
import xml.etree.ElementTree as ET
from typing import BinaryIO, Dict, Optional, Tuple

XDF_MAGIC = b"XDF:"
FILE_HEADER_TAG = 1
STREAM_HEADER_TAG = 2
SAMPLES_TAG = 3
CLOCK_OFFSET_TAG = 4
BOUNDARY_TAG = 5
STREAM_FOOTER_TAG = 6

CHUNK_NAMES = {
    FILE_HEADER_TAG: "FileHeader",
    STREAM_HEADER_TAG: "StreamHeader",
    SAMPLES_TAG: "Samples",
    CLOCK_OFFSET_TAG: "ClockOffset",
    BOUNDARY_TAG: "Boundary",
    STREAM_FOOTER_TAG: "StreamFooter",
}

STREAM_SPECIFIC_TAGS = {STREAM_HEADER_TAG, SAMPLES_TAG, CLOCK_OFFSET_TAG, STREAM_FOOTER_TAG}
NUMERIC_SIZES = {
    "float32": 4,
    "double64": 8,
    "int32": 4,
    "int16": 2,
    "int8": 1,
    "int64": 8,
}


def inspect_xdf_file(filename: str) -> bool:
    """Inspect XDF file and print summary information."""
    print("=== XDF File Inspector ===")
    print(f"File: {filename}")

    try:
        open_fn = gzip.open if filename.lower().endswith(".xdfz") else open
        with open_fn(filename, "rb") as file_handle:
            magic = file_handle.read(4)
            if magic != XDF_MAGIC:
                print(f"ERROR: Invalid XDF file. Expected 'XDF:', got {magic}")
                return False

            print(f"Valid XDF magic header: {magic.decode()}")
            file_handle.seek(0, 2)
            file_size = file_handle.tell()
            file_handle.seek(4)
            print(f"File size: {file_size} bytes ({file_size / 1024:.1f} KB)")
            print("\n=== Chunks ===")

            chunk_counts: Dict[str, int] = {}
            stream_info: Dict[int, Dict[str, object]] = {}
            chunk_num = 0

            while file_handle.tell() < file_size:
                chunk_length = _read_varlen_int(file_handle)
                if chunk_length is None:
                    break

                tag_data = file_handle.read(2)
                if len(tag_data) < 2:
                    break
                tag = struct.unpack("<H", tag_data)[0]

                stream_id = None
                content_length = chunk_length - 2
                if tag in STREAM_SPECIFIC_TAGS:
                    stream_id_data = file_handle.read(4)
                    if len(stream_id_data) < 4:
                        break
                    stream_id = struct.unpack("<I", stream_id_data)[0]
                    content_length -= 4

                content = file_handle.read(content_length)
                if len(content) < content_length:
                    print(f"Warning: Expected {content_length} bytes, got {len(content)}")
                    break

                chunk_num += 1
                chunk_name = CHUNK_NAMES.get(tag, f"Unknown({tag})")
                chunk_counts[chunk_name] = chunk_counts.get(chunk_name, 0) + 1
                stream_label = f", Stream ID={stream_id}" if stream_id is not None else ""
                print(f"Chunk {chunk_num}: {chunk_name} (tag={tag}, {content_length} bytes{stream_label})")

                if tag in (STREAM_HEADER_TAG, STREAM_FOOTER_TAG):
                    _parse_stream_xml_chunk(tag, stream_id, content, stream_info)
                elif tag == SAMPLES_TAG and stream_id is not None:
                    parsed_sample_count = _count_samples(content, stream_info.get(stream_id))
                    print(f"  Stream ID: {stream_id}, Samples: {parsed_sample_count}")
                    if stream_id in stream_info:
                        stream_info[stream_id]["sample_count"] = stream_info[stream_id].get("sample_count", 0) + parsed_sample_count
                elif tag == CLOCK_OFFSET_TAG and stream_id is not None and len(content) >= 16:
                    time_val, offset_val = struct.unpack("<dd", content[:16])
                    print(f"  Stream ID: {stream_id}, Time: {time_val:.6f}, Offset: {offset_val:.9f}")

            print("\n=== Summary ===")
            print(f"Total chunks: {chunk_num}")
            for chunk_type, count in chunk_counts.items():
                print(f"  {chunk_type}: {count}")

            print("\n=== Streams ===")
            total_samples = 0
            for stream_id, info in stream_info.items():
                sample_count = int(info.get("sample_count", 0))
                total_samples += sample_count
                print(f"Stream {stream_id}: {info.get('name', 'Unknown')} ({info.get('type', 'Unknown')})")
                print(f"  Channels: {info.get('channels', 'Unknown')}, Rate: {info.get('srate', 'Unknown')} Hz")
                print(f"  Format: {info.get('channel_format', 'Unknown')}, Samples recorded: {sample_count}")

            print(f"\nTotal samples across all streams: {total_samples}")
            return True
    except FileNotFoundError:
        print(f"ERROR: File '{filename}' not found")
        return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False


def _read_varlen_int(file_handle: BinaryIO) -> Optional[int]:
    width_data = file_handle.read(1)
    if not width_data:
        return None
    width = width_data[0]
    if width not in (1, 4, 8):
        raise ValueError(f"Unsupported variable-length integer width: {width}")
    value_data = file_handle.read(width)
    if len(value_data) != width:
        return None
    return int.from_bytes(value_data, "little")


def _decode_varlen_int(data: bytes, offset: int) -> Tuple[int, int]:
    width = data[offset]
    offset += 1
    if width not in (1, 4, 8):
        raise ValueError(f"Unsupported variable-length integer width: {width}")
    end_offset = offset + width
    value = int.from_bytes(data[offset:end_offset], "little")
    return value, end_offset


def _parse_stream_xml_chunk(tag: int, stream_id: Optional[int], content: bytes, stream_info: Dict[int, Dict[str, object]]) -> None:
    xml_content = content.decode("utf-8", errors="ignore")
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print(f"  Warning: Could not parse XML: {e}")
        return

    if tag == STREAM_FOOTER_TAG:
        if stream_id is not None:
            print(f"  Stream ID: {stream_id}")
        print(f"  First timestamp: {_find_xml_text(root, 'first_timestamp', 'Unknown')}")
        print(f"  Last timestamp: {_find_xml_text(root, 'last_timestamp', 'Unknown')}")
        print(f"  Sample count: {_find_xml_text(root, 'sample_count', 'Unknown')}")
        return

    name_text = _find_xml_text(root, "name", "Unknown")
    type_text = _find_xml_text(root, "type", "Unknown")
    channel_text = _find_xml_text(root, "channel_count", "Unknown")
    srate_text = _find_xml_text(root, "nominal_srate", "Unknown")
    channel_format = _find_xml_text(root, "channel_format", "Unknown")

    if stream_id is not None:
        print(f"  Stream ID: {stream_id}")
    print(f"  Name: {name_text}")
    print(f"  Type: {type_text}")
    print(f"  Channels: {channel_text}")
    print(f"  Sample Rate: {srate_text}")
    print(f"  Channel Format: {channel_format}")

    if stream_id is not None:
        stream_info[stream_id] = {
            "name": name_text,
            "type": type_text,
            "channels": int(channel_text) if str(channel_text).isdigit() else channel_text,
            "srate": float(srate_text) if _is_float_like(str(srate_text)) else srate_text,
            "channel_format": channel_format,
            "sample_count": 0,
        }


def _count_samples(content: bytes, stream_metadata: Optional[Dict[str, object]]) -> int:
    num_samples, offset = _decode_varlen_int(content, 0)
    if stream_metadata is None:
        return num_samples

    channel_format = str(stream_metadata.get("channel_format", ""))
    channel_count = int(stream_metadata.get("channels", 0) or 0)
    if channel_count <= 0:
        return num_samples

    for _ in range(num_samples):
        timestamp_width = content[offset]
        offset += 1
        if timestamp_width not in (0, 4, 8):
            raise ValueError(f"Unsupported timestamp width: {timestamp_width}")
        offset += timestamp_width

        if channel_format == "string":
            for _channel_idx in range(channel_count):
                string_length, offset = _decode_varlen_int(content, offset)
                offset += string_length
        else:
            numeric_size = NUMERIC_SIZES.get(channel_format)
            if numeric_size is None:
                return num_samples
            offset += channel_count * numeric_size

    return num_samples


def _find_xml_text(root: ET.Element, tag_name: str, default: str) -> str:
    element = root.find(tag_name)
    if element is None or element.text is None:
        return default
    return element.text


def _is_float_like(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python xdf_inspector.py <xdf_file>")
        sys.exit(1)

    filename = sys.argv[1]
    success = inspect_xdf_file(filename)
    sys.exit(0 if success else 1)
