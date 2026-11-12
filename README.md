# DDT: Deterministic Data Transformer

A prototype for transforming tabular data to fit a schema with an LLM's help, without having to
trust the LLM with the data.

The LLM never edits data. It edits a **mapping file**, and a deterministic engine applies that
mapping to the untouched input. The same input and the same mapping always give byte-identical
output, so what needs reviewing is a short, readable mapping rather than thousands of rewritten
rows.

There are three pieces:

1. **Data**: a CSV file, read as verbatim strings. Its hash, text encoding and any header renames
   are recorded in the mapping.
2. **A target schema**: a JSON Schema that every output record is validated against.
3. **A mapping** (`*.ddt.yaml`): one [JSONata](https://jsonata.org) expression per output field.

A web UI shows the input and output in a spreadsheet-like grid with facets, filters, column types
and per-cell validation errors, beside the mapping and a chat with the model.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Node.js.

```sh
uv sync
(cd frontend && npm install && npm run build)

# Download the example data into data/ (gitignored)
mkdir -p data/henderson data/cushman
curl -L -o data/henderson/henderson.csv https://zookeys.pensoft.net/article/2909/download/suppl/32/
curl -L -o data/cushman/cushman_metadata.csv \
  https://raw.githubusercontent.com/iulibdcs/cushman_photos/master/cushman_metadata.csv

uv run ddt run examples/henderson/henderson.ddt.yaml -o data/henderson/out.jsonl   # report + JSONL
uv run ddt serve examples/henderson/henderson.ddt.yaml                             # web UI on :8787
```

To start on your own data: `uv run ddt new my.csv my.schema.json -o my.ddt.yaml`, then
`ddt serve my.ddt.yaml`.

### Models

The chat needs a model that supports tool calling, reached through any OpenAI-compatible endpoint.
With no configuration DDT lists the models of a local [Ollama](https://ollama.com) server. To
choose models or use other endpoints, copy `ddt.example.toml` to `ddt.toml`. The model can be
switched between messages, and the Log tab records which model made each edit.

## The mapping format

```yaml
ddt: 1
source:                       # written by DDT; edits to this block are ignored
  file: ../../data/henderson/henderson.csv
  sha256: 89a88e6f...
  encoding: utf-8/cp1252
schema: occurrence.schema.json
fields:
  occurrenceID: catalogNumber
  eventDate: $substringAfter(dateIdentified, "'")
  stateProvince: 'stateProvince = "Colo" ? "Colorado" : stateProvince'
  genre: '$distinct([`Genre 1`, `Genre 2`])[]'
```

- A field key is a path into the output record; dots make nested objects (`location.country`).
  It must be a field the schema declares.
- An expression sees one input row as an object of column name to string. Use backticks for
  names with spaces or punctuation.
- Empty cells are absent from the row. An expression with no result leaves its field out.
- One input row always gives exactly one output record. A failing expression is recorded as an
  error on that row and field; rows are never dropped.
- `$now()`, `$millis()` and `$random()` are rejected: output must not change between runs.
- JSONata returns a one-item result as the item itself. End an expression with `[]` when the
  schema wants an array.
- Output is JSONL, one record per input row in input order, keys in schema order.

## What the model can and cannot do

The model has ten tools (`backend/ddt/harness/tools.py`) and nothing else. Eight only read: the
schema, the mapping, column profiles, sample rows, validation errors, and a preview of an
expression. Two write: `set_field` and `remove_field`. A write is checked and run before it is
accepted; a rejected write changes nothing. Every attempt, accepted or not, is logged with its
author and its effect on the error count, and any accepted edit can be undone.

The model cannot change the input file, the output records, the schema, or the mapping's `source`
block.

What this does and does not show: replaying a mapping proves the output is what the mapping says.
It does not prove the mapping is the right one. DDT's job is to make that judgement easy: the
mapping is short, every output value traces to one expression, validation errors are counted per
field, and input columns that hold data but are read by no field are listed.

## Examples

| | Henderson field notebooks | Cushman photographs |
|---|---|---|
| Source | Thomer et al. 2012, ZooKeys 209, [doi:10.3897/zookeys.209.3247](https://doi.org/10.3897/zookeys.209.3247), Appendix 3 | [iulibdcs/cushman_photos](https://github.com/iulibdcs/cushman_photos) |
| Size | 1,087 rows, 68 columns | 15,190 rows, 90 columns |
| Encoding | UTF-8 with stray Latin-1 bytes, CRLF | Mac Roman, bare CR |
| Target | Darwin Core-style occurrence | schema.org Photograph-style |
| Result | 239 rows lack a resolved scientific name | 227 rows lack a description, a date, or a country |

Both target schemas are hand-written for these examples. `tests/fixtures/` holds short raw-byte
excerpts of both files.

## Not done yet

- Other mapping formats (YARRRML, LinkML-Map). `Engine` in `backend/ddt/engine.py` is where they
  would plug in. YARRRML engines emit RDF, so it also needs an RDF-to-JSON step.
- Inputs other than CSV; rows that should become several records, or several rows one record.
- Validation rules beyond JSON Schema; find-and-replace across fields from the UI.
- Importing a CSV or choosing a schema from the UI (use `ddt new`).
- Mojibake already in a file (text that was mis-decoded before it was saved) is kept as it is.
- The live chat path has only been tested against a scripted stand-in model, not a real one.

## Development

```sh
uv run pytest            # backend tests; no model or network needed
uv run ruff check . && uv run ruff format .
(cd frontend && npm run typecheck)
(cd frontend && npm run dev)   # UI with hot reload; expects `ddt serve` on :8787
```

Licence: GPL-3.0-or-later.
