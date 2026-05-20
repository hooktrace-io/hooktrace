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

# Threshold of POSTs an endpoint must have received in 24h before the
# phishing heuristic considers it suspect. Tunable single source of truth —
# both the SQL HAVING clause in jobs/abuse_scan.py and the in-memory check
# below MUST reference this constant so they never drift.
MIN_SUSPICIOUS_POSTS = 20


@dataclass(frozen=True)
class PhishingSignal:
    endpoint_id: UUID
    post_count_24h: int
    forward_succeeded_count_24h: int

    @property
    def is_suspicious(self) -> bool:
        return self.post_count_24h >= MIN_SUSPICIOUS_POSTS and self.forward_succeeded_count_24h == 0
