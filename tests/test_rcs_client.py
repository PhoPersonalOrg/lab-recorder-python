"""Unit tests for RCS client parsing."""

from labrecorder.service.rcs_client import parse_recording_path_rcs_response


def test_parse_recording_path_multiline():
    response = "C:\\data\\rec.xdf\nOK\n"
    path = parse_recording_path_rcs_response(response)
    assert path is not None
    assert path.name == "rec.xdf"


def test_parse_recording_path_missing_ok():
    assert parse_recording_path_rcs_response("C:\\data\\rec.xdf\n") is None


def test_parse_recording_path_ok_only():
    assert parse_recording_path_rcs_response("OK") is None
