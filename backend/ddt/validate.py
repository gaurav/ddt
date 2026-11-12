"""Check output records against the target JSON Schema, attributing each problem to a field."""

from __future__ import annotations

from jsonschema import Draft202012Validator, FormatChecker

from .engine import RowError


def make_validator(schema: dict) -> Draft202012Validator:
    Draft202012Validator.check_schema(schema)
    # Without a format checker, `"format": "date"` is an annotation and never fails.
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_records(validator: Draft202012Validator, records: list[dict]) -> list[RowError]:
    errors: list[RowError] = []
    for n, record in enumerate(records, start=1):
        for err in validator.iter_errors(record):
            parent = ".".join(str(p) for p in err.absolute_path)
            if err.validator == "required" and isinstance(err.instance, dict):
                # One error per missing property, filed under that property so it shows up in
                # the column where the value should have been.
                for name in err.validator_value:
                    if name not in err.instance:
                        path = f"{parent}.{name}" if parent else name
                        errors.append(RowError(n, path, "schema", "required value is missing"))
            else:
                errors.append(RowError(n, parent, "schema", err.message))
    return errors
