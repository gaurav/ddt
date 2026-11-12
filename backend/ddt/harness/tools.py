"""The complete set of things a model can do. There is nothing else: no files, no shell, no SQL.

Read tools look at the input data, the target schema and the current validation state. The only
write tools are `set_field` and `remove_field`, and both go through `Project.set_mapping`, which
checks and runs an edit before accepting it. The input file and the output records are never
writable: output only ever comes from running the mapping.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from ..engine import MISSING
from ..mapping import MappingError, compile_expression
from ..project import Project

MAX_SAMPLE_ROWS = 20
MAX_CELL = 300


def _fn(name: str, description: str, properties: dict | None = None, required=()) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


_STR = {"type": "string"}

TOOLS: list[dict] = [
    _fn("get_schema", "Return the target JSON Schema that output records must satisfy."),
    _fn(
        "get_mapping",
        "Return the current mapping (output field path -> JSONata expression), the schema "
        "fields that have no mapping yet, and input columns holding data that no field reads.",
    ),
    _fn(
        "list_columns",
        "List the input CSV columns with how many rows are filled, how many distinct values "
        "each has, and the type its values look like.",
    ),
    _fn(
        "profile_column",
        "Show the most common values of one input column, with counts.",
        {"column": _STR},
        ["column"],
    ),
    _fn(
        "sample_rows",
        "Return a few input rows (empty cells left out). Optionally only the given columns, "
        "and only rows where `where_column` contains the text `contains`.",
        {
            "columns": {"type": "array", "items": _STR},
            "where_column": _STR,
            "contains": _STR,
            "limit": {"type": "integer", "minimum": 1, "maximum": MAX_SAMPLE_ROWS},
        },
    ),
    _fn("validation_summary", "Count current errors per output field, and rows with errors."),
    _fn(
        "errors_for_field",
        "Show the most common error messages for one output field, with example row numbers "
        "and the input values of one example row.",
        {"field": _STR},
        ["field"],
    ),
    _fn(
        "preview_expression",
        "Evaluate a JSONata expression against every input row WITHOUT changing the mapping. "
        "Returns the most common results and how many rows failed or gave no value.",
        {"expression": _STR},
        ["expression"],
    ),
    _fn(
        "set_field",
        "Set (add or replace) the JSONata expression for one output field. `path` is a field "
        "of the target schema, dotted for nested objects. The edit is rejected, changing "
        "nothing, if the expression does not parse or the field is not in the schema. "
        "Returns how many records changed and the error counts before and after.",
        {"path": _STR, "expression": _STR},
        ["path", "expression"],
    ),
    _fn(
        "remove_field",
        "Remove one output field from the mapping.",
        {"path": _STR},
        ["path"],
    ),
]

WRITE_TOOLS = {"set_field", "remove_field"}


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_CELL:
        return value[:MAX_CELL] + "…"
    return value


def _input_row(project: Project, n: int, columns: list[str] | None = None) -> dict:
    row = project.engine.inputs[n - 1]
    return {"_row": n, **{k: _clip(v) for k, v in row.items() if not columns or k in columns}}


def _need_column(project: Project, column: Any) -> str:
    if column not in project.columns:
        raise ValueError(f"there is no input column {column!r}; use list_columns")
    return column


def _get_schema(project: Project, args: dict) -> Any:
    return project.schema


def _get_mapping(project: Project, args: dict) -> Any:
    return {
        "fields": project.mapping.fields,
        "unmapped_schema_fields": project.unmapped_fields(),
        "unused_input_columns": project.unused_columns(),
    }


def _list_columns(project: Project, args: dict) -> Any:
    return project.store.profile("input")


def _profile_column(project: Project, args: dict) -> Any:
    column = _need_column(project, args.get("column"))
    facet = project.store.facet("input", column, limit=25)
    facet["values"] = [{"value": _clip(v["value"]), "count": v["count"]} for v in facet["values"]]
    return facet


def _sample_rows(project: Project, args: dict) -> Any:
    columns = [_need_column(project, c) for c in args.get("columns") or []] or None
    limit = max(1, min(int(args.get("limit") or 5), MAX_SAMPLE_ROWS))
    filters = None
    if args.get("where_column"):
        column = _need_column(project, args["where_column"])
        filters = [{"column": column, "op": "contains", "value": args.get("contains") or ""}]
    page = project.store.rows("input", limit=limit, filters=filters)
    return {
        "matching_rows": page["total"],
        "rows": [_input_row(project, r["_row"], columns) for r in page["rows"]],
    }


def _validation_summary(project: Project, args: dict) -> Any:
    return {"rows": len(project.result.records), **project.store.error_counts()}


def _errors_for_field(project: Project, args: dict) -> Any:
    field = args.get("field")
    examples = project.store.error_examples(str(field))
    if not examples:
        return {"field": field, "errors": []}
    first = examples[0]["example_rows"][0]
    return {
        "field": field,
        "expression": project.mapping.fields.get(str(field)),
        "errors": examples,
        "example_input_row": _input_row(project, first),
        "example_output_record": project.result.records[first - 1],
    }


def _preview_expression(project: Project, args: dict) -> Any:
    expr = compile_expression(args.get("expression"))
    counts: Counter[str] = Counter()
    failures: Counter[str] = Counter()
    no_value = 0
    # Not cached: a preview must not leave anything behind in the project.
    for obj in project.engine.inputs:
        try:
            value = expr.compiled.evaluate(obj)
        except Exception as exc:
            failures[str(exc)[:200]] += 1
            continue
        if value is None or value is MISSING:
            no_value += 1
        else:
            counts[json.dumps(_clip(value), ensure_ascii=False)] += 1
    return {
        "rows": len(project.engine.inputs),
        "no_value": no_value,
        "failed": sum(failures.values()),
        "failures": [{"message": m, "rows": n} for m, n in failures.most_common(5)],
        "distinct_results": len(counts),
        "most_common_results": [
            {"value": json.loads(v), "rows": n} for v, n in counts.most_common(15)
        ],
    }


def _set_field(project: Project, args: dict, author: str) -> Any:
    effect = project.set_field(str(args.get("path")), args.get("expression"), author=author)
    field_errors = project.store.error_counts()["by_field"].get(str(args.get("path")), {})
    return {"accepted": True, **effect, "errors_in_this_field": field_errors}


def _remove_field(project: Project, args: dict, author: str) -> Any:
    return {"accepted": True, **project.remove_field(str(args.get("path")), author=author)}


_READ = {
    "get_schema": _get_schema,
    "get_mapping": _get_mapping,
    "list_columns": _list_columns,
    "profile_column": _profile_column,
    "sample_rows": _sample_rows,
    "validation_summary": _validation_summary,
    "errors_for_field": _errors_for_field,
    "preview_expression": _preview_expression,
}
_WRITE = {"set_field": _set_field, "remove_field": _remove_field}

assert set(_READ) | set(_WRITE) == {t["function"]["name"] for t in TOOLS}
assert set(_WRITE) == WRITE_TOOLS


def call_tool(project: Project, name: str, arguments: str | dict | None, author: str) -> dict:
    """Run one tool call from a model. Never raises: problems come back as {"error": ...}."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return {"error": f"arguments are not valid JSON: {exc}"}
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be a JSON object"}
    try:
        if name in _WRITE:
            return {"result": _WRITE[name](project, arguments, author)}
        if name in _READ:
            return {"result": _READ[name](project, arguments)}
    except MappingError as exc:
        return {"error": f"rejected, nothing was changed: {exc}"}
    except (ValueError, TypeError, KeyError) as exc:
        return {"error": str(exc)}
    return {"error": f"there is no tool named {name!r}"}
