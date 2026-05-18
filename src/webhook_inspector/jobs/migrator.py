"""Run Alembic migrations to head. Entrypoint for Cloud Run Job."""

import logging
import subprocess
import sys

from webhook_inspector.config import Settings
from webhook_inspector.observability.logging import configure_logging
from webhook_inspector.observability.tracing import configure_tracing

logger = logging.getLogger(__name__)


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level, settings.service_name + "-migrator")
    configure_tracing(settings.service_name + "-migrator", settings.environment)

    logger.info("starting migration")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
    )

    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)

    if result.returncode != 0:
        logger.error("migration_failed", extra={"returncode": result.returncode})
        sys.exit(result.returncode)

    logger.info("migration_complete")


if __name__ == "__main__":
    main()
