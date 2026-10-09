"""Validate create-field values against Jira Server metadata without guessing plugin formats."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from typing import Any

RESERVED_FIELDS = {"project", "issuetype", "parent", "summary", "description"}
REFERENCE_TYPES = {
    "option",
    "priority",
    "version",
    "component",
    "issuetype",
    "project",
    "resolution",
    "securitylevel",
    "user",
    "group",
}


def _reference_error(value: Any, kind: str, allowed: list) -> str | None:
    keys = {"name"} if kind in {"user", "group"} else {"id"}
    if not isinstance(value, dict) or set(value) != keys:
        return f"Expected an object containing only {next(iter(keys))}"
    identifier = value[next(iter(keys))]
    if not isinstance(identifier, str) or not identifier.strip():
        return "Expected a nonempty string identifier"
    if allowed and not any(all(item.get(k) == v for k, v in value.items()) for item in allowed):
        return "Value is not one of the available options"
    return None


def value_error(value: Any, schema: dict, allowed: list) -> str | None:
    kind = schema.get("type")
    if kind == "array":
        if not isinstance(value, list):
            return "Expected an array"
        for item in value:
            problem = value_error(item, {"type": schema.get("items")}, allowed)
            if problem:
                return problem
        return None
    if kind in REFERENCE_TYPES:
        return _reference_error(value, kind, allowed)
    if kind == "number":
        if type(value) not in {int, float} or not math.isfinite(value):
            return "Expected a finite number"
        return None
    if kind in {"string", "date", "datetime"}:
        if not isinstance(value, str):
            return "Expected a string"
        if kind in {"date", "datetime"}:
            try:
                if kind == "date":
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    date.fromisoformat(value)
                else:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        raise ValueError
            except ValueError:
                return "Expected ISO date (YYYY-MM-DD) or datetime with timezone"
        if allowed and not any(value in (item.get("value"), item.get("name")) for item in allowed):
            return "Value is not one of the available options"
        return None
    return f"Unsupported field schema: {kind}; this field needs a dedicated adapter"


def validate_fields(payload: dict, metadata: dict) -> dict[str, str]:
    errors = {}
    for key in payload.keys() - metadata.keys() - {"parent"}:
        errors[key] = "Field is not available on the create screen"
    for key, field in metadata.items():
        value = payload.get(key)
        empty = (
            value is None
            or value == []
            or value == ""
            or (isinstance(value, str) and not value.strip())
        )
        if empty:
            if field.get("required") and (key in payload or not field.get("hasDefaultValue")):
                errors[key] = f"Required: {field.get('name', key)}"
            continue
        if key in {"project", "issuetype", "parent"}:
            continue
        schema = field.get("schema") or {}
        custom = schema.get("custom", "")
        # Plugins can reuse standard schema types with incompatible wire formats.
        if custom and (
            not custom.startswith("com.atlassian.jira.plugin.system.customfieldtypes:")
            or custom.endswith(":cascadingselect")
        ):
            errors[key] = f"Unsupported custom field: {field.get('name', key)}"
            continue
        problem = value_error(value, schema, field.get("allowedValues") or [])
        if problem:
            errors[key] = problem
    return errors
