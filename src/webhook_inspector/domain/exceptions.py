"""Typed domain exceptions raised within the domain layer."""


class EndpointNotFoundError(Exception):
    """Endpoint with the given token does not exist."""


class EndpointValidationError(Exception):
    """Base class for endpoint validation failures."""


class InvalidResponseStatusError(EndpointValidationError):
    """response_status_code is outside [100, 599]."""


class InvalidResponseDelayError(EndpointValidationError):
    """response_delay_ms is outside [0, 30000]."""


class ResponseBodyTooLargeError(EndpointValidationError):
    """response_body exceeds 64 KiB."""


class ForbiddenResponseHeaderError(EndpointValidationError):
    """response_headers contains a header that must be controlled by the server."""


class InvalidSlugError(EndpointValidationError):
    """slug fails the format regex (length / charset / leading/trailing hyphen)."""


class ReservedSlugError(EndpointValidationError):
    """slug is on the reserved list and cannot be claimed."""


class SlugDenylistedError(EndpointValidationError):
    """slug contains a denylisted substring (brand name, admin reserved, phishing pattern)."""


class SlugAlreadyTakenError(Exception):
    """slug is already used by another endpoint."""
