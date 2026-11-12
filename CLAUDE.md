# DDT

Deterministic Data Transformer: an LLM edits a mapping file, a deterministic engine applies it to
CSV input, and output is validated against a JSON Schema. See `README.md` for the format and scope.

## Commands

- `uv run pytest` — backend tests (no model or network needed)
- `uv run ruff check . && uv run ruff format .`
- `cd frontend && npm run typecheck && npm run build` — the server serves `frontend/dist`
- `uv run ddt serve examples/henderson/henderson.ddt.yaml` — UI on port 8787 (8765 is used by
  another tool on the main development machine)
- Full example data lives in the gitignored `data/` directory; README has the download commands.
  Tests use the excerpts in `tests/fixtures/`.

## Checking the UI

There are no frontend tests. To check a UI change, build the frontend, start `ddt serve`, and
drive the page with headless Playwright (Chromium). Wait for `/api/project` to answer before
loading the page: the Cushman example takes about 11 seconds to start. Grid columns are
virtualised, so a header that is scrolled out of view is not in the DOM.

## Invariants (do not weaken without asking)

- The only way to change a mapping is `Project.set_mapping` (`backend/ddt/project.py`). The UI,
  the CLI and the model's tools all go through it. A rejected edit must change nothing.
- The model's tools are the list in `backend/ddt/harness/tools.py`. Adding a tool that writes
  anything other than a mapping field breaks the point of the project.
- One input row gives exactly one output record. Input values are verbatim strings; the importer
  must not clean, trim or convert cell values.
- No source of non-determinism in the engine: no clocks, randomness or network. Timestamps belong
  only in the edit log.
- The mapping's `source` block is written by the importer and is not editable.

## Things that are easy to get wrong

- Fixture CSVs are raw-byte excerpts in their original encodings (Mac Roman with bare CR;
  UTF-8 with stray Latin-1 bytes). Do not re-save them with an editor. Cut new excerpts as bytes.
- Do not put literal U+FFFD or BOM characters in source files; write `�` / `﻿`.
- jsonata-python returns `None` both for "no value" and for JSON null; both mean "omit the field".
- JSONata collapses a one-item result to the item. Array-valued expressions end with `[]`.
  Wrapping in `[ ... ]` nests the array when there are several results.
- jsonschema reports `required` once per missing property; `validate.py` de-duplicates.
- Encoding detection is a hand-written rule in `importer.py`. charset-normalizer was tried and
  guessed wrong for both example files.
- AG Grid: use `valueGetter`, not `field` (output field names contain dots). Set Filter and the
  server-side row model are Enterprise-only; filtering is done in DuckDB via the facet panel.
- The design notes this project grew from are in a private repository. Do not copy text from them
  into this public one.
