"""Per-integration aggregation result — a domain concept produced by
RequestRepository.aggregate_by_integration.
"""

from dataclasses import dataclass, field


@dataclass
class IntegrationAggregate:
    """Per-integration breakdown.

    event_types maps event_type → count for senders that expose one
    (github, shopify, stripe via body); empty for senders that don't
    (twilio, slack ...).

    signature_status_counts maps the 4 ValidationResult values to
    per-bucket counts. Every captured request has exactly one of these
    (NEVER NULL) — default = 'no_provider' when no signature provider
    is configured.
    """

    integration: str
    total: int
    event_types: dict[str, int] = field(default_factory=dict)
    signature_status_counts: dict[str, int] = field(default_factory=dict)
