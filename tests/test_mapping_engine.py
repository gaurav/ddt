import json

import pytest

from ddt.engine import JsonataEngine, to_jsonl
from ddt.mapping import (
    Mapping,
    MappingError,
    check_mapping,
    compile_expression,
    schema_for_path,
    schema_paths,
)
from ddt.validate import make_validator, validate_records

SCHEMA = {
    "type": "object",
    "required": ["id", "when"],
    "properties": {
        "id": {"type": "string"},
        "count": {"type": "integer"},
        "when": {"type": "string", "format": "date"},
        "place": {"$ref": "#/$defs/place"},
    },
    "$defs": {
        "place": {
            "type": "object",
            "required": ["state"],
            "properties": {"state": {"enum": ["Colorado"]}, "town": {"type": "string"}},
        }
    },
}
COLUMNS = ["ID", "N", "Date", "State"]
ROWS = [["a", "3", "'1907-02-09", "Colo"], ["b", "x", "", "Colorado"], ["c", "", "1907", ""]]
FIELDS = {
    "place.state": 'State = "Colo" ? "Colorado" : State',
    "count": "$number(N)",
    "when": """$substringAfter(Date, "'")""",
    "id": "ID",
}


def run(fields):
    mapping = Mapping(schema="s.json", fields=fields)
    compiled = check_mapping(mapping, SCHEMA)
    result = JsonataEngine(COLUMNS, ROWS).run(compiled, SCHEMA)
    result.errors.extend(validate_records(make_validator(SCHEMA), result.records))
    return result


def test_one_record_per_row_in_schema_order():
    result = run(FIELDS)
    assert len(result.records) == len(ROWS)
    assert result.records[0] == {
        "id": "a", "count": 3, "when": "1907-02-09", "place": {"state": "Colorado"}
    }  # fmt: skip
    assert list(result.records[0]) == ["id", "count", "when", "place"]
    assert isinstance(result.records[0]["count"], int)


def test_errors_are_recorded_per_row_and_field_not_raised():
    errors = {(e.row, e.field, e.kind) for e in run(FIELDS).errors}
    assert (2, "count", "transform") in errors  # $number("x")
    assert (2, "when", "schema") in errors  # empty cell -> field omitted -> required
    assert (3, "when", "schema") in errors  # "1907" is not a date
    assert (3, "place.state", "schema") not in errors  # no place object at all for row 3
    assert not any(e.row == 1 for e in run(FIELDS).errors)


def test_empty_cells_give_no_value_and_omit_the_field():
    assert "count" not in run(FIELDS).records[2]


@pytest.mark.parametrize("expr", ["$now()", "$millis()", "$random()", "($f := $random; $f())"])
def test_nondeterministic_functions_are_rejected(expr):
    with pytest.raises(MappingError, match="changes between runs"):
        compile_expression(expr)


def test_bad_expressions_and_unknown_fields_are_rejected():
    with pytest.raises(MappingError, match="not valid JSONata"):
        compile_expression("$trim(")
    with pytest.raises(MappingError, match="no field 'nope'"):
        check_mapping(Mapping(schema="s", fields={"nope": "ID"}), SCHEMA)
    with pytest.raises(MappingError, match="parent of another field"):
        check_mapping(Mapping(schema="s", fields={"place": "ID", "place.town": "ID"}), SCHEMA)


def test_column_references_are_extracted():
    names = compile_expression('$trim(`Scraped Name`) & (State = "x" ? a.b : $uppercase(c))').names
    assert {"Scraped Name", "State", "a", "c"} <= names


def test_schema_helpers_follow_local_refs():
    assert schema_for_path(SCHEMA, "place.state") == {"enum": ["Colorado"]}
    assert schema_for_path(SCHEMA, "place.nope") is None
    assert schema_paths(SCHEMA) == ["id", "count", "when", "place.state", "place.town"]


def test_yaml_round_trip_and_format_checks():
    mapping = Mapping(schema="s.json", fields=FIELDS, source={"file": "x.csv"})
    again = Mapping.from_yaml(mapping.to_yaml())
    assert again == mapping and list(again.fields) == list(FIELDS)
    with pytest.raises(MappingError, match="ddt: 1"):
        Mapping.from_yaml("fields: {}")
    with pytest.raises(MappingError, match="unknown top-level"):
        Mapping.from_yaml("ddt: 1\nschema: s\nscript: rm -rf /\n")


def test_jsonl_is_compact_and_ordered():
    text = to_jsonl(run(FIELDS).records)
    assert text.count("\n") == 3
    assert json.loads(text.splitlines()[0])["id"] == "a"


def test_several_missing_required_fields_give_one_error_each():
    validator = make_validator(SCHEMA)
    errors = validate_records(validator, [{}, {"id": "a", "when": "1907-02-09", "place": {}}])
    assert sorted((e.row, e.field) for e in errors) == [(1, "id"), (1, "when"), (2, "place.state")]
