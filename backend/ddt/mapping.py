"""The mapping file: the only thing a person or a model edits.

A mapping is a YAML document with one JSONata expression per output field:

    ddt: 1
    source: {file: henderson.csv, sha256: ..., encoding: utf-8}
    schema: occurrence.schema.json
    fields:
      scientificName: '$trim(ScrapedName)'
      location.stateProvince: 'stateProvince = "Colo" ? "Colorado" : stateProvince'

Field keys are dotted paths into the output record. Each expression is evaluated against one
input row (an object of column name -> string, with empty cells left out) and its result becomes
that field's value; an expression with no result leaves the field out of the record.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import jsonata
import yaml

FORMAT_VERSION = 1

# JSONata built-ins whose result depends on something other than the input row.
NONDETERMINISTIC = {"now", "millis", "random"}

_CHILD_ATTRS = (
    "steps", "arguments", "procedure", "lhs", "rhs", "expressions", "condition", "then", "_else",
    "expression", "body", "stages", "predicate", "group", "terms", "lhs_object", "rhs_object",
    "rhs_terms", "pattern", "update", "delete", "expr",
)  # fmt: skip


class MappingError(ValueError):
    """The mapping document or one of its expressions is not acceptable."""


@dataclass
class Expression:
    source: str
    compiled: jsonata.Jsonata
    # Names used as the first step of a path: candidates for input column references.
    names: set[str]


def _walk(node, visit, seen: set[int]) -> None:
    if node is None or isinstance(node, str | int | float | bool):
        return
    if isinstance(node, list | tuple):
        for item in node:
            _walk(item, visit, seen)
        return
    if not hasattr(node, "type") or id(node) in seen:
        return
    seen.add(id(node))
    visit(node)
    for attr in _CHILD_ATTRS:
        _walk(getattr(node, attr, None), visit, seen)


def compile_expression(source: str) -> Expression:
    """Parse one JSONata expression, rejecting anything non-deterministic."""
    if not isinstance(source, str) or not source.strip():
        raise MappingError("expression is empty")
    try:
        compiled = jsonata.Jsonata(source)
    except Exception as exc:  # the parser raises JException and plain Python errors alike
        raise MappingError(f"not valid JSONata: {exc}") from exc

    banned: set[str] = set()
    names: set[str] = set()

    def visit(node) -> None:
        if node.type == "variable" and node.value in NONDETERMINISTIC:
            banned.add(f"${node.value}")
        elif node.type == "name":
            names.add(node.value)
        elif node.type == "path" and node.steps and node.steps[0].type == "name":
            names.add(node.steps[0].value)

    _walk(compiled.ast, visit, set())
    if banned:
        raise MappingError(
            f"{', '.join(sorted(banned))} is not allowed: its result changes between runs"
        )
    return Expression(source=source, compiled=compiled, names=names)


@dataclass
class Mapping:
    schema: str
    fields: dict[str, str] = field(default_factory=dict)
    source: dict = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, text: str) -> Mapping:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise MappingError(f"not valid YAML: {exc}") from exc
        if not isinstance(doc, dict):
            raise MappingError("a mapping must be a YAML object")
        if doc.get("ddt") != FORMAT_VERSION:
            raise MappingError(f"expected `ddt: {FORMAT_VERSION}` at the top of the mapping")
        unknown = set(doc) - {"ddt", "source", "schema", "fields"}
        if unknown:
            raise MappingError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
        fields = doc.get("fields") or {}
        if not isinstance(fields, dict):
            raise MappingError("`fields` must map output field paths to expressions")
        for path, expr in fields.items():
            if not isinstance(path, str) or not isinstance(expr, str):
                raise MappingError(f"field {path!r}: path and expression must both be strings")
        schema = doc.get("schema")
        if not isinstance(schema, str):
            raise MappingError("`schema` must be the path of a JSON Schema file")
        source = doc.get("source") or {}
        if not isinstance(source, dict):
            raise MappingError("`source` must be an object")
        return cls(schema=schema, fields=dict(fields), source=source)

    def to_yaml(self) -> str:
        doc = {
            "ddt": FORMAT_VERSION,
            "source": self.source,
            "schema": self.schema,
            "fields": self.fields,
        }
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100)

    def copy(self) -> Mapping:
        return Mapping(schema=self.schema, fields=dict(self.fields), source=dict(self.source))


# --- target schema helpers -------------------------------------------------------------------


def _resolve(schema: dict, root: dict) -> dict:
    """Follow local `$ref`s (`#/$defs/x`); anything else is returned as it is."""
    hops = 0
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str) and hops < 20:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            break
        target = root
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {})
        schema, hops = target, hops + 1
    return schema if isinstance(schema, dict) else {}


def schema_for_path(root: dict, path: str) -> dict | None:
    """Return the subschema for a dotted field path, or None if the schema does not declare it."""
    node = _resolve(root, root)
    for part in path.split("."):
        props = node.get("properties")
        if not isinstance(props, dict) or part not in props:
            return None
        node = _resolve(props[part], root)
    return node


def schema_paths(root: dict) -> list[str]:
    """Every leaf field path the schema declares, in schema order."""
    out: list[str] = []

    def visit(node: dict, prefix: str, depth: int) -> None:
        node = _resolve(node, root)
        props = node.get("properties")
        if isinstance(props, dict) and props and depth < 8:
            for name, sub in props.items():
                visit(sub, f"{prefix}{name}.", depth + 1)
        elif prefix:
            out.append(prefix[:-1])

    visit(root, "", 0)
    return out


def check_field(root_schema: dict, path: str, expression: str) -> Expression:
    """Validate one field edit: the path must be in the schema and the expression must compile."""
    if not path or any(not part for part in path.split(".")):
        raise MappingError(f"{path!r} is not a valid field path")
    if schema_for_path(root_schema, path) is None:
        raise MappingError(f"the target schema has no field {path!r}")
    return compile_expression(expression)


def check_mapping(mapping: Mapping, root_schema: dict) -> dict[str, Expression]:
    """Compile every field, raising MappingError naming all the fields that fail."""
    compiled, problems = {}, []
    paths = list(mapping.fields)
    for path, expr in mapping.fields.items():
        try:
            compiled[path] = check_field(root_schema, path, expr)
        except MappingError as exc:
            problems.append(f"{path}: {exc}")
        if any(other.startswith(path + ".") for other in paths):
            problems.append(f"{path}: is both a field and the parent of another field")
    if problems:
        raise MappingError("; ".join(problems))
    return compiled
