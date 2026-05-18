"""Unit tests for domain/services/body_parsers.py — TDD RED phase."""

from webhook_inspector.domain.services.body_parsers import (
    extract_stripe_event_type,
    parse_form_params,
)

_8KB = 8 * 1024


# ---------------------------------------------------------------------------
# parse_form_params
# ---------------------------------------------------------------------------


def test_parse_form_params_happy_path():
    body = b"signature=abc&timestamp=12345&token=tok"
    result = parse_form_params(body, "application/x-www-form-urlencoded")
    assert result == {"signature": "abc", "timestamp": "12345", "token": "tok"}


def test_parse_form_params_wrong_content_type_returns_empty():
    body = b"key=value"
    result = parse_form_params(body, "application/json")
    assert result == {}


def test_parse_form_params_oversized_body_returns_empty():
    body = b"a=b&" * (_8KB // 4 + 1)  # definitely > 8KB
    assert len(body) > _8KB
    result = parse_form_params(body, "application/x-www-form-urlencoded")
    assert result == {}


def test_parse_form_params_non_utf8_returns_empty():
    body = b"\xff\xfe"  # invalid UTF-8
    result = parse_form_params(body, "application/x-www-form-urlencoded")
    assert result == {}


# ---------------------------------------------------------------------------
# extract_stripe_event_type
# ---------------------------------------------------------------------------


def test_extract_stripe_event_type_happy_path():
    import json

    body = json.dumps({"type": "charge.succeeded", "id": "evt_123"}).encode()
    result = extract_stripe_event_type(body)
    assert result == "charge.succeeded"


def test_extract_stripe_event_type_missing_field_returns_none():
    import json

    body = json.dumps({"id": "evt_123"}).encode()
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_malformed_json_returns_none():
    body = b"not-json-at-all{{{{"
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_non_dict_root_string_returns_none():
    import json

    body = json.dumps("just a string").encode()
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_non_dict_root_array_returns_none():
    import json

    body = json.dumps([{"type": "charge.succeeded"}]).encode()
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_numeric_type_field_returns_none():
    import json

    body = json.dumps({"type": 42}).encode()
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_null_type_field_returns_none():
    import json

    body = json.dumps({"type": None}).encode()
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_non_utf8_returns_none():
    body = b"\xff\xfe{broken}"
    result = extract_stripe_event_type(body)
    assert result is None


def test_extract_stripe_event_type_oversized_body_returns_none():
    body = b"x" * (_8KB + 1)
    result = extract_stripe_event_type(body)
    assert result is None
