"""Central JSON Schema validation for tool definitions and invocation payloads."""

from typing import Any

from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.validators import validator_for

from copilot.contracts import JsonObject
from copilot.tools.exceptions import ToolDefinitionValidationError, ToolValidationError


def validate_schema_definition(schema: dict[str, Any], label: str) -> None:
    """Reject malformed registered schemas before a plugin can be discovered."""
    try:
        validator_for(schema).check_schema(schema)
    except SchemaError as exc:
        raise ToolDefinitionValidationError(
            f"Tool {label} schema is not a valid JSON Schema"
        ) from exc


def validate_payload(payload: JsonObject, schema: dict[str, Any], label: str) -> None:
    """Validate one JSON payload without including sensitive values in the error."""
    validator_class = validator_for(schema)
    validator = validator_class(schema, format_checker=FormatChecker())
    try:
        validator.validate(payload.root)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "root"
        raise ToolValidationError(
            f"Tool {label} failed schema validation at '{location}' ({exc.validator})"
        ) from exc
