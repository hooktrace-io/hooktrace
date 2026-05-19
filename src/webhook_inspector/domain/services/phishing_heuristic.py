"""Daily scan over the previous 24h to flag suspected phishing endpoints.

Signal: an endpoint that received many POSTs (victims submitting a form)
but never had a successful forward (the sender — i.e. the suspected phisher
— didn't configure a real receiver because they don't have one; they only
wanted to harvest the data via the viewer page).

False positive: a legit user who configured the endpoint for inspection
ONLY (no forward_url) and is getting heavy traffic. That's why a flag
doesn't auto-freeze — the launch plan stipulates manual review. Flag =
"look at this", not "block this".
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class PhishingSignal:
    endpoint_id: UUID
    post_count_24h: int
    forward_succeeded_count_24h: int

    @property
    def is_suspicious(self) -> bool:
        return self.post_count_24h >= 20 and self.forward_succeeded_count_24h == 0
