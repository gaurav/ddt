import {
  AllCommunityModule,
  type ColDef,
  type IDatasource,
  ModuleRegistry,
  colorSchemeDark,
  themeQuartz,
} from "ag-grid-community";
import { AgGridReact, type CustomHeaderProps } from "ag-grid-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type ColumnProfile, type Filter, type Row, type TableName, api } from "./api";

ModuleRegistry.registerModules([AllCommunityModule]);

const PAGE = 200;

function usePrefersDark(): boolean {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(query.matches);
  useEffect(() => {
    const listen = (e: MediaQueryListEvent) => setDark(e.matches);
    query.addEventListener("change", listen);
    return () => query.removeEventListener("change", listen);
  }, [query]);
  return dark;
}

interface HeaderExtras {
  profile: ColumnProfile;
  rows: number;
  onFacet: (column: string) => void;
}

/** Column header: name, sort arrow, the column's type and fill, and a button to facet on it. */
function Header(props: CustomHeaderProps & HeaderExtras) {
  const { profile, rows, onFacet, column, progressSort } = props;
  const [sort, setSort] = useState(column.getSort());
  useEffect(() => {
    const listen = () => setSort(column.getSort());
    column.addEventListener("sortChanged", listen);
    return () => column.removeEventListener("sortChanged", listen);
  }, [column]);
  const empty = rows - profile.filled;
  return (
    <div className="col-header" title={profile.expression ?? undefined}>
      <button className="col-name" onClick={(e) => progressSort(e.shiftKey)}>
        <span>{profile.name}</span>
        {sort && <span className="sort">{sort === "asc" ? "▲" : "▼"}</span>}
      </button>
      <div className="col-meta">
        <span className={`type type-${profile.type.split(/[ /]/)[0]}`}>{profile.type}</span>
        <span>{profile.distinct.toLocaleString()} distinct</span>
        {empty > 0 && <span>{empty.toLocaleString()} empty</span>}
        <button className="facet-button" title="Facet on this column" onClick={() => onFacet(profile.name)}>
          facet
        </button>
      </div>
    </div>
  );
}

export interface DataGridProps {
  table: TableName;
  columns: ColumnProfile[];
  rows: number;
  filters: Filter[];
  errorsOnly: boolean;
  errorField: string | null;
  /** Changes whenever the mapping has been re-run, so cached pages are dropped. */
  version: string;
  /** Scroll this column into view (set when the user asks to see a field's errors). */
  focusColumn: string | null;
  onFacet: (column: string) => void;
  onSelectRow: (row: number) => void;
  onTotal: (total: number) => void;
}

export function DataGrid(props: DataGridProps) {
  const { table, columns, rows, filters, errorsOnly, errorField, version, focusColumn } = props;
  const grid = useRef<AgGridReact<Row>>(null);
  useEffect(() => {
    if (focusColumn && columns.some((c) => c.name === focusColumn)) grid.current?.api?.ensureColumnVisible(focusColumn);
  }, [focusColumn, columns]);
  const { onFacet, onSelectRow, onTotal } = props;
  const dark = usePrefersDark();
  const theme = useMemo(() => (dark ? themeQuartz.withPart(colorSchemeDark) : themeQuartz), [dark]);

  const columnDefs = useMemo<ColDef<Row>[]>(
    () => [
      {
        colId: "_row",
        headerName: "#",
        valueGetter: (p) => p.data?._row,
        width: 70,
        pinned: "left",
        sortable: false,
        cellClass: (p) => (p.data && Object.keys(p.data._errors).length ? "row-number has-error" : "row-number"),
        tooltipValueGetter: (p) =>
          p.data && Object.keys(p.data._errors).length
            ? `Errors in: ${Object.keys(p.data._errors).join(", ")}`
            : undefined,
      },
      ...columns.map<ColDef<Row>>((profile) => ({
        colId: profile.name,
        // A getter, not `field`: field names can contain dots, which AG Grid would treat as paths.
        valueGetter: (p) => p.data?.[profile.name],
        headerComponent: Header,
        headerComponentParams: { profile, rows, onFacet } satisfies HeaderExtras,
        width: 190,
        cellClassRules: {
          "cell-error": (p) => table === "output" && !!p.data?._errors[profile.name],
          "cell-empty": (p) => p.value == null,
        },
        tooltipValueGetter: (p) =>
          (table === "output" && p.data?._errors[profile.name]?.join("\n")) || (p.value as string),
      })),
    ],
    [columns, rows, table, onFacet],
  );

  // A new datasource object makes the grid drop its cache and start again from the top.
  const datasource = useMemo<IDatasource>(
    () => ({
      getRows: (params) => {
        const sort = params.sortModel[0];
        api
          .rows({
            table,
            offset: params.startRow,
            limit: params.endRow - params.startRow,
            sort: sort?.colId ?? null,
            descending: sort?.sort === "desc",
            filters,
            errors_only: errorsOnly,
            error_field: errorField,
          })
          .then((page) => {
            onTotal(page.total);
            params.successCallback(page.rows, page.total);
          })
          .catch(() => params.failCallback());
      },
    }),
    // `version` is not read above but must invalidate the cache when the output changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [table, filters, errorsOnly, errorField, version, onTotal],
  );

  return (
    <AgGridReact<Row>
      ref={grid}
      theme={theme}
      columnDefs={columnDefs}
      rowModelType="infinite"
      datasource={datasource}
      cacheBlockSize={PAGE}
      headerHeight={52}
      rowHeight={30}
      tooltipShowDelay={400}
      defaultColDef={{ resizable: true, sortable: true, suppressMovable: false }}
      getRowId={(p) => String(p.data._row)}
      rowSelection={{ mode: "singleRow", checkboxes: false, enableClickSelection: true }}
      onRowClicked={(e) => e.data && onSelectRow(e.data._row)}
    />
  );
}
