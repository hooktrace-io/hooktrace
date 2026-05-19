from uuid import uuid4

from webhook_inspector.domain.services.phishing_heuristic import PhishingSignal


def test_high_posts_zero_forwards_is_suspicious():
    signal = PhishingSignal(
        endpoint_id=uuid4(),
        post_count_24h=25,
        forward_succeeded_count_24h=0,
    )
    assert signal.is_suspicious is True


def test_high_posts_with_forwards_is_not_suspicious():
    signal = PhishingSignal(
        endpoint_id=uuid4(),
        post_count_24h=25,
        forward_succeeded_count_24h=1,
    )
    assert signal.is_suspicious is False


def test_low_posts_zero_forwards_is_not_suspicious():
    signal = PhishingSignal(
        endpoint_id=uuid4(),
        post_count_24h=5,
        forward_succeeded_count_24h=0,
    )
    assert signal.is_suspicious is False


def test_boundary_19_posts_is_not_suspicious():
    signal = PhishingSignal(
        endpoint_id=uuid4(),
        post_count_24h=19,
        forward_succeeded_count_24h=0,
    )
    assert signal.is_suspicious is False


def test_boundary_20_posts_is_suspicious():
    signal = PhishingSignal(
        endpoint_id=uuid4(),
        post_count_24h=20,
        forward_succeeded_count_24h=0,
    )
    assert signal.is_suspicious is True
