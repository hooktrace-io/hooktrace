"""Daily cron job invoked by arq's WorkerSettings.cron_jobs. Scans the
last 24h of activity, flags suspected phishing endpoints, posts a Discord
webhook with the list.

Runs at 03:30 UTC (off-peak for hooktrace's expected user base).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from webhook_inspector.config import Settings
from webhook_inspector.domain.services.phishing_heuristic import PhishingSignal
from webhook_inspector.infrastructure.notifications.discord_webhook import (
    post_discord_alert,
)
from webhook_inspector.observability.metrics import force_flush_metrics

logger = logging.getLogger(__name__)

# Reasons for endpoints.flag_reason. Enum-style — keep in sync with the
# CHECK constraint in migration 0012.
FLAG_REASON_PHISHING = "phishing_no_forward"
FLAG_REASON_DENYLIST = "slug_denylist_postcreation"
FLAG_REASON_MANUAL = "manual_review"


async def run_abuse_scan(ctx: dict[str, Any]) -> int:
    """arq cron entry. ctx is provided by arq; we re-use the session_factory
    set up in WorkerSettings.on_startup.

    Returns the count of newly flagged endpoints (used for tests + arq logs).
    """
    session_factory = ctx["_session_factory"]
    settings: Settings = ctx["_settings"]

    cutoff = datetime.now(UTC) - timedelta(hours=24)

    try:
        async with session_factory() as session:
            rows = await session.execute(
                text("""
                    SELECT
                        e.id AS endpoint_id,
                        COUNT(r.id) FILTER (
                            WHERE r.method IN ('POST', 'PUT', 'PATCH')
                        ) AS post_count,
                        COUNT(f.id) FILTER (
                            WHERE f.status = 'succeeded'
                        ) AS forward_ok_count
                    FROM endpoints e
                    LEFT JOIN requests r ON r.endpoint_id = e.id AND r.received_at > :cutoff
                    LEFT JOIN forwards f ON f.endpoint_id = e.id AND f.forward_completed_at > :cutoff
                    WHERE e.flagged_at IS NULL
                    GROUP BY e.id
                    HAVING COUNT(r.id) FILTER (
                        WHERE r.method IN ('POST', 'PUT', 'PATCH')
                    ) >= 20
                """),
                {"cutoff": cutoff},
            )

            suspicious: list[PhishingSignal] = []
            flagged_count = 0
            for row in rows:
                signal = PhishingSignal(
                    endpoint_id=row.endpoint_id,
                    post_count_24h=row.post_count,
                    forward_succeeded_count_24h=row.forward_ok_count,
                )
                if signal.is_suspicious:
                    await session.execute(
                        text("""
                            UPDATE endpoints
                            SET flagged_at = NOW(), flag_reason = :reason
                            WHERE id = :id
                        """),
                        {"id": signal.endpoint_id, "reason": FLAG_REASON_PHISHING},
                    )
                    suspicious.append(signal)
                    flagged_count += 1
            await session.commit()

        if suspicious and settings.abuse_webhook_url:
            lines = [
                f"- endpoint {s.endpoint_id}: {s.post_count_24h} POSTs, "
                f"{s.forward_succeeded_count_24h} successful forwards (phishing suspicion)"
                for s in suspicious
            ]
            try:
                await post_discord_alert(
                    settings.abuse_webhook_url,
                    f"**Abuse scan — {flagged_count} endpoint(s) flagged:**\n" + "\n".join(lines),
                )
            except Exception:
                logger.exception("abuse_scan_discord_post_failed")

        logger.info("abuse_scan_complete", extra={"flagged": flagged_count})
        return flagged_count
    finally:
        # Short-lived cron path: flush OTEL metrics before returning so
        # the last interval's counters are exported. Matches the worker's
        # general on_shutdown pattern but at per-cron-run granularity.
        force_flush_metrics()
