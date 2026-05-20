from abc import ABC, abstractmethod
from enum import StrEnum


class ValidationResult(StrEnum):
    """Canonical enum for requests.signature_status. CaptureRequest must
    always write one of these values (never NULL) so aggregation queries
    GROUP BY signature_status produce a complete histogram.
    """

    VALID = "valid"  # HMAC matches the captured body
    INVALID = "invalid"  # HMAC verification failed
    MISSING = "missing"  # provider configured but no signature header in request
    NO_PROVIDER = "no_provider"  # endpoint has no signature_provider configured


# Sync with any future DB CHECK constraint and the test fixtures.
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
    ) -> ValidationResult:
        """Return the validation outcome for this provider's signature scheme.

        Implementations MUST be constant-time when comparing the computed
        digest to the header value (hmac.compare_digest). Header lookup MUST
        be case-insensitive.
        """
