from starlette.datastructures import Headers

from webhook_inspector.web.middleware.client_ip import extract_client_ip


def test_uses_fly_client_ip_when_present():
    h = Headers({"Fly-Client-IP": "1.2.3.4"})
    assert extract_client_ip(h, "172.16.0.1") == "1.2.3.4"


def test_falls_back_to_first_xff_entry():
    h = Headers({"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.10.11.12"})
    assert extract_client_ip(h, None) == "1.2.3.4"


def test_strips_whitespace_from_xff():
    h = Headers({"X-Forwarded-For": "   1.2.3.4  , 5.6.7.8"})
    assert extract_client_ip(h, None) == "1.2.3.4"


def test_falls_back_to_request_client_host():
    h = Headers({})
    assert extract_client_ip(h, "1.2.3.4") == "1.2.3.4"


def test_unknown_when_no_signals():
    h = Headers({})
    assert extract_client_ip(h, None) == "0.0.0.0"


def test_fly_client_ip_takes_precedence_over_xff():
    h = Headers(
        {
            "Fly-Client-IP": "1.2.3.4",
            "X-Forwarded-For": "9.9.9.9",
        }
    )
    assert extract_client_ip(h, None) == "1.2.3.4"
