from unittest.mock import MagicMock

from starlette.datastructures import Headers

from webhook_inspector.web.middleware.client_ip import extract_client_ip


def _req(headers=None, client_host=None):
    r = MagicMock()
    r.headers = Headers(headers or {})
    r.client = MagicMock(host=client_host) if client_host else None
    return r


def test_uses_fly_client_ip_when_present():
    r = _req(headers={"Fly-Client-IP": "1.2.3.4"}, client_host="172.16.0.1")
    assert extract_client_ip(r) == "1.2.3.4"


def test_falls_back_to_first_xff_entry():
    r = _req(headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.10.11.12"})
    assert extract_client_ip(r) == "1.2.3.4"


def test_strips_whitespace_from_xff():
    r = _req(headers={"X-Forwarded-For": "   1.2.3.4  , 5.6.7.8"})
    assert extract_client_ip(r) == "1.2.3.4"


def test_falls_back_to_request_client_host():
    r = _req(client_host="1.2.3.4")
    assert extract_client_ip(r) == "1.2.3.4"


def test_unknown_when_no_signals():
    r = _req()
    assert extract_client_ip(r) == "0.0.0.0"


def test_fly_client_ip_takes_precedence_over_xff():
    r = _req(
        headers={
            "Fly-Client-IP": "1.2.3.4",
            "X-Forwarded-For": "9.9.9.9",
        }
    )
    assert extract_client_ip(r) == "1.2.3.4"
