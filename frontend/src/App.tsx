import { useCallback, useEffect, useState } from "react";
import { ChatPane } from "./ChatPane";
import { DataGrid } from "./DataGrid";
import { Facets } from "./Facets";
import { LogPane, MappingPane } from "./MappingPane";
import { type Filter, type ProjectState, type RowDetail, type TableName, api } from "./api";

type SideTab = "mapping" | "chat" | "log";
type PerTable<T> = Record<TableName, T>;

function RowPanel({ row, version, onClose }: { row: number; version: string; onClose: () => void }) {
  const [detail, setDetail] = useState<RowDetail | null>(null);
  useEffect(() => {
    api.row(row).then(setDetail).catch(() => setDetail(null));
  }, [row, version]);
  if (!detail) return null;
  return (
    <section className="row-panel">
      <header>
        <strong>Row {detail.row}</strong>
        {detail.errors.map((e, i) => (
          <span key={i} className="error">
            {e.field || "(record)"}: {e.message}
          </span>
        ))}
        <button className="link" onClick={onClose} aria-label="Close row detail">
          ×
        </button>
      </header>
      <div className="row-sides">
        <div>
          <h3>Input (as read from the file)</h3>
          <dl>
            {Object.entries(detail.input)
              .filter(([, v]) => v !== "")
              .map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
          </dl>
        </div>
        <div>
          <h3>Output record</h3>
          <pre>{JSON.stringify(detail.output, null, 2)}</pre>
        </div>
      </div>
    </section>
  );
}

export function App() {
  const [project, setProject] = useState<ProjectState | null>(null);
  const [failure, setFailure] = useState("");
  const [note, setNote] = useState("");
  const [table, setTable] = useState<TableName>("output");
  const [filters, setFilters] = useState<PerTable<Filter[]>>({ input: [], output: [] });
  const [facets, setFacets] = useState<PerTable<string[]>>({ input: [], output: [] });
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [errorField, setErrorField] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [total, setTotal] = useState(0);
  const [tab, setTab] = useState<SideTab>("mapping");
  // Bumped on every mapping change (the output hash alone misses edits that change nothing).
  const [edits, setEdits] = useState(0);

  const refresh = useCallback(() => {
    api
      .project()
      .then((p) => {
        setProject(p);
        setEdits((n) => n + 1);
      })
      .catch((e: Error) => setFailure(e.message));
  }, []);
  useEffect(refresh, [refresh]);

  // Once a field has no errors left, a filter on its errors would only show an empty grid.
  useEffect(() => {
    if (project && errorField !== null && !(errorField in project.summary.by_field)) setErrorField(null);
  }, [project, errorField]);

  const openFacet = useCallback(
    (column: string) => setFacets((f) => ({ ...f, [table]: [...f[table].filter((c) => c !== column), column] })),
    [table],
  );
  const adopt = (next: ProjectState, message: string) => {
    setProject(next);
    setEdits((n) => n + 1);
    setNote(message);
  };

  if (failure) return <p className="error fatal">Could not reach the DDT server: {failure}</p>;
  if (!project) return <p className="fatal muted">Loading…</p>;

  const { summary, source } = project;
  const version = `${summary.output_sha256}/${edits}`;
  const columns = table === "input" ? project.input_columns : project.output_columns;
  const filtered = filters[table].length > 0 || errorsOnly || errorField !== null;

  return (
    <div className="app">
      <header className="top">
        <h1>DDT</h1>
        <div className="source" title={`sha256 ${source.sha256}`}>
          <strong>{source.name}</strong>
          <span className="muted">
            {" "}
            read as {source.encoding}
            {source.fallback_bytes > 0 && ` (${source.fallback_bytes} bytes via fallback)`}
            {source.header_renames.length > 0 && `, ${source.header_renames.length} headers renamed`} →{" "}
          </span>
          <strong>{project.schema_name}</strong>
        </div>
        <div className="stats">
          <span>{summary.rows.toLocaleString()} rows</span>
          <span>{summary.records.toLocaleString()} records</span>
          <span className={summary.rows_with_errors ? "error" : "ok"}>
            {summary.rows_with_errors.toLocaleString()} rows with errors
          </span>
          <span title={`Output sha256 ${summary.output_sha256}`} className="muted">
            output {summary.output_sha256.slice(0, 8)}
          </span>
        </div>
        <div className="actions">
          <button disabled={!summary.can_undo} onClick={() => api.undo().then((r) => adopt(r.project, "Undone"))}>
            Undo
          </button>
          <button
            onClick={() =>
              api
                .save()
                .then((r) => setNote(`Saved ${r.saved}`))
                .catch((e: Error) => setNote(e.message))
            }
          >
            Save mapping
          </button>
          <a className="button" href="/api/output.jsonl" download="output.jsonl">
            Download JSONL
          </a>
        </div>
      </header>

      <main>
        <Facets
          table={table}
          columns={columns.map((c) => c.name)}
          open={facets[table]}
          onOpen={(open) => setFacets((f) => ({ ...f, [table]: open }))}
          filters={filters[table]}
          errorsOnly={errorsOnly}
          errorField={errorField}
          version={version}
          onChange={(next) => setFilters((f) => ({ ...f, [table]: next }))}
        />

        <section className="center">
          <div className="toolbar">
            <div className="tabs" role="tablist">
              {(["input", "output"] as const).map((t) => (
                <button key={t} role="tab" aria-selected={table === t} onClick={() => setTable(t)}>
                  {t === "input" ? "Input" : "Output"}
                </button>
              ))}
            </div>
            <label>
              <input
                type="checkbox"
                checked={errorsOnly || errorField !== null}
                onChange={(e) => {
                  setErrorsOnly(e.target.checked);
                  setErrorField(null);
                }}
              />{" "}
              Only rows with errors{errorField !== null && ` in ${errorField || "(record)"}`}
            </label>
            <span className="muted">
              {filtered ? `${total.toLocaleString()} of ${summary.rows.toLocaleString()} rows` : ""}
            </span>
            {filtered && (
              <button
                className="link"
                onClick={() => {
                  setFilters((f) => ({ ...f, [table]: [] }));
                  setErrorsOnly(false);
                  setErrorField(null);
                }}
              >
                Clear filters
              </button>
            )}
            {note && <span className="note">{note}</span>}
          </div>
          <div className="grid">
            {columns.length === 0 ? (
              <p className="muted fatal">The mapping has no fields yet, so there are no output columns.</p>
            ) : (
              <DataGrid
                key={table}
                table={table}
                columns={columns}
                rows={summary.rows}
                filters={filters[table]}
                errorsOnly={errorsOnly}
                errorField={errorField}
                version={version}
                focusColumn={errorField}
                onFacet={openFacet}
                onSelectRow={setSelected}
                onTotal={setTotal}
              />
            )}
          </div>
          {selected !== null && <RowPanel row={selected} version={version} onClose={() => setSelected(null)} />}
        </section>

        <aside className="side">
          <div className="tabs" role="tablist">
            {(["mapping", "chat", "log"] as const).map((t) => (
              <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
                {t[0].toUpperCase() + t.slice(1)}
              </button>
            ))}
          </div>
          {/* All three stay mounted so a chat in progress and unsaved YAML survive tab switches. */}
          <div hidden={tab !== "mapping"} className="tab-body">
            <MappingPane
              project={project}
              onProject={adopt}
              onShowErrors={(field) => {
                setErrorField(field);
                setTable("output");
              }}
            />
          </div>
          <div hidden={tab !== "chat"} className="tab-body">
            <ChatPane onMappingChanged={refresh} />
          </div>
          <div hidden={tab !== "log"} className="tab-body">
            <LogPane version={version} />
          </div>
        </aside>
      </main>
    </div>
  );
}
