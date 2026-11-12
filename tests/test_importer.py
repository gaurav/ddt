import pytest

from ddt.importer import UTF8_WITH_FALLBACK, detect_encoding, import_csv, unique_headers
from ddt.store import Store


def test_plain_utf8_with_bom():
    result = import_csv("\ufeffa,b\r\n1,é\r\n".encode())
    assert result.columns == ["a", "b"]
    assert result.rows == [["1", "é"]]
    assert result.info.encoding == "utf-8"
    assert result.info.header_renames == []


def test_henderson_mixed_encoding_and_duplicate_headers(henderson_raw):
    result = import_csv(henderson_raw)
    assert result.info.encoding == UTF8_WITH_FALLBACK
    assert result.info.fallback_bytes > 0
    assert len(result.rows) == 40 and len(result.columns) == 68
    # Both copies of a repeated header are numbered; neither keeps the plain name.
    assert "scientificName" not in result.columns
    assert {"scientificName_1", "scientificName_2"} <= set(result.columns)
    assert {"original": " Name", "name": "Name", "index": 55} in result.info.header_renames
    text = "\n".join(",".join(r) for r in result.rows)
    assert "\ufffd" not in text  # nothing was replaced with the "unknown character" mark
    assert "Soják" in text  # a Latin-1 byte, read through the fallback


def test_cushman_mac_roman_and_bare_cr(cushman_raw):
    assert detect_encoding(cushman_raw)[0] == "mac_roman"
    result = import_csv(cushman_raw)
    assert len(result.rows) == 42 and len(result.columns) == 90
    assert result.info.ragged_rows == []
    text = "\n".join(",".join(r) for r in result.rows)
    # "Carrâe" is a typo in the source ("Vieux Carré"); it shows the bytes were read as Mac Roman
    # and kept as they are, not corrected.
    assert "Vieux Carrâe" in text and "reads…" in text and "\ufffd" not in text


def test_explicit_encoding_is_not_marked_detected(cushman_raw):
    assert import_csv(cushman_raw, "mac_roman").info.encoding_detected is False


def test_quoted_newlines_and_short_rows():
    result = import_csv(b'a,b,c\n"x\ny",2\n')
    assert result.rows == [["x\ny", "2", ""]]
    assert result.info.ragged_rows == [{"row": 1, "cells": 2}]


def test_extra_cells_are_refused_not_dropped():
    with pytest.raises(ValueError, match="refusing to drop"):
        import_csv(b"a,b\n1,2,3\n")


def test_unique_headers_handles_blank_names():
    names, renames = unique_headers(["a", "", "a"])
    assert names == ["a_1", "column_2", "a_2"]
    assert len(renames) == 3


def test_store_paging_filters_facets_and_profile():
    rows = [["1", "x", "2001-01-01"], ["2", "y", ""], ["3", "x", "2003-03-03"]]
    store = Store(["n", "s", "d"], rows)
    page = store.rows("input", offset=1, limit=1)
    assert page["total"] == 3 and page["rows"][0]["n"] == "2"
    hit = store.rows("input", filters=[{"column": "s", "op": "in", "values": ["x"]}])
    assert [r["_row"] for r in hit["rows"]] == [1, 3]
    assert store.rows("input", filters=[{"column": "d", "op": "empty"}])["total"] == 1
    assert store.rows("input", sort="n", descending=True)["rows"][0]["n"] == "3"
    numbers = Store(["n"], [["10"], ["9"], ["x"], [""], ["100"]])
    assert [r["n"] for r in numbers.rows("input", sort="n")["rows"]] == [
        "9",
        "10",
        "100",
        "x",
        None,
    ]
    facet = store.facet("input", "s", filters=[{"column": "s", "op": "in", "values": ["y"]}])
    # A column's own filter does not narrow its facet, so other values stay selectable.
    assert facet["values"] == [{"value": "x", "count": 2}, {"value": "y", "count": 1}]
    types = {p["name"]: p["type"] for p in store.profile("input")}
    assert types == {"n": "integer", "s": "text", "d": "date"}
    with pytest.raises(ValueError):
        store.rows("input", filters=[{"column": 'x"; DROP TABLE input; --', "op": "empty"}])


def test_fallback_decodes_only_the_invalid_bytes():
    # Valid UTF-8, then a lone Latin-1 byte, then 0x81, which cp1252 leaves undefined.
    valid = "\u00e9" * 8
    result = import_csv(b"a\n" + valid.encode() + b" \xe9 \x81\n")
    assert result.info.encoding == UTF8_WITH_FALLBACK and result.info.fallback_bytes == 2
    assert result.rows == [[valid + " \u00e9 \u0081"]]


def test_cp1252_with_an_undefined_byte_does_not_fail():
    assert import_csv(b"a\ncaf\xe9 \x81\n").rows == [["caf\u00e9 \u0081"]]
    assert import_csv(b"a\n\x81\n", "cp1252").rows == [["\u0081"]]
