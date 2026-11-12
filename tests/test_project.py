import pytest

from ddt.cli import main
from ddt.mapping import Mapping, MappingError
from ddt.project import Project

from .conftest import EXAMPLES, FIXTURES

HENDERSON = EXAMPLES / "henderson" / "henderson.ddt.yaml"
EXCERPT = FIXTURES / "henderson_excerpt.csv"


@pytest.fixture
def project() -> Project:
    return Project.open(HENDERSON, source=EXCERPT)


def test_example_runs_on_the_excerpt(project):
    s = project.summary()
    assert s["rows"] == s["records"] == 40
    assert set(s["by_field"]) == {"scientificName"}
    assert "ScrapedName" not in s["unused_columns"]  # read by verbatimIdentification
    assert "AnnotatorName" in s["unused_columns"]
    assert "scientificName_1" not in s["unused_columns"]  # empty column: nothing to lose


def test_replay_gives_identical_output(project):
    assert Project.open(HENDERSON, source=EXCERPT).output_sha256() == project.output_sha256()


def test_cushman_example_builds_arrays_of_places():
    cushman = EXAMPLES / "cushman" / "cushman.ddt.yaml"
    project = Project.open(cushman, source=FIXTURES / "cushman_excerpt.csv")
    assert project.summary()["records"] == 42 and project.summary()["errors"] == 0
    first = project.result.records[0]
    assert first["dateCreated"] == "1938-09-03" and first["dateModified"] == "2003-10-16"
    places = [r["contentLocation"] for r in project.result.records if "contentLocation" in r]
    assert places and all(isinstance(p, list) for p in places)  # one place is still a list
    # Keys inside array items follow the schema's order too.
    assert list(places[0][0])[-1] == "addressCountry"
    assert all(isinstance(r["genre"], list) for r in project.result.records if "genre" in r)
    again = Project.open(cushman, source=FIXTURES / "cushman_excerpt.csv")
    assert again.output_sha256() == project.output_sha256()


def test_accepted_edit_reports_its_effect_and_can_be_undone(project):
    before = project.output_sha256()
    effect = project.set_field("scientificName", "$trim(ScrapedName)", author="test")
    assert effect["records_changed"] > 0
    assert effect["errors_after"] < effect["errors_before"]
    assert project.undo() is True
    assert project.output_sha256() == before
    assert [e["action"] for e in project.log] == ["set_field", "undo"]


def test_rejected_edit_changes_nothing_and_is_logged(project):
    before, yaml_before = project.output_sha256(), project.mapping.to_yaml()
    for path, expr in [("genus", "$now()"), ("genus", "$trim("), ("notInSchema", "Genus")]:
        with pytest.raises(MappingError):
            project.set_field(path, expr, author="test")
    with pytest.raises(MappingError):
        project.remove_field("notMapped", author="test")
    assert project.output_sha256() == before and project.mapping.to_yaml() == yaml_before
    assert project.history == []
    assert [e["accepted"] for e in project.log] == [False] * 4


def test_edits_cannot_change_the_source_block(project):
    source = dict(project.mapping.source)
    hijack = project.mapping.copy()
    hijack.source = {"file": "/etc/passwd"}
    hijack.schema = "other.json"
    project.set_mapping(hijack, "test", "set_mapping", {})
    assert project.mapping.source == source and project.mapping.schema == "occurrence.schema.json"


def test_wrong_source_file_is_refused():
    mapping = Mapping.from_yaml(HENDERSON.read_text())
    with pytest.raises(MappingError, match="not the file this mapping was written for"):
        Project(EXCERPT.read_bytes(), "x.csv", {"type": "object"}, "s.json", mapping=mapping)


def test_cli_run_and_new(tmp_path, capsys):
    out = tmp_path / "out.jsonl"
    assert main(["run", str(HENDERSON), "--source", str(EXCERPT), "-o", str(out)]) == 0
    assert out.read_text(encoding="utf-8").count("\n") == 40
    assert "40 input rows -> 40 records" in capsys.readouterr().out
    assert main(["run", str(HENDERSON), "--source", str(EXCERPT), "--strict"]) == 1

    new = tmp_path / "new.ddt.yaml"
    schema = EXAMPLES / "henderson" / "occurrence.schema.json"
    assert main(["new", str(EXCERPT), str(schema), "-o", str(new)]) == 0
    mapping = Mapping.from_yaml(new.read_text(encoding="utf-8"))
    assert mapping.source["encoding"] == "utf-8/cp1252" and mapping.fields == {}
    assert main(["run", str(new)]) == 0
