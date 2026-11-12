"""The harness with a scripted stand-in for the model: no network, no Ollama."""

import json

import pytest
from fastapi.testclient import TestClient

from ddt.api import create_app
from ddt.harness.agent import Conversation
from ddt.harness.models import ModelConfig
from ddt.harness.tools import TOOLS, WRITE_TOOLS, call_tool
from ddt.project import Project

from .conftest import EXAMPLES, FIXTURES

HENDERSON = EXAMPLES / "henderson" / "henderson.ddt.yaml"
EXCERPT = FIXTURES / "henderson_excerpt.csv"


@pytest.fixture
def project() -> Project:
    return Project.open(HENDERSON, source=EXCERPT)


def call(name: str, **arguments) -> dict:
    return {"id": f"call_{name}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}  # fmt: skip


class ScriptedModel:
    """Plays back a fixed list of assistant messages, recording what it was sent."""

    def __init__(self, script: list[dict]):
        self.script, self.seen = list(script), []

    def complete(self, model, messages, tools):
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0)


def test_the_only_write_tools_are_the_two_mapping_edits():
    names = {t["function"]["name"] for t in TOOLS}
    assert WRITE_TOOLS == {"set_field", "remove_field"}
    assert not any(w in n for n in names - WRITE_TOOLS for w in ("set", "write", "delete", "run"))


def test_read_tools_do_not_change_the_project(project):
    before, log = project.output_sha256(), list(project.log)
    for name, args in [
        ("get_schema", {}),
        ("get_mapping", {}),
        ("list_columns", {}),
        ("profile_column", {"column": "stateProvince"}),
        ("sample_rows", {"columns": ["ScrapedName"], "where_column": "Genus", "contains": "f"}),
        ("validation_summary", {}),
        ("errors_for_field", {"field": "scientificName"}),
        ("preview_expression", {"expression": "$uppercase(Genus)"}),
    ]:
        out = call_tool(project, name, args, author="m")
        assert "result" in out, (name, out)
    assert project.output_sha256() == before and project.log == log and project.history == []
    assert "$uppercase(Genus)" not in project.engine._cache


def test_tool_problems_come_back_as_errors_not_exceptions(project):
    before = project.mapping.to_yaml()
    for name, args in [
        ("set_field", {"path": "genus", "expression": "$random()"}),
        ("set_field", {"path": "nope", "expression": "Genus"}),
        ("set_field", {"path": "genus"}),
        ("remove_field", {"path": "nope"}),
        ("profile_column", {"column": "nope"}),
        ("preview_expression", {"expression": "$trim("}),
        ("read_file", {"path": "/etc/passwd"}),
        ("set_field", "{not json"),
    ]:
        assert "error" in call_tool(project, name, args, author="m"), name
    assert project.mapping.to_yaml() == before
    rejected = [e for e in project.log if not e["accepted"]]
    assert len(rejected) == 4 and {e["author"] for e in rejected} == {"m"}


def test_a_turn_runs_tools_and_logs_edits_under_the_model_name(project):
    model = ScriptedModel(
        [
            {"content": "", "tool_calls": [call("errors_for_field", field="scientificName")]},
            {"content": "", "tool_calls": [call("set_field", path="genus", expression="$now()")]},
            {
                "content": "Trying the scraped name.",
                "tool_calls": [
                    call("set_field", path="scientificName", expression="$trim(ScrapedName)")
                ],
            },
            {"content": "Mapped scientificName from ScrapedName; no errors remain."},
        ]
    )
    events = []
    Conversation(project).turn(model, "test-model", "fix scientificName", events.append)

    assert project.mapping.fields["scientificName"] == "$trim(ScrapedName)"
    assert project.summary()["errors"] == 0
    assert [e["type"] for e in events] == [
        "tool_call", "tool_result", "tool_call", "tool_result",
        "assistant", "tool_call", "tool_result", "assistant",
    ]  # fmt: skip
    assert [e["ok"] for e in events if e["type"] == "tool_result"] == [True, False, True]
    assert [(e["author"], e["action"], e["accepted"]) for e in project.log] == [
        ("test-model", "set_field", False),
        ("test-model", "set_field", True),
    ]
    # The model was told the rejected edit changed nothing.
    tool_messages = [m for m in model.seen[-1] if m["role"] == "tool"]
    assert "nothing was changed" in tool_messages[1]["content"]
    assert project.undo() and project.summary()["errors"] > 0


def test_a_model_that_never_stops_is_cut_off(project):
    loop = {"content": "", "tool_calls": [call("validation_summary")]}
    events = []
    Conversation(project).turn(ScriptedModel([loop] * 50), "m", "go", events.append)
    assert events[-1]["type"] == "stopped"


def test_api_end_to_end(project, tmp_path):
    config = tmp_path / "ddt.toml"
    config.write_text('[[models]]\nname = "fake"\nmodel = "fake-1"\n')
    script = [
        {"content": "", "tool_calls": [call("set_field", path="scientificName",
                                            expression="ScrapedName")]},
        {"content": "Done."},
    ]  # fmt: skip
    model = ScriptedModel(script)
    app = create_app(project, config_path=config, client_factory=lambda cfg: model)
    http = TestClient(app)

    described = http.get("/api/project").json()
    assert described["summary"]["rows"] == 40
    assert http.get("/api/models").json()["models"] == [{"name": "fake", "model": "fake-1"}]

    page = http.post("/api/rows", json={"table": "output", "errors_only": True}).json()
    assert page["total"] == 22
    assert "scientificName" in page["rows"][0]["_errors"]
    facet = http.post("/api/facet", json={"table": "input", "column": "stateProvince"}).json()
    assert {v["value"] for v in facet["values"]} >= {"Colorado"}
    assert http.post("/api/rows", json={"table": "nope"}).status_code == 400
    assert http.get("/api/row/7").json()["errors"][0]["field"] == "scientificName"

    bad = http.put("/api/mapping", json={"yaml": "ddt: 1\nschema: s\nfields: {genus: '$now()'}"})
    assert bad.status_code == 400 and "changes between runs" in bad.json()["detail"]

    with http.stream("POST", "/api/chat", json={"model": "fake", "message": "go"}) as response:
        events = [json.loads(line) for line in response.iter_lines() if line]
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "assistant"]
    assert events[1]["changed_mapping"] is True
    assert http.get("/api/project").json()["summary"]["errors"] == 0
    assert http.get("/api/log").json()[-1]["author"] == "fake-1"
    assert http.post("/api/undo").json()["project"]["summary"]["errors"] == 22
    assert http.get("/api/output.jsonl").text.count("\n") == 40
    assert http.post("/api/chat", json={"model": "nope", "message": "x"}).status_code == 400


def test_model_config_defaults_to_ollama(tmp_path):
    assert ModelConfig(name="x", model="x").base_url.startswith("http://localhost:11434")
