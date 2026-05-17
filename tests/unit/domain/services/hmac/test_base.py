import pytest

from webhook_inspector.domain.services.hmac.base import HmacValidator, ValidationResult


def test_validator_is_abstract():
    with pytest.raises(TypeError):
        HmacValidator()  # type: ignore[abstract]


def test_validation_result_values():
    assert ValidationResult.VALID.value == "valid"
    assert ValidationResult.INVALID.value == "invalid"
    assert ValidationResult.MISSING.value == "missing"
    assert ValidationResult.NO_PROVIDER.value == "no_provider"
