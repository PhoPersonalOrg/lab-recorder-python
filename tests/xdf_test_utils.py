import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pylsl import cf_int16, cf_string

try:
    import pyxdf
except ImportError:  # pragma: no cover - handled via unittest skips
    pyxdf = None


EXAMPLE_FIXTURE_BASE_URL = "https://raw.githubusercontent.com/xdf-modules/example-files/master"
MINIMAL_FIXTURE_NAME = "minimal.xdf"
MINIMAL_REFERENCE_FOOTER_XML = "<?xml version=\"1.0\"?><info><writer>LabRecorder xdfwriter</writer><first_timestamp>5.1</first_timestamp><last_timestamp>5.9</last_timestamp><sample_count>9</sample_count><clock_offsets><offset><time>50979.76</time><value>-.01</value></offset><offset><time>50979.86</time><value>-.02</value></offset></clock_offsets></info>"
MINIMAL_EEG_TIMESTAMPS = [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8]
MINIMAL_EEG_WRITER_TIMESTAMPS = [5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9]
MINIMAL_EEG_SAMPLES = [[192, 255, 238], [12, 22, 32], [13, 23, 33], [14, 24, 34], [15, 25, 35], [12, 22, 32], [13, 23, 33], [14, 24, 34], [15, 25, 35]]
MINIMAL_EEG_CLOCK_TIMES = [6.1, 7.1]
MINIMAL_EEG_CLOCK_VALUES = [-0.1, -0.1]
MINIMAL_MARKER_TIMESTAMPS = [5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9]
MINIMAL_MARKER_WRITER_SAMPLES = [MINIMAL_REFERENCE_FOOTER_XML, "Hello", "World", "from", "LSL", "Hello", "World", "from", "LSL"]
MINIMAL_MARKER_SAMPLES = [[MINIMAL_REFERENCE_FOOTER_XML], ["Hello"], ["World"], ["from"], ["LSL"], ["Hello"], ["World"], ["from"], ["LSL"]]


class DummyStreamInfo:
    def __init__(self, name: str, stream_type: str, channel_count: int, nominal_srate: float, channel_format: int, source_id: str, uid: str, created_at: Optional[float] = None):
        self._name = name
        self._type = stream_type
        self._channel_count = channel_count
        self._nominal_srate = nominal_srate
        self._channel_format = channel_format
        self._source_id = source_id
        self._uid = uid
        self._created_at = created_at


    def as_xml(self) -> str:
        parts = [
            "<?xml version=\"1.0\"?>",
            "<info>",
            f"<name>{self._name}</name>",
            f"<type>{self._type}</type>",
            f"<channel_count>{self._channel_count}</channel_count>",
            f"<nominal_srate>{self._nominal_srate:g}</nominal_srate>",
            f"<channel_format>{'string' if self._channel_format == cf_string else 'int16'}</channel_format>",
            f"<source_id>{self._source_id}</source_id>",
            f"<uid>{self._uid}</uid>",
        ]
        if self._created_at is not None:
            parts.append(f"<created_at>{self._created_at:.15f}</created_at>")
        parts.append("</info>")
        return "".join(parts)


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


class FakeStreamInlet:
    def __init__(self, stream_info: DummyStreamInfo, first_sample: Any, first_timestamp: float, chunk_batches: Sequence[Tuple[Sequence[Any], Sequence[float]]], time_correction_value: float = 0.0):
        self._stream_info = stream_info
        self._first_sample = self._copy_sample(first_sample)
        self._first_timestamp = float(first_timestamp)
        self._chunk_batches = [(self._copy_samples(samples), [float(timestamp) for timestamp in timestamps]) for samples, timestamps in chunk_batches]
        self._time_correction_value = float(time_correction_value)
        self._closed = False
        self._lock = threading.Lock()
        self._first_sample_returned = False


    def open_stream(self, timeout: float) -> None:
        _ = timeout


    def info(self) -> DummyStreamInfo:
        return self._stream_info


    def pull_sample(self, timeout: float = 1.0) -> Tuple[Optional[List[Any]], Optional[float]]:
        _ = timeout
        with self._lock:
            if self._first_sample_returned:
                return None, None
            self._first_sample_returned = True
            return self._copy_sample(self._first_sample), self._first_timestamp


    def pull_chunk(self, timeout: float = 0.1, max_samples: Optional[int] = None) -> Tuple[List[Any], List[float]]:
        _ = timeout
        _ = max_samples
        with self._lock:
            if not self._chunk_batches:
                return [], []
            samples, timestamps = self._chunk_batches.pop(0)
            return self._copy_samples(samples), list(timestamps)


    def was_clock_reset(self) -> bool:
        return False


    def time_correction(self, timeout: float = 2.0) -> float:
        _ = timeout
        return self._time_correction_value


    def close_stream(self) -> None:
        self._closed = True


    def is_drained(self) -> bool:
        with self._lock:
            return self._first_sample_returned and not self._chunk_batches


    @staticmethod
    def _copy_sample(sample: Any) -> Any:
        if isinstance(sample, (list, tuple)):
            return list(sample)
        return sample


    @classmethod
    def _copy_samples(cls, samples: Sequence[Any]) -> List[Any]:
        return [cls._copy_sample(sample) for sample in samples]


def build_minimal_stream_infos() -> Tuple[DummyStreamInfo, DummyStreamInfo]:
    eeg_info = DummyStreamInfo(name="SendDataC", stream_type="EEG", channel_count=3, nominal_srate=10.0, channel_format=cf_int16, source_id="xdfwriter_11_int", uid="xdfwriter_11_int", created_at=50942.723319709003)
    marker_info = DummyStreamInfo(name="SendDataString", stream_type="StringMarker", channel_count=1, nominal_srate=10.0, channel_format=cf_string, source_id="xdfwriter_11_str", uid="xdfwriter_11_str", created_at=50942.723319709003)
    return eeg_info, marker_info


def download_example_fixture(fixture_name: str) -> Path:
    fixture_dir = Path(tempfile.gettempdir()) / "lab-recorder-python-xdf-fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = fixture_dir / fixture_name
    if fixture_path.exists() and fixture_path.stat().st_size > 0:
        return fixture_path
    fixture_url = f"{EXAMPLE_FIXTURE_BASE_URL}/{fixture_name}"
    try:
        urllib.request.urlretrieve(fixture_url, fixture_path)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        raise unittest.SkipTest(f"Unable to download XDF fixture '{fixture_name}' from {fixture_url}: {exc}") from exc
    return fixture_path


def load_normalized_xdf(path: Path, include_clock_offsets: bool = False) -> List[Dict[str, Any]]:
    if pyxdf is None:
        raise unittest.SkipTest("pyxdf is required for XDF parity validation")
    streams, _ = pyxdf.load_xdf(str(path))
    normalized_streams = [_normalize_stream(stream, include_clock_offsets=include_clock_offsets) for stream in streams]
    return sorted(normalized_streams, key=lambda stream: (stream["name"], stream["type"], stream["uid"]))


def assert_xdf_matches_fixture(test_case: unittest.TestCase, actual_path: Path, fixture_name: str = MINIMAL_FIXTURE_NAME, include_clock_offsets: bool = False) -> None:
    expected_path = download_example_fixture(fixture_name)
    expected_streams = load_normalized_xdf(expected_path, include_clock_offsets=include_clock_offsets)
    actual_streams = load_normalized_xdf(actual_path, include_clock_offsets=include_clock_offsets)
    test_case.assertEqual(actual_streams, expected_streams)


def _normalize_stream(stream: Dict[str, Any], include_clock_offsets: bool = False) -> Dict[str, Any]:
    info = stream["info"]
    normalized_stream = {
        "name": _first(info.get("name")),
        "type": _first(info.get("type")),
        "channel_count": int(_first(info.get("channel_count"), 0)),
        "nominal_srate": round(float(_first(info.get("nominal_srate"), 0.0)), 6),
        "channel_format": _first(info.get("channel_format")),
        "uid": _first(info.get("uid")),
        "time_stamps": _normalize_numeric_sequence(stream["time_stamps"]),
        "time_series": _normalize_time_series(stream["time_series"]),
    }
    if include_clock_offsets:
        normalized_stream["clock_times"] = _normalize_numeric_sequence(stream.get("clock_times", []))
        normalized_stream["clock_values"] = _normalize_numeric_sequence(stream.get("clock_values", []))
    return normalized_stream


def _normalize_time_series(time_series: Any) -> List[Any]:
    if hasattr(time_series, "tolist"):
        return _normalize_nested_sequence(time_series.tolist())
    return _normalize_nested_sequence(time_series)


def _normalize_nested_sequence(values: Iterable[Any]) -> List[Any]:
    normalized = []
    for value in values:
        if isinstance(value, (list, tuple)):
            normalized.append([_normalize_scalar(item) for item in value])
        else:
            normalized.append(_normalize_scalar(value))
    return normalized


def _normalize_numeric_sequence(values: Iterable[Any]) -> List[float]:
    return [round(float(_normalize_scalar(value)), 6) for value in values]


def _normalize_scalar(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def _first(values: Optional[Sequence[Any]], default: Any = "") -> Any:
    if values is None or len(values) == 0:
        return default
    return _normalize_scalar(values[0])
