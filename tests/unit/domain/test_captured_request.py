from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from webhook_inspector.domain.entities.captured_request import CapturedRequest


def test_captured_request_stores_metadata():
    endpoint_id = uuid4()
    req = CapturedRequest.create(
        endpoint_id=endpoint_id,
        method="POST",
        path="/h/abc/foo",
        query_string="x=1",
        headers={"content-type": "application/json"},
        body=b'{"hello":"world"}',
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
    )

    assert isinstance(req.id, UUID)
    assert req.endpoint_id == endpoint_id
    assert req.method == "POST"
    assert req.path == "/h/abc/foo"
    assert req.query_string == "x=1"
    assert req.headers == {"content-type": "application/json"}
    assert req.body_size == len(b'{"hello":"world"}')
    assert req.source_ip == "192.0.2.1"
    assert isinstance(req.received_at, datetime)
    assert req.received_at.tzinfo == UTC


def test_small_body_stays_inline():
    req = CapturedRequest.create(
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"small",
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
    )

    assert req.body_preview == "small"
    assert req.blob_key is None


def test_large_body_marked_for_blob():
    big = b"x" * 9000
    req = CapturedRequest.create(
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=big,
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
    )

    assert req.body_preview is None
    assert req.blob_key is not None
    assert str(req.id) in req.blob_key
    assert req.body_size == 9000


def test_non_utf8_body_stored_as_repr_when_inline():
    body = b"\xff\xfe\xfd"
    req = CapturedRequest.create(
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=body,
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
    )

    assert req.body_preview is not None
    assert "\\x" in req.body_preview


def test_method_must_be_uppercase():
    with pytest.raises(ValueError, match="method must be uppercase"):
        CapturedRequest.create(
            endpoint_id=uuid4(),
            method="post",
            path="/h/abc",
            query_string=None,
            headers={},
            body=b"",
            source_ip="192.0.2.1",
            inline_threshold_bytes=8192,
        )


def test_captured_request_create_accepts_integration_fields():
    req = CapturedRequest.create(
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"{}",
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
        detected_integration="stripe",
        detected_event_type="charge.succeeded",
    )

    assert req.detected_integration == "stripe"
    assert req.detected_event_type == "charge.succeeded"


def test_captured_request_integration_fields_default_to_none():
    req = CapturedRequest.create(
        endpoint_id=uuid4(),
        method="POST",
        path="/h/abc",
        query_string=None,
        headers={},
        body=b"{}",
        source_ip="192.0.2.1",
        inline_threshold_bytes=8192,
    )

    assert req.detected_integration is None
    assert req.detected_event_type is None
