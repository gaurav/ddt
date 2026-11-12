"""One open dataset: input rows, target schema, the mapping and its history.

Everything that changes the mapping goes through `Project.set_mapping` (or the `set_field` /
`remove_field` wrappers). A change is checked first and applied only if it is acceptable, so a
rejected edit leaves the project exactly as it was. The UI, the CLI and the LLM harness all use
this one path.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .engine import JsonataEngine, RunResult, to_jsonl
from .importer import ImportResult, import_csv
from .mapping import Mapping, MappingError, check_mapping, schema_for_path, schema_paths
from .store import Store
from .validate import make_validator, validate_records

MAX_HISTORY = 50


class Project:
    def __init__(
        self,
        raw: bytes,
        source_name: str,
        schema: dict,
        schema_name: str,
        mapping: Mapping | None = None,
        encoding: str | None = None,
        check_hash: bool = True,
        mapping_path: Path | None = None,
    ):
        mapping = mapping.copy() if mapping else Mapping(schema=schema_name)
        pinned = mapping.source.get("sha256")
        self.imported: ImportResult = import_csv(raw, encoding or mapping.source.get("encoding"))
        info = self.imported.info
        if check_hash and pinned and pinned != info.sha256:
            raise MappingError(
                f"{source_name} is not the file this mapping was written for "
                f"(sha256 {info.sha256[:12]}…, mapping expects {pinned[:12]}…)"
            )
        if check_hash or not pinned:
            # Record what the importer decided, so the next run does not have to detect it again.
            mapping.source = {
                "file": mapping.source.get("file", source_name),
                "sha256": info.sha256,
                "encoding": info.encoding,
                **({"header_renames": info.header_renames} if info.header_renames else {}),
            }
        self.source_name = source_name
        self.schema = schema
        self.schema_name = schema_name
        self.mapping_path = mapping_path
        self.validator = make_validator(schema)
        self.columns = self.imported.columns
        self.store = Store(self.columns, self.imported.rows)
        self.input_profile = self.store.profile("input")  # the input never changes
        self.engine = JsonataEngine(self.columns, self.imported.rows)
        self.history: list[Mapping] = []
        self.log: list[dict] = []
        self.mapping = mapping
        self.result = RunResult(records=[], errors=[])
        self._apply(mapping)

    @classmethod
    def open(
        cls, mapping_path: Path, source: Path | None = None, check_hash: bool | None = None
    ) -> Project:
        """Open a mapping file. `source` substitutes another CSV for the one the mapping names
        (a test excerpt, say); the pinned hash is then not enforced unless asked for."""
        mapping_path = Path(mapping_path)
        mapping = Mapping.from_yaml(mapping_path.read_text(encoding="utf-8"))
        base = mapping_path.parent
        schema_file = base / mapping.schema
        src = Path(source) if source else base / mapping.source.get("file", "")
        if not src.is_file():
            raise MappingError(f"source file not found: {src}")
        return cls(
            raw=src.read_bytes(),
            source_name=src.name,
            schema=json.loads(schema_file.read_text(encoding="utf-8")),
            schema_name=mapping.schema,
            mapping=mapping,
            check_hash=(source is None) if check_hash is None else check_hash,
            mapping_path=mapping_path,
        )

    # --- running -----------------------------------------------------------------------------

    def _apply(self, mapping: Mapping) -> None:
        """Run `mapping` and make it current. Raises MappingError without changing anything."""
        compiled = check_mapping(mapping, self.schema)
        result = self.engine.run(compiled, self.schema)
        result.errors.extend(validate_records(self.validator, result.records))
        self.mapping, self.result, self._compiled = mapping, result, compiled
        self._output_sha256: str | None = None
        self.store.set_output(list(mapping.fields), result.records, result.errors)
        in_use = {e for m in [*self.history, mapping] for e in m.fields.values()}
        self.engine.forget_unused(in_use)

    def output_jsonl(self) -> str:
        return to_jsonl(self.result.records)

    def output_sha256(self) -> str:
        if self._output_sha256 is None:
            digest = hashlib.sha256(self.output_jsonl().encode("utf-8"))
            self._output_sha256 = digest.hexdigest()
        return self._output_sha256

    # --- editing -----------------------------------------------------------------------------

    def set_mapping(self, mapping: Mapping, author: str, action: str, detail: dict) -> dict:
        """Replace the mapping. Returns the effect; on MappingError nothing has changed."""
        before_records = self.result.records
        before = self.store.error_counts()
        previous = self.mapping
        # The source block describes the input file, which no edit is allowed to change.
        mapping.source, mapping.schema = dict(previous.source), previous.schema
        try:
            self._apply(mapping)
        except MappingError as exc:
            self._record(author, action, detail, accepted=False, reason=str(exc))
            raise
        self.history.append(previous)
        del self.history[:-MAX_HISTORY]
        after = self.store.error_counts()
        effect = {
            "records_changed": sum(
                1 for a, b in zip(before_records, self.result.records, strict=True) if a != b
            ),
            "errors_before": before["errors"],
            "errors_after": after["errors"],
            "rows_with_errors_before": before["rows_with_errors"],
            "rows_with_errors_after": after["rows_with_errors"],
        }
        self._record(author, action, detail, accepted=True, effect=effect)
        return effect

    def set_field(self, path: str, expression: str, author: str) -> dict:
        mapping = self.mapping.copy()
        mapping.fields[path] = expression
        return self.set_mapping(
            mapping, author, "set_field", {"path": path, "expression": expression}
        )

    def remove_field(self, path: str, author: str) -> dict:
        if path not in self.mapping.fields:
            exc = MappingError(f"the mapping has no field {path!r}")
            self._record(author, "remove_field", {"path": path}, accepted=False, reason=str(exc))
            raise exc
        mapping = self.mapping.copy()
        del mapping.fields[path]
        return self.set_mapping(mapping, author, "remove_field", {"path": path})

    def undo(self, author: str = "user") -> bool:
        if not self.history:
            return False
        previous = self.history.pop()
        self._apply(previous)
        self._record(author, "undo", {}, accepted=True)
        return True

    def save(self) -> Path | None:
        if self.mapping_path:
            self.mapping_path.write_text(self.mapping.to_yaml(), encoding="utf-8")
        return self.mapping_path

    def _record(self, author: str, action: str, detail: dict, accepted: bool, **extra) -> None:
        self.log.append(
            {
                "time": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "author": author,
                "action": action,
                **detail,
                "accepted": accepted,
                **extra,
            }
        )

    # --- reporting ---------------------------------------------------------------------------

    def unused_columns(self) -> list[str]:
        """Input columns that hold data but that no expression reads: data left behind."""
        read = {name for expr in self._compiled.values() for name in expr.names}
        filled = {p["name"] for p in self.input_profile if p["filled"]}
        return [c for c in self.columns if c in filled and c not in read]

    def unmapped_fields(self) -> list[dict]:
        required = set()

        def collect(node_path: str) -> None:
            node = schema_for_path(self.schema, node_path) if node_path else self.schema
            for name in (node or {}).get("required", []):
                child = f"{node_path}.{name}" if node_path else name
                required.add(child)
                collect(child)

        collect("")
        return [
            {"path": p, "required": p in required}
            for p in schema_paths(self.schema)
            if p not in self.mapping.fields
        ]

    def summary(self) -> dict:
        return {
            "rows": len(self.imported.rows),
            "records": len(self.result.records),
            **self.store.error_counts(),
            "unused_columns": self.unused_columns(),
            "unmapped_fields": self.unmapped_fields(),
            "output_sha256": self.output_sha256(),
            "can_undo": bool(self.history),
        }

    def output_profile(self) -> list[dict]:
        profile = self.store.profile("output")
        for p in profile:
            sub = schema_for_path(self.schema, p["name"]) or {}
            declared = sub.get("type")
            if isinstance(declared, list):
                declared = "/".join(declared)
            p["type"] = declared or ("enum" if "enum" in sub else "any")
            if sub.get("format"):
                p["type"] += f" ({sub['format']})"
            p["expression"] = self.mapping.fields.get(p["name"])
        return profile

    def describe(self) -> dict:
        return {
            "source": {"name": self.source_name, **asdict(self.imported.info)},
            "schema_name": self.schema_name,
            "schema": self.schema,
            "mapping": self.mapping.to_yaml(),
            "fields": self.mapping.fields,
            "input_columns": self.input_profile,
            "output_columns": self.output_profile(),
            "summary": self.summary(),
        }
