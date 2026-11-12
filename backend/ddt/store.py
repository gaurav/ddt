"""DuckDB tables behind the grid: paging, sorting, filtering, facets and column profiles.

Three tables, all keyed by `_row` (the 1-based input row number):

* `input`  – one VARCHAR column per CSV column, values verbatim, empty cells as NULL;
* `output` – one VARCHAR column per mapped field (strings as they are, other values as JSON);
* `errors` – one row per transform or schema error: (_row, field, kind, message).
"""

from __future__ import annotations

import json
from typing import Any

import duckdb
import pyarrow as pa

from .engine import RowError

TABLES = ("input", "output")
FILTER_OPS = ("contains", "equals", "in", "empty", "not_empty")


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _get_path(record: dict, path: str) -> Any:
    node: Any = record
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _cell(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, columns: list[str], rows: list[list[str]]):
        self.con = duckdb.connect(":memory:")
        self.columns: dict[str, list[str]] = {"input": list(columns), "output": []}
        data = {"_row": pa.array(range(1, len(rows) + 1), pa.int64())}
        for i, name in enumerate(columns):
            data[name] = pa.array([r[i] or None for r in rows], pa.string())
        self._create("input", data)
        self.set_output([], [], [])

    def _create(self, table: str, data: dict[str, pa.Array]) -> None:
        arrow_table = pa.table(data)  # noqa: F841  (DuckDB reads it by variable name)
        self.con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM arrow_table")

    def set_output(self, fields: list[str], records: list[dict], errors: list[RowError]) -> None:
        self.columns["output"] = list(fields)
        data = {"_row": pa.array(range(1, len(records) + 1), pa.int64())}
        for path in fields:
            data[path] = pa.array([_cell(_get_path(r, path)) for r in records], pa.string())
        self._create("output", data)
        self._create(
            "errors",
            {
                "_row": pa.array([e.row for e in errors], pa.int64()),
                "field": pa.array([e.field for e in errors], pa.string()),
                "kind": pa.array([e.kind for e in errors], pa.string()),
                "message": pa.array([e.message for e in errors], pa.string()),
            },
        )

    # --- query building ----------------------------------------------------------------------

    def _where(
        self,
        table: str,
        filters: list[dict] | None,
        errors_only: bool,
        error_field: str | None,
        skip_column: str | None = None,
    ) -> tuple[str, list]:
        clauses, params = [], []
        for f in filters or []:
            column, op = f.get("column"), f.get("op")
            if column not in self.columns[table]:
                raise ValueError(f"unknown column {column!r}")
            if op not in FILTER_OPS:
                raise ValueError(f"unknown filter {op!r}")
            if column == skip_column:
                continue
            col = q(column)
            if op == "contains":
                clauses.append(f"contains(lower({col}), lower(?))")
                params.append(str(f.get("value", "")))
            elif op == "equals":
                clauses.append(f"{col} = ?")
                params.append(str(f.get("value", "")))
            elif op == "in":
                values = list(f.get("values") or [])
                parts = []
                if any(v is None for v in values):
                    parts.append(f"{col} IS NULL")
                strings = [str(v) for v in values if v is not None]
                if strings:
                    parts.append(f"{col} IN ({', '.join('?' * len(strings))})")
                    params.extend(strings)
                clauses.append("(" + " OR ".join(parts or ["FALSE"]) + ")")
            elif op == "empty":
                clauses.append(f"{col} IS NULL")
            else:
                clauses.append(f"{col} IS NOT NULL")
        if error_field:
            clauses.append("_row IN (SELECT _row FROM errors WHERE field = ?)")
            params.append(error_field)
        elif errors_only:
            clauses.append("_row IN (SELECT _row FROM errors)")
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _check_table(self, table: str) -> None:
        if table not in TABLES:
            raise ValueError(f"unknown table {table!r}")

    def rows(
        self,
        table: str,
        offset: int = 0,
        limit: int = 100,
        sort: str | None = None,
        descending: bool = False,
        filters: list[dict] | None = None,
        errors_only: bool = False,
        error_field: str | None = None,
    ) -> dict:
        self._check_table(table)
        where, params = self._where(table, filters, errors_only, error_field)
        order = "_row"
        if sort:
            if sort not in self.columns[table]:
                raise ValueError(f"unknown column {sort!r}")
            # Values are stored as text; numbers sort as numbers, then everything else as text.
            way = "DESC" if descending else "ASC"
            order = (
                f"TRY_CAST({q(sort)} AS DOUBLE) {way} NULLS LAST, {q(sort)} {way} NULLS LAST, _row"
            )
        total = self.con.execute(f"SELECT count(*) FROM {table}{where}", params).fetchone()[0]
        names = ["_row", *self.columns[table]]
        cur = self.con.execute(
            f"SELECT {', '.join(q(n) for n in names)} FROM {table}{where} "
            f"ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, max(1, min(int(limit), 1000)), max(0, int(offset))],
        )
        rows = [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
        # Errors for the rows on this page, so the grid can mark the cells.
        by_row: dict[int, dict[str, list[str]]] = {}
        if rows:
            ids = [r["_row"] for r in rows]
            marks = ", ".join("?" * len(ids))
            for row, fld, kind, message in self.con.execute(
                f"SELECT _row, field, kind, message FROM errors WHERE _row IN ({marks})", ids
            ).fetchall():
                by_row.setdefault(row, {}).setdefault(fld, []).append(f"{kind}: {message}")
        for r in rows:
            r["_errors"] = by_row.get(r["_row"], {})
        return {"total": total, "rows": rows}

    def facet(
        self,
        table: str,
        column: str,
        filters: list[dict] | None = None,
        errors_only: bool = False,
        error_field: str | None = None,
        limit: int = 50,
    ) -> dict:
        """Value counts for one column, under every active filter except that column's own."""
        self._check_table(table)
        if column not in self.columns[table]:
            raise ValueError(f"unknown column {column!r}")
        where, params = self._where(table, filters, errors_only, error_field, skip_column=column)
        col = q(column)
        distinct = self.con.execute(
            f"SELECT count(DISTINCT {col}) + max(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) "
            f"FROM {table}{where}",
            params,
        ).fetchone()[0]
        values = self.con.execute(
            f"SELECT {col}, count(*) AS n FROM {table}{where} GROUP BY 1 "
            f"ORDER BY n DESC, {col} NULLS LAST LIMIT ?",
            [*params, limit],
        ).fetchall()
        return {
            "column": column,
            "distinct": distinct or 0,
            "values": [{"value": v, "count": n} for v, n in values],
        }

    def profile(self, table: str) -> list[dict]:
        """Per column: filled and distinct counts and the type its values would all cast to."""
        self._check_table(table)
        names = self.columns[table]
        if not names:
            return []
        parts = []
        for n in names:
            c = q(n)
            parts.append(
                f"count({c}), count(DISTINCT {c}), count(TRY_CAST({c} AS BIGINT)), "
                f"count(TRY_CAST({c} AS DOUBLE)), "
                f"count(CASE WHEN lower({c}) IN ('true','false') THEN 1 END), "
                f"count(TRY_CAST({c} AS DATE))"
            )
        flat = self.con.execute(f"SELECT {', '.join(parts)} FROM {table}").fetchone()
        out = []
        for i, name in enumerate(names):
            filled, distinct, ints, nums, bools, dates = flat[i * 6 : i * 6 + 6]
            if filled == 0:
                kind = "empty"
            elif ints == filled:
                kind = "integer"
            elif nums == filled:
                kind = "number"
            elif bools == filled:
                kind = "boolean"
            elif dates == filled:
                kind = "date"
            else:
                kind = "text"
            out.append({"name": name, "filled": filled, "distinct": distinct, "type": kind})
        return out

    def error_counts(self) -> dict:
        total, rows = self.con.execute(
            "SELECT count(*), count(DISTINCT _row) FROM errors"
        ).fetchone()
        by_field: dict[str, dict[str, int]] = {}
        for fld, kind, n in self.con.execute(
            "SELECT field, kind, count(DISTINCT _row) FROM errors GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall():
            by_field.setdefault(fld, {})[kind] = n
        return {"errors": total, "rows_with_errors": rows, "by_field": by_field}

    def error_examples(self, field: str, limit: int = 10) -> list[dict]:
        """The most common error messages for a field, each with a few row numbers."""
        cur = self.con.execute(
            "SELECT kind, message, count(*) AS n, list(_row ORDER BY _row)[1:5] FROM errors "
            "WHERE field = ? GROUP BY 1, 2 ORDER BY n DESC, message LIMIT ?",
            [field, limit],
        )
        return [
            {"kind": k, "message": m, "rows": n, "example_rows": list(ex)}
            for k, m, n, ex in cur.fetchall()
        ]
