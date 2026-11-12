"""Apply a mapping to input rows. Deterministic: same rows + same mapping = same records.

`Engine` is the seam for other mapping formats (YARRRML, LinkML-Map) later. `JsonataEngine`
evaluates each field's expression against each row on its own, which gives three things:

* every output value traces to exactly one expression;
* a failure is recorded against one row and one field instead of stopping the run;
* results are cached per expression, so editing one field re-evaluates only that field.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Protocol

from .mapping import Expression, _resolve

MISSING = object()  # the expression produced no value: the field is left out of the record


@dataclass(frozen=True)
class RowError:
    row: int  # 1-based input row number
    field: str
    kind: str  # "transform" (expression failed) or "schema" (record does not fit the schema)
    message: str


@dataclass
class RunResult:
    records: list[dict]  # exactly one per input row, in input order
    errors: list[RowError]


class Engine(Protocol):
    def run(self, fields: dict[str, Expression], root_schema: dict) -> RunResult: ...


def row_objects(columns: list[str], rows: list[list[str]]) -> list[dict[str, str]]:
    """Rows as objects of verbatim strings. Empty cells are left out, so an expression that
    reads one gets "no value" (and its field is omitted) instead of an empty string."""
    return [{c: v for c, v in zip(columns, row, strict=True) if v != ""} for row in rows]


def _normalise(value: Any) -> Any:
    """Make a JSONata result plain JSON: integral floats become ints, NaN/inf are refused."""
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("result is not a finite number")
        return int(value) if value.is_integer() and abs(value) < 2**53 else value
    if isinstance(value, dict):
        return {str(k): _normalise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normalise(v) for v in value]
    raise ValueError(f"result of type {type(value).__name__} cannot be written as JSON")


class JsonataEngine:
    def __init__(self, columns: list[str], rows: list[list[str]]):
        self.inputs = row_objects(columns, rows)
        # expression source -> one (value | MISSING | Exception) per row
        self._cache: dict[str, list[Any]] = {}

    def evaluate(self, expr: Expression) -> list[Any]:
        cached = self._cache.get(expr.source)
        if cached is not None:
            return cached
        out: list[Any] = []
        for obj in self.inputs:
            try:
                value = expr.compiled.evaluate(obj)
                # jsonata-python returns None both for "no value" and for JSON null; a mapping
                # has no use for a null that the schema would have to allow, so both mean omit.
                out.append(MISSING if value is None else _normalise(value))
            except Exception as exc:
                out.append(exc)
        self._cache[expr.source] = out
        return out

    def run(self, fields: dict[str, Expression], root_schema: dict) -> RunResult:
        columns = {path: self.evaluate(expr) for path, expr in fields.items()}
        records: list[dict] = []
        errors: list[RowError] = []
        for i in range(len(self.inputs)):
            record: dict = {}
            for path, values in columns.items():
                value = values[i]
                if value is MISSING:
                    continue
                if isinstance(value, Exception):
                    errors.append(RowError(i + 1, path, "transform", str(value) or repr(value)))
                    continue
                _set_path(record, path, value)
            records.append(order_like_schema(record, root_schema, root_schema))
        return RunResult(records=records, errors=errors)

    def forget_unused(self, in_use: set[str]) -> None:
        """Drop cached results for expressions no kept mapping version uses."""
        for source in list(self._cache):
            if source not in in_use:
                del self._cache[source]


def _set_path(record: dict, path: str, value: Any) -> None:
    *parents, leaf = path.split(".")
    node = record
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def order_like_schema(value: Any, schema: dict, root: dict) -> Any:
    """Order object keys as the schema declares them (undeclared keys follow, sorted)."""
    if not isinstance(value, dict):
        return value
    props = _resolve(schema, root).get("properties")
    props = props if isinstance(props, dict) else {}
    ordered = {k: order_like_schema(value[k], props[k], root) for k in props if k in value}
    for k in sorted(set(value) - set(props)):
        ordered[k] = value[k]
    return ordered


def to_jsonl(records: list[dict]) -> str:
    """The canonical output form: one compact JSON object per input row, in input order."""
    return "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        for r in records
    )
