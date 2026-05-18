"""Tests for schema_inferer — pure-function schema inference and recursive diff."""

import json

from webhook_inspector.domain.services.schema_inferer import diff_schemas, infer_schema

# ---------------------------------------------------------------------------
# infer_schema tests
# ---------------------------------------------------------------------------


class TestInferSchema:
    def test_infer_from_flat_object(self):
        body = json.dumps({"id": 1, "name": "foo"}).encode()
        schema = infer_schema(body)
        assert schema["properties"]["id"]["type"] == "integer"
        assert schema["properties"]["name"]["type"] == "string"

    def test_infer_from_nested_object(self):
        body = json.dumps({"data": {"object": {"amount": 4200, "currency": "eur"}}}).encode()
        schema = infer_schema(body)
        # depth 3: data → object → amount / currency
        assert schema["properties"]["data"]["type"] == "object"
        assert schema["properties"]["data"]["properties"]["object"]["type"] == "object"
        inner = schema["properties"]["data"]["properties"]["object"]["properties"]
        assert inner["amount"]["type"] == "integer"
        assert inner["currency"]["type"] == "string"

    def test_infer_returns_empty_on_invalid_json(self):
        assert infer_schema(b"not json") == {}

    def test_infer_returns_empty_on_non_utf8(self):
        assert infer_schema(b"\xff\xfe") == {}

    def test_infer_returns_empty_on_non_dict_root_string(self):
        assert infer_schema(json.dumps("hello").encode()) == {}

    def test_infer_returns_empty_on_non_dict_root_array(self):
        assert infer_schema(json.dumps([1, 2, 3]).encode()) == {}

    def test_infer_returns_empty_on_non_dict_root_number(self):
        assert infer_schema(json.dumps(42).encode()) == {}

    def test_infer_returns_empty_on_non_dict_root_null(self):
        assert infer_schema(json.dumps(None).encode()) == {}

    def test_infer_returns_empty_on_oversized_body(self):
        # 10 MB body — well above the 1 MB cap
        big_body = b"x" * (10 * 1024 * 1024)
        assert infer_schema(big_body) == {}


# ---------------------------------------------------------------------------
# diff_schemas tests
# ---------------------------------------------------------------------------

# Helpers to build minimal JSON-Schema fragments.


def _flat_schema(*fields: str) -> dict:
    """Produce a simple object schema whose properties are all strings."""
    return {
        "type": "object",
        "properties": {f: {"type": "string"} for f in fields},
    }


def _nested_schema_without_currency() -> dict:
    """Schema for {"data": {"object": {"amount": integer}}}."""
    return {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "object": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "integer"},
                        },
                    }
                },
            }
        },
    }


def _nested_schema_with_currency() -> dict:
    """Schema for {"data": {"object": {"amount": integer, "currency": string}}}."""
    return {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {
                    "object": {
                        "type": "object",
                        "properties": {
                            "amount": {"type": "integer"},
                            "currency": {"type": "string"},
                        },
                    }
                },
            }
        },
    }


class TestDiffSchemas:
    def test_diff_detects_added_top_level_field(self):
        old = _flat_schema("id")
        new = _flat_schema("id", "name")
        result = diff_schemas(old, new)
        assert result["added"] == ["name"]
        assert result["removed"] == []
        assert result["changed"] == []

    def test_diff_detects_added_nested_field(self):
        old = _nested_schema_without_currency()
        new = _nested_schema_with_currency()
        result = diff_schemas(old, new)
        assert result["added"] == ["data.object.currency"]
        assert result["removed"] == []
        assert result["changed"] == []

    def test_diff_detects_nested_type_change(self):
        old = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer"},
                    },
                }
            },
        }
        new = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "string"},
                    },
                }
            },
        }
        result = diff_schemas(old, new)
        assert result["changed"] == ["data.amount"]
        assert result["added"] == []
        assert result["removed"] == []

    def test_diff_detects_removed_field(self):
        old = _flat_schema("id", "name")
        new = _flat_schema("id")
        result = diff_schemas(old, new)
        assert result["removed"] == ["name"]
        assert result["added"] == []
        assert result["changed"] == []

    def test_diff_empty_when_identical(self):
        schema = _flat_schema("id", "name")
        result = diff_schemas(schema, schema)
        assert result == {"added": [], "removed": [], "changed": []}

    def test_diff_type_change_does_not_recurse_into_object(self):
        """When a field changes from object → string, sub-fields must NOT appear."""
        old = {
            "type": "object",
            "properties": {
                "payload": {
                    "type": "object",
                    "properties": {
                        "nested_field": {"type": "string"},
                    },
                }
            },
        }
        new = {
            "type": "object",
            "properties": {
                "payload": {"type": "string"},
            },
        }
        result = diff_schemas(old, new)
        assert result["changed"] == ["payload"]
        assert result["added"] == []
        assert result["removed"] == []

    def test_diff_handles_missing_properties_key(self):
        """Empty schema {} (no 'properties') vs schema with one field → field in added."""
        old: dict = {}
        new = _flat_schema("status")
        result = diff_schemas(old, new)
        assert result["added"] == ["status"]
        assert result["removed"] == []
        assert result["changed"] == []
