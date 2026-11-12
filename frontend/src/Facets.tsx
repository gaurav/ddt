import { useEffect, useState } from "react";
import { type Facet, type Filter, type TableName, api } from "./api";

interface FacetProps {
  table: TableName;
  column: string;
  filters: Filter[];
  errorsOnly: boolean;
  errorField: string | null;
  version: string;
  onChange: (filters: Filter[]) => void;
  onClose: () => void;
}

/** Value counts for one column. Ticking values filters the grid; the text box filters by substring. */
function FacetBox(props: FacetProps) {
  const { table, column, filters, errorsOnly, errorField, version, onChange, onClose } = props;
  const [facet, setFacet] = useState<Facet | null>(null);
  const [error, setError] = useState("");
  const selected = filters.find((f) => f.column === column && f.op === "in")?.values ?? [];
  const contains = filters.find((f) => f.column === column && f.op === "contains")?.value ?? "";
  const key = JSON.stringify(filters);

  useEffect(() => {
    let current = true;
    api
      .facet({ table, column, filters, errors_only: errorsOnly, error_field: errorField })
      .then((f) => current && (setFacet(f), setError("")))
      .catch((e: Error) => current && setError(e.message));
    return () => {
      current = false;
    };
    // `key` stands in for `filters`, which is a new array on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table, column, key, errorsOnly, errorField, version]);

  const replace = (op: Filter["op"], next: Filter | null) => {
    const others = filters.filter((f) => !(f.column === column && f.op === op));
    onChange(next ? [...others, next] : others);
  };
  const toggle = (value: string | null) => {
    const values = selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value];
    replace("in", values.length ? { column, op: "in", values } : null);
  };

  return (
    <section className="facet">
      <header>
        <strong title={column}>{column}</strong>
        <span className="muted">{facet ? `${facet.distinct.toLocaleString()} values` : ""}</span>
        <button className="link" onClick={onClose} aria-label={`Remove facet ${column}`}>
          ×
        </button>
      </header>
      <input
        type="search"
        placeholder="contains…"
        value={contains}
        onChange={(e) => replace("contains", e.target.value ? { column, op: "contains", value: e.target.value } : null)}
      />
      {error && <p className="error">{error}</p>}
      <ul>
        {facet?.values.map(({ value, count }) => (
          <li key={value ?? "\u0000"}>
            <label>
              <input type="checkbox" checked={selected.includes(value)} onChange={() => toggle(value)} />
              <span className={value === null ? "muted value" : "value"} title={value ?? ""}>
                {value ?? "(empty)"}
              </span>
              <span className="count">{count.toLocaleString()}</span>
            </label>
          </li>
        ))}
      </ul>
      {facet && facet.distinct > facet.values.length && (
        <p className="muted">Showing the {facet.values.length} most common.</p>
      )}
    </section>
  );
}

export interface FacetsProps extends Omit<FacetProps, "column" | "onClose"> {
  columns: string[];
  open: string[];
  onOpen: (columns: string[]) => void;
}

export function Facets({ columns, open, onOpen, ...rest }: FacetsProps) {
  return (
    <aside className="facets">
      <select
        value=""
        aria-label="Add a facet"
        onChange={(e) => e.target.value && onOpen([...open.filter((c) => c !== e.target.value), e.target.value])}
      >
        <option value="">Add facet…</option>
        {columns
          .filter((c) => !open.includes(c))
          .map((c) => (
            <option key={c}>{c}</option>
          ))}
      </select>
      {open.length === 0 && (
        <p className="muted">
          Facets show a column's values with counts and filter the grid. Add one here or with the “facet” button in
          a column header.
        </p>
      )}
      {open.map((column) => (
        <FacetBox
          key={`${rest.table}/${column}`}
          column={column}
          {...rest}
          onClose={() => {
            onOpen(open.filter((c) => c !== column));
            rest.onChange(rest.filters.filter((f) => f.column !== column));
          }}
        />
      ))}
    </aside>
  );
}
