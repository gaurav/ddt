"""HTTP API for the web UI: one project per server process."""

from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .harness.agent import ChatClient, Conversation, OpenAIChatClient
from .harness.models import ModelConfig, load_models
from .mapping import Mapping, MappingError
from .project import Project

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class RowsQuery(BaseModel):
    table: str = "input"
    offset: int = 0
    limit: int = 100
    sort: str | None = None
    descending: bool = False
    filters: list[dict] = Field(default_factory=list)
    errors_only: bool = False
    error_field: str | None = None


class FacetQuery(BaseModel):
    table: str = "input"
    column: str
    filters: list[dict] = Field(default_factory=list)
    errors_only: bool = False
    error_field: str | None = None


class MappingBody(BaseModel):
    yaml: str


class ChatBody(BaseModel):
    model: str
    message: str


def create_app(
    project: Project,
    config_path: Path | None = None,
    client_factory=None,
) -> FastAPI:
    """`client_factory(ModelConfig) -> ChatClient` is replaceable so tests need no model."""
    app = FastAPI(title="DDT")
    # FastAPI runs these handlers on a thread pool; the project and its DuckDB connection are
    # used by one thread at a time.
    lock = threading.RLock()
    conversation = Conversation(project, lock)
    chat_busy = threading.Lock()

    def make_client(cfg: ModelConfig) -> ChatClient:
        if client_factory:
            return client_factory(cfg)
        return OpenAIChatClient(cfg.base_url, cfg.api_key())

    def bad_request(exc: Exception) -> HTTPException:
        return HTTPException(status_code=400, detail=str(exc))

    @app.get("/api/project")
    def get_project() -> dict:
        with lock:
            return project.describe()

    @app.post("/api/rows")
    def rows(query: RowsQuery) -> dict:
        try:
            with lock:
                return project.store.rows(**query.model_dump())
        except ValueError as exc:
            raise bad_request(exc) from exc

    @app.post("/api/facet")
    def facet(query: FacetQuery) -> dict:
        try:
            with lock:
                return project.store.facet(**query.model_dump())
        except ValueError as exc:
            raise bad_request(exc) from exc

    @app.get("/api/row/{n}")
    def row(n: int) -> dict:
        with lock:
            if not 1 <= n <= len(project.result.records):
                raise HTTPException(status_code=404, detail="no such row")
            return {
                "row": n,
                "input": dict(zip(project.columns, project.imported.rows[n - 1], strict=True)),
                "output": project.result.records[n - 1],
                "errors": [
                    {"field": e.field, "kind": e.kind, "message": e.message}
                    for e in project.result.errors
                    if e.row == n
                ],
            }

    @app.put("/api/mapping")
    def put_mapping(body: MappingBody) -> dict:
        with lock:
            try:
                mapping = Mapping.from_yaml(body.yaml)
                effect = project.set_mapping(mapping, "user", "edit_mapping", {})
            except MappingError as exc:
                raise bad_request(exc) from exc
            return {"effect": effect, "project": project.describe()}

    @app.post("/api/undo")
    def undo() -> dict:
        with lock:
            if not project.undo():
                raise HTTPException(status_code=400, detail="nothing to undo")
            return {"project": project.describe()}

    @app.post("/api/save")
    def save() -> dict:
        with lock:
            path = project.save()
        if path is None:
            raise HTTPException(status_code=400, detail="this project has no mapping file")
        return {"saved": str(path)}

    @app.get("/api/log")
    def log() -> list[dict]:
        with lock:
            return list(project.log)

    @app.get("/api/output.jsonl", response_class=PlainTextResponse)
    def output() -> str:
        with lock:
            return project.output_jsonl()

    @app.get("/api/models")
    def models() -> dict:
        found, origin = load_models(config_path)
        return {"models": [{"name": m.name, "model": m.model} for m in found], "source": origin}

    @app.post("/api/chat/reset")
    def chat_reset() -> dict:
        with lock:
            conversation.reset()
        return {"ok": True}

    @app.post("/api/chat")
    def chat(body: ChatBody) -> StreamingResponse:
        found, _ = load_models(config_path)
        cfg = next((m for m in found if m.name == body.model), None)
        if cfg is None:
            raise HTTPException(status_code=400, detail=f"unknown model {body.model!r}")
        if not chat_busy.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="a chat turn is already running")

        events: queue.Queue[dict | None] = queue.Queue()

        def work() -> None:
            try:
                conversation.turn(make_client(cfg), cfg.model, body.message, events.put)
            except Exception as exc:  # network errors, a model without tool support, ...
                events.put({"type": "error", "text": f"{type(exc).__name__}: {exc}"})
            finally:
                chat_busy.release()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while (event := events.get()) is not None:
                yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="ui")
    return app
