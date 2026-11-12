"""Command line: `ddt run`, `ddt new`, `ddt serve`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .mapping import Mapping, MappingError
from .project import Project


def _report(project: Project) -> str:
    s = project.summary()
    lines = [
        f"{s['rows']} input rows -> {s['records']} records",
        f"{s['errors']} errors in {s['rows_with_errors']} rows",
    ]
    for fld, kinds in s["by_field"].items():
        detail = ", ".join(f"{n} rows {kind}" for kind, n in kinds.items())
        lines.append(f"  {fld or '(record)'}: {detail}")
        for ex in project.store.error_examples(fld, limit=3):
            where = f"e.g. row {ex['example_rows'][0]}"
            lines.append(f"      {ex['rows']}x {ex['message'][:100]} ({where})")
    if s["unused_columns"]:
        unused = ", ".join(s["unused_columns"])
        lines.append(f"input columns with data that no field reads: {unused}")
    missing = [f["path"] for f in s["unmapped_fields"] if f["required"]]
    if missing:
        lines.append(f"required schema fields with no mapping: {', '.join(missing)}")
    lines.append(f"output sha256: {s['output_sha256']}")
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    project = Project.open(args.mapping, source=args.source)
    if args.output:
        Path(args.output).write_text(project.output_jsonl(), encoding="utf-8")
    print(_report(project), file=sys.stderr if args.output == "-" else sys.stdout)
    if args.output == "-":
        sys.stdout.write(project.output_jsonl())
    return 1 if project.result.errors and args.strict else 0


def cmd_new(args: argparse.Namespace) -> int:
    out = Path(args.output)
    if out.exists():
        print(f"{out} already exists", file=sys.stderr)
        return 2
    csv_path, schema_path = Path(args.csv), Path(args.schema)
    base = out.parent.resolve()

    def rel(p: Path) -> str:
        return Path(*_relative(p.resolve(), base)).as_posix()

    project = Project(
        raw=csv_path.read_bytes(),
        source_name=csv_path.name,
        schema=json.loads(schema_path.read_text(encoding="utf-8")),
        schema_name=rel(schema_path),
        mapping=Mapping(schema=rel(schema_path), source={"file": rel(csv_path)}),
        encoding=args.encoding,
    )
    out.write_text(project.mapping.to_yaml(), encoding="utf-8")
    info = project.imported.info
    print(f"wrote {out}: {len(project.imported.rows)} rows, {len(project.columns)} columns, "
          f"encoding {info.encoding}, {len(info.header_renames)} headers renamed")  # fmt: skip
    return 0


def _relative(path: Path, base: Path) -> tuple[str, ...]:
    import os

    return Path(os.path.relpath(path, base)).parts


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    project = Project.open(args.mapping, source=args.source)
    app = create_app(project, config_path=Path(args.config) if args.config else None)
    print(f"DDT: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ddt", description="Deterministic Data Transformer")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="apply a mapping and report validation errors")
    run.add_argument("mapping")
    run.add_argument("--source", help="use this CSV instead of the one the mapping names")
    run.add_argument("-o", "--output", help="write records as JSONL here ('-' for stdout)")
    run.add_argument("--strict", action="store_true", help="exit 1 if any record has errors")
    run.set_defaults(func=cmd_run)

    new = sub.add_parser("new", help="start an empty mapping for a CSV and a JSON Schema")
    new.add_argument("csv")
    new.add_argument("schema")
    new.add_argument("-o", "--output", required=True, help="mapping file to create (*.ddt.yaml)")
    new.add_argument("--encoding", help="text encoding, if detection gets it wrong")
    new.set_defaults(func=cmd_new)

    serve = sub.add_parser("serve", help="open a mapping in the web UI")
    serve.add_argument("mapping")
    serve.add_argument("--source", help="use this CSV instead of the one the mapping names")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--config", help="model list (default: ./ddt.toml)")
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (MappingError, FileNotFoundError, ValueError) as exc:
        print(f"ddt: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
