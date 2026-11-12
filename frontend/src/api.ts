// Types and calls for the DDT backend (backend/ddt/api.py).

export type TableName = "input" | "output";

export interface Filter {
  column: string;
  op: "contains" | "in";
  value?: string;
  values?: (string | null)[];
}

export interface ColumnProfile {
  name: string;
  filled: number;
  distinct: number;
  type: string;
  expression?: string | null;
}

export interface Summary {
  rows: number;
  records: number;
  errors: number;
  rows_with_errors: number;
  by_field: Record<string, Record<string, number>>;
  unused_columns: string[];
  unmapped_fields: { path: string; required: boolean }[];
  output_sha256: string;
  can_undo: boolean;
}

export interface ProjectState {
  source: {
    name: string;
    sha256: string;
    encoding: string;
    encoding_detected: boolean;
    fallback_bytes: number;
    header_renames: { index: number; original: string; name: string }[];
  };
  schema_name: string;
  schema: unknown;
  mapping: string;
  fields: Record<string, string>;
  input_columns: ColumnProfile[];
  output_columns: ColumnProfile[];
  summary: Summary;
}

export interface Row {
  _row: number;
  _errors: Record<string, string[]>;
  [column: string]: unknown;
}

export interface RowsQuery {
  table: TableName;
  offset: number;
  limit: number;
  sort?: string | null;
  descending?: boolean;
  filters: Filter[];
  errors_only: boolean;
  error_field: string | null;
}

export interface Facet {
  column: string;
  distinct: number;
  values: { value: string | null; count: number }[];
}

export interface RowDetail {
  row: number;
  input: Record<string, string>;
  output: Record<string, unknown>;
  errors: { field: string; kind: string; message: string }[];
}

export interface LogEntry {
  time: string;
  author: string;
  action: string;
  path?: string;
  expression?: string;
  accepted: boolean;
  reason?: string;
  effect?: Effect;
}

export interface Effect {
  records_changed: number;
  errors_before: number;
  errors_after: number;
  rows_with_errors_before: number;
  rows_with_errors_after: number;
}

export type ChatEvent =
  | { type: "assistant"; text: string; model: string }
  | { type: "tool_call"; name: string; arguments: string }
  | {
      type: "tool_result";
      name: string;
      ok: boolean;
      changed_mapping: boolean;
      result: { result?: Record<string, unknown>; error?: string };
    }
  | { type: "stopped" | "error"; text: string };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown, method = "POST"): Promise<T> {
  return call<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  project: () => call<ProjectState>("/api/project"),
  rows: (query: RowsQuery) => post<{ total: number; rows: Row[] }>("/api/rows", query),
  facet: (query: Omit<RowsQuery, "offset" | "limit" | "sort" | "descending"> & { column: string }) =>
    post<Facet>("/api/facet", query),
  row: (n: number) => call<RowDetail>(`/api/row/${n}`),
  putMapping: (yaml: string) =>
    post<{ effect: Effect; project: ProjectState }>("/api/mapping", { yaml }, "PUT"),
  undo: () => post<{ project: ProjectState }>("/api/undo"),
  save: () => post<{ saved: string }>("/api/save"),
  log: () => call<LogEntry[]>("/api/log"),
  models: () => call<{ models: { name: string; model: string }[]; source: string }>("/api/models"),
  resetChat: () => post<{ ok: boolean }>("/api/chat/reset"),

  /** Send one chat message; `onEvent` is called for each step as the model works. */
  async chat(model: string, message: string, onEvent: (event: ChatEvent) => void): Promise<void> {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, message }),
    });
    if (!response.ok || !response.body) {
      const detail = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(detail.detail);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) if (line.trim()) onEvent(JSON.parse(line) as ChatEvent);
      if (done) break;
    }
  },
};
