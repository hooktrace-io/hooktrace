from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class InferredSchema:
    id: UUID
    endpoint_id: UUID
    integration: str
    event_type: str | None
    schema_json: dict[str, Any]
    sample_count: int
    version: int
    last_field_added_at: datetime | None
    created_at: datetime
    updated_at: datetime
