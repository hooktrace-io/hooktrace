import logging
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from opentelemetry import trace

from webhook_inspector.domain.entities.captured_request import CapturedRequest
from webhook_inspector.domain.entities.endpoint import Endpoint
from webhook_inspector.domain.exceptions import EndpointNotFoundError
from webhook_inspector.domain.ports.blob_storage import BlobStorage
from webhook_inspector.domain.ports.endpoint_repository import EndpointRepository
from webhook_inspector.domain.ports.metrics_collector import MetricsCollector
from webhook_inspector.domain.ports.request_repository import RequestRepository
from webhook_inspector.domain.services.body_parsers import (
    extract_stripe_event_type,
    parse_form_params,
)
from webhook_inspector.domain.services.hmac.base import ValidationResult
from webhook_inspector.domain.services.hmac.factory import get_validator
from webhook_inspector.domain.services.integration_detector import detect_integration
from webhook_inspector.infrastructure.crypto.secrets import decrypt_secret

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

# Re-export for backward compat with callers that import from this module.
__all__ = ["CaptureRequest", "EndpointNotFoundError"]


@dataclass
class CaptureRequest:
    endpoint_repo: EndpointRepository
    request_repo: RequestRepository
    blob_storage: BlobStorage
    inline_threshold: int
    metrics: MetricsCollector
    secrets_key: bytes  # 32-byte AES-256 key decoded from Settings.secrets_encryption_key
    # notifier dropped — NOTIFY now happens in request_repo.save() transactionally
    # schema_queue removed — enqueue happens post-commit in the route via BackgroundTasks

    async def execute(
        self,
        token: str,
        method: str,
        path: str,
        query_string: str | None,
        headers: dict[str, str],
        body: bytes,
        source_ip: str,
    ) -> tuple[CapturedRequest, Endpoint]:
        start = time.monotonic()

        with _tracer.start_as_current_span("capture") as capture_span:
            capture_span.set_attribute("method", method.upper())

            with _tracer.start_as_current_span("endpoint.lookup"):
                endpoint = await self.endpoint_repo.find_by_token(token)
                if endpoint is None:
                    capture_span.set_attribute("result", "endpoint_not_found")
                    raise EndpointNotFoundError(token)

            # Always set signature_status — NEVER leave it None. Aggregation queries
            # GROUP BY signature_status; NULL rows break the cross-tab histogram.
            if endpoint.signature_provider and endpoint.signature_secret_encrypted:
                with _tracer.start_as_current_span("hmac.validate") as hmac_span:
                    validator = get_validator(endpoint.signature_provider)
                    if validator is not None:
                        try:
                            secret = decrypt_secret(
                                self.secrets_key, endpoint.signature_secret_encrypted
                            )
                            result = validator.validate(body=body, headers=headers, secret=secret)
                            signature_status = result.value
                        except InvalidTag:
                            logger.error(
                                "decrypt_secret_failed",
                                extra={"endpoint_id": str(endpoint.id)},
                            )
                            signature_status = ValidationResult.INVALID.value
                    else:
                        logger.warning(
                            "unknown_signature_provider",
                            extra={"provider": endpoint.signature_provider},
                        )
                        signature_status = ValidationResult.NO_PROVIDER.value
                    hmac_span.set_attribute("status", signature_status)
                    hmac_span.set_attribute("provider", endpoint.signature_provider)
            else:
                signature_status = ValidationResult.NO_PROVIDER.value

            content_type = headers.get("content-type", "")
            form_params = parse_form_params(body, content_type)
            user_agent = headers.get("user-agent", "")
            with _tracer.start_as_current_span("integration.detect") as int_span:
                integration, event_type = detect_integration(
                    headers=headers,
                    user_agent=user_agent,
                    form_params=form_params,
                )
                if integration == "stripe":
                    event_type = extract_stripe_event_type(body)
                int_span.set_attribute("integration", integration or "none")
                if event_type:
                    int_span.set_attribute("event_type", event_type)

            captured = CapturedRequest.create(
                endpoint_id=endpoint.id,
                method=method.upper(),
                path=path,
                query_string=query_string,
                headers=headers,
                body=body,
                source_ip=source_ip,
                inline_threshold_bytes=self.inline_threshold,
                signature_status=signature_status,
                detected_integration=integration,
                detected_event_type=event_type,
            )

            if captured.blob_key is not None:
                with _tracer.start_as_current_span("blob.offload") as blob_span:
                    blob_span.set_attribute("body_bytes", len(body))
                    try:
                        await self.blob_storage.put(captured.blob_key, body)
                    except Exception:
                        blob_span.set_attribute("error", True)
                        logger.exception(
                            "blob_storage_put_failed", extra={"key": captured.blob_key}
                        )
                        # Downgrade: drop blob reference; keep metadata
                        captured = CapturedRequest(
                            id=captured.id,
                            endpoint_id=captured.endpoint_id,
                            method=captured.method,
                            path=captured.path,
                            query_string=captured.query_string,
                            headers=captured.headers,
                            body_preview=None,
                            body_size=captured.body_size,
                            blob_key=None,
                            source_ip=captured.source_ip,
                            received_at=captured.received_at,
                            signature_status=captured.signature_status,
                            detected_integration=captured.detected_integration,
                            detected_event_type=captured.detected_event_type,
                        )

            with _tracer.start_as_current_span("db.insert"):
                await self.request_repo.save(captured)
                await self.endpoint_repo.increment_request_count(endpoint.id)

            capture_span.set_attribute("body_offloaded", captured.blob_key is not None)
            capture_span.set_attribute("body_size", captured.body_size)

        duration = time.monotonic() - start
        self.metrics.request_captured(
            method=captured.method,
            body_offloaded=captured.blob_key is not None,
            body_size=captured.body_size,
            duration_seconds=duration,
        )

        return captured, endpoint
