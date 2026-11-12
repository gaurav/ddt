import { useEffect, useRef, useState } from "react";
import { type ChatEvent, api } from "./api";

type Item = { kind: "user"; text: string } | { kind: "event"; event: ChatEvent };

function describeResult(event: Extract<ChatEvent, { type: "tool_result" }>): string {
  if (!event.ok) return event.result.error ?? "failed";
  const r = event.result.result ?? {};
  if (event.changed_mapping)
    return `accepted: ${r.records_changed} records changed, errors ${r.errors_before} → ${r.errors_after}`;
  return "ok";
}

function EventView({ event }: { event: ChatEvent }) {
  switch (event.type) {
    case "assistant":
      return (
        <div className="msg assistant">
          <div className="who">{event.model}</div>
          {event.text}
        </div>
      );
    case "tool_call":
      return (
        <div className="msg tool">
          <code>
            {event.name}({event.arguments && event.arguments !== "{}" ? event.arguments : ""})
          </code>
        </div>
      );
    case "tool_result":
      return (
        <details className={`msg tool result ${event.ok ? "" : "failed"}`}>
          <summary>
            {event.ok ? "↳" : "✕"} {describeResult(event)}
          </summary>
          <pre>{JSON.stringify(event.result.result ?? event.result, null, 2)}</pre>
        </details>
      );
    default:
      return <div className="msg error">{event.text}</div>;
  }
}

export function ChatPane({ onMappingChanged }: { onMappingChanged: () => void }) {
  const [models, setModels] = useState<{ name: string; model: string }[]>([]);
  const [source, setSource] = useState("");
  const [model, setModel] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef<HTMLDivElement>(null);

  const loadModels = () =>
    api.models().then((m) => {
      setModels(m.models);
      setSource(m.source);
      setModel((current) => (m.models.some((x) => x.name === current) ? current : (m.models[0]?.name ?? "")));
    });
  useEffect(() => {
    loadModels().catch(() => setModels([]));
  }, []);
  useEffect(() => end.current?.scrollIntoView({ block: "end" }), [items]);

  const send = async () => {
    const message = draft.trim();
    if (!message || !model || busy) return;
    setDraft("");
    setBusy(true);
    setItems((prev) => [...prev, { kind: "user", text: message }]);
    try {
      await api.chat(model, message, (event) => {
        setItems((prev) => [...prev, { kind: "event", event }]);
        if (event.type === "tool_result" && event.changed_mapping) onMappingChanged();
      });
    } catch (e) {
      setItems((prev) => [...prev, { kind: "event", event: { type: "error", text: (e as Error).message } }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pane chat">
      <div className="chat-bar">
        <select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">
          {models.length === 0 && <option value="">No models found</option>}
          {models.map((m) => (
            <option key={m.name}>{m.name}</option>
          ))}
        </select>
        <button onClick={() => loadModels().catch(() => undefined)} title={`Models from ${source}`}>
          Refresh
        </button>
        <button
          disabled={busy || items.length === 0}
          onClick={() => api.resetChat().then(() => setItems([]))}
          title="Forget this conversation. The mapping is kept."
        >
          New chat
        </button>
      </div>
      <div className="messages">
        {models.length === 0 && (
          <p className="muted">
            No models found ({source || "no model source"}). Start Ollama and pull a model that supports tool
            calling, or list endpoints in <code>ddt.toml</code>, then press Refresh.
          </p>
        )}
        {items.length === 0 && models.length > 0 && (
          <p className="muted">
            The model can read the input columns, the schema and the validation errors. The only thing it can change
            is the mapping; every edit it tries appears in the Log tab and can be undone.
          </p>
        )}
        {items.map((item, i) =>
          item.kind === "user" ? (
            <div key={i} className="msg user">
              {item.text}
            </div>
          ) : (
            <EventView key={i} event={item.event} />
          ),
        )}
        {busy && <div className="msg muted">Working…</div>}
        <div ref={end} />
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <textarea
          rows={3}
          placeholder="Ask about the data, or ask for a mapping change…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="primary" disabled={busy || !model || !draft.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
