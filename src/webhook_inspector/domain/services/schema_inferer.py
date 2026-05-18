"""Schema inference + recursive diff for webhook bodies.

Pure functions — no I/O. The worker (PR3.3 UpdateInferredSchema) calls these
to compute (a) the schema of the new payload and (b) the path-style drift
list against the cumulative schema for the same integration x event_type.

LIMITATIONS (intentional, V3-acceptable):
- Bodies > 1MB are skipped (return empty/None). Aligns with the inline-vs-R2
  threshold logic in PR4 / PR7. Larger payloads aren't worth inferring on
  the synchronous hot path.
- Bare JSON primitives (string, number, array root, null) return empty.
  Drift detection is meaningless for these — the worker treats absence as
  "no schema available" and skips.
- Non-UTF-8 / malformed JSON return empty defensively (never raise).
"""

import json
from typing import Any

from genson import SchemaBuilder

_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1MB — aligns with PR4/PR7 body cap


def infer_schema(body: bytes) -> dict[str, Any]:
    """Return a JSON Schema (Draft-7 style) inferred from the body.

    Empty dict on any failure — including oversized bodies, non-UTF-8,
    malformed JSON, or non-dict root types.
    """
    if len(body) > _MAX_BODY_BYTES:
        return {}
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    try:
        parsed = json.loads(decoded)
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    builder = SchemaBuilder()
    builder.add_object(parsed)
    result: dict[str, Any] = builder.to_schema()
    return result


def diff_schemas(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[str]]:
    """Recursive diff between two JSON schemas.

    Returns a dict with three lists (always present, may be empty) :
    - "added"   : path-style field names that exist in new but not old
    - "removed" : path-style field names that exist in old but not new
    - "changed" : path-style field names whose type or schema differs

    Field paths use dot notation : top-level "foo", nested "data.object.amount".
    """
    added: list[str] = []
    removed: list[str] = []
    changed: list[str] = []

    _diff_recursive(old, new, path="", added=added, removed=removed, changed=changed)

    return {"added": added, "removed": removed, "changed": changed}


def _diff_recursive(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    path: str,
    added: list[str],
    removed: list[str],
    changed: list[str],
) -> None:
    """Walk both schemas' "properties" recursively. Each branch handles one of :
    added (key in new only), removed (key in old only), or changed/recursed
    (key in both but type or sub-schema differs).
    """
    old_props = old.get("properties", {}) if isinstance(old, dict) else {}
    new_props = new.get("properties", {}) if isinstance(new, dict) else {}

    old_keys = set(old_props.keys())
    new_keys = set(new_props.keys())

    for key in new_keys - old_keys:
        added.append(_join(path, key))
    for key in old_keys - new_keys:
        removed.append(_join(path, key))

    for key in old_keys & new_keys:
        old_sub = old_props[key]
        new_sub = new_props[key]
        old_type = old_sub.get("type") if isinstance(old_sub, dict) else None
        new_type = new_sub.get("type") if isinstance(new_sub, dict) else None

        if old_type != new_type:
            changed.append(_join(path, key))
            # Don't recurse further on a type change — the sub-shape is
            # unrelated by definition.
            continue

        if old_type == "object":
            _diff_recursive(
                old_sub,
                new_sub,
                path=_join(path, key),
                added=added,
                removed=removed,
                changed=changed,
            )
        elif old_sub != new_sub:
            # Catch-all for non-object sub-schemas that differ beyond the
            # top-level `type` field — anyOf / oneOf / enum / format /
            # constraint changes. Without this, mutations inside opaque
            # sub-schemas would be silently invisible.
            changed.append(_join(path, key))


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key
