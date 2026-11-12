import { useEffect, useState } from "react";
import { type LogEntry, type ProjectState, api } from "./api";

interface Props {
  project: ProjectState;
  onProject: (project: ProjectState, note: string) => void;
  onShowErrors: (field: string) => void;
}

export function MappingPane({ project, onProject, onShowErrors }: Props) {
  const [text, setText] = useState(project.mapping);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // When the mapping changes underneath (an undo, or the model edited it), show the new one.
  useEffect(() => {
    setText(project.mapping);
    setError("");
  }, [project.mapping]);
  const dirty = text !== project.mapping;
  const { summary } = project;

  const apply = async () => {
    setBusy(true);
    try {
      const { effect, project: next } = await api.putMapping(text);
      onProject(
        next,
        `Applied: ${effect.records_changed} records changed, errors ${effect.errors_before} → ${effect.errors_after}`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pane mapping">
      <table className="fields">
        <thead>
          <tr>
            <th>Output field</th>
            <th>Expression</th>
            <th title="Rows with errors in this field">Errors</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(project.fields).map(([path, expression]) => {
            const kinds = summary.by_field[path];
            const count = kinds ? Math.max(...Object.values(kinds)) : 0;
            return (
              <tr key={path}>
                <td>{path}</td>
                <td>
                  <code>{expression}</code>
                </td>
                <td className="num">
                  {count ? (
                    <button className="link error" onClick={() => onShowErrors(path)}>
                      {count.toLocaleString()}
                    </button>
                  ) : (
                    <span className="muted">0</span>
                  )}
                </td>
              </tr>
            );
          })}
          {Object.entries(summary.by_field)
            .filter(([path]) => !(path in project.fields))
            .map(([path, kinds]) => (
              <tr key={path}>
                <td>{path || "(record)"}</td>
                <td className="muted">not mapped</td>
                <td className="num">
                  <button className="link error" onClick={() => onShowErrors(path)}>
                    {Math.max(...Object.values(kinds)).toLocaleString()}
                  </button>
                </td>
              </tr>
            ))}
        </tbody>
      </table>

      <details open={summary.unused_columns.length > 0}>
        <summary>
          {summary.unused_columns.length} input columns with data that no field reads
        </summary>
        <p className="chips">
          {summary.unused_columns.map((c) => (
            <span key={c}>{c}</span>
          ))}
        </p>
      </details>
      <details>
        <summary>{summary.unmapped_fields.length} schema fields with no mapping</summary>
        <p className="chips">
          {summary.unmapped_fields.map((f) => (
            <span key={f.path} className={f.required ? "required" : ""}>
              {f.path}
              {f.required ? " (required)" : ""}
            </span>
          ))}
        </p>
      </details>

      <label className="editor-label" htmlFor="mapping-yaml">
        Mapping file <span className="muted">(JSONata expression per output field)</span>
      </label>
      <textarea
        id="mapping-yaml"
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === "Enter" && dirty && apply()}
      />
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button className="primary" disabled={!dirty || busy} onClick={apply}>
          Apply
        </button>
        <button disabled={!dirty || busy} onClick={() => setText(project.mapping)}>
          Discard changes
        </button>
        <span className="muted">The source block is fixed; edits to it are ignored.</span>
      </div>
    </div>
  );
}

export function LogPane({ version }: { version: string }) {
  const [log, setLog] = useState<LogEntry[]>([]);
  useEffect(() => {
    api.log().then(setLog).catch(() => setLog([]));
  }, [version]);
  if (!log.length) return <p className="pane muted">No mapping edits yet. Every attempted edit is listed here, including rejected ones.</p>;
  return (
    <ol className="pane log">
      {[...log].reverse().map((entry, i) => (
        <li key={log.length - i} className={entry.accepted ? "" : "rejected"}>
          <div>
            <strong>{entry.author}</strong> {entry.action.replace("_", " ")} {entry.path && <code>{entry.path}</code>}
            <span className="muted"> {new Date(entry.time).toLocaleTimeString()}</span>
          </div>
          {entry.expression && <code className="block">{entry.expression}</code>}
          {entry.accepted
            ? entry.effect && (
                <div className="muted">
                  {entry.effect.records_changed} records changed, errors {entry.effect.errors_before} →{" "}
                  {entry.effect.errors_after}
                </div>
              )
            : <div className="error">Rejected: {entry.reason}</div>}
        </li>
      ))}
    </ol>
  );
}
