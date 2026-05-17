from abc import ABC, abstractmethod
from enum import Enum


class ValidationResult(str, Enum):
    """Canonical enum for requests.signature_status. CaptureRequest must
    always write one of these values (never NULL) so aggregation queries
    GROUP BY signature_status produce a complete histogram in PR2.
    """

    VALID = "valid"  # HMAC matches the captured body
    INVALID = "invalid"  # HMAC verification failed
    MISSING = "missing"  # provider configured but no signature header in request
    NO_PROVIDER = "no_provider"  # endpoint has no signature_provider configured


# Sync with PR2's CHECK constraint (if we add one) and the test fixtures.
SIGNATURE_STATUS_VALUES = frozenset(v.value for v in ValidationResult)


class HmacValidator(ABC):
    """Port: validate the signature header(s) of a captured request body.

    Implementations are stateless; secret is passed at validation time
    (decrypted on demand by the use case layer).
    """

    @abstractmethod
    def validate(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> ValidationResult: ...
