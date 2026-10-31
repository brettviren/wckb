import pytest

from kb import ids, schema
from kb.errors import KBError

BASE = {"schema_version": 1, "title": "T", "summary": "S",
        "created": {"at": "2026-10-30T12:00:00Z", "by": "per-x"}, "access": "group"}
NOTE = {"name": "note", "role": "content", "media_type": "text/markdown",
        "sha256": "0" * 64, "size": 1, "locations": [{"type": "db", "filename": "note.md"}]}


def rec(kind, **kw):
    return {**BASE, "id": ids.new_id(kind), "kind": kind, **kw}


VALID = [
    rec("observation", status="unverified", asserted_by=[{"who": "A"}], renditions=[NOTE],
        valid={"runs": [[1, 2]]}),
    rec("document", doctype="paper", date="2026", creators=["A"], identifiers={"doi": "10.1234/abc"}),
    rec("dataset", renditions=[{**NOTE, "role": "original", "name": "data", "media_type": "text/csv"}]),
    rec("config", system="production/sim", valid={"time": {"start": "2025"}}, parameters={"x": 7}),
    rec("snapshot", system="conditions/db", query="SELECT 1", retrieved="2026-10-30T12:00:00Z",
        renditions=[{**NOTE, "role": "original"}]),
    rec("software", repository="https://github.com/x/y", commit="abcdef1"),
    rec("topic", subject=["a/b"], derived_from=["obs-0000000000"], renditions=[{**NOTE, "name": "page"}]),
    rec("view", derived_from=[], build={"tool": "pandoc", "target": "index.md"}),
    {**BASE, "id": "per-ann", "kind": "person", "name": "Ann"},
]


@pytest.mark.parametrize("data", VALID, ids=lambda d: d["kind"])
def test_valid_examples(data):
    assert schema.validate(data) == []


def test_unknown_field_is_caught():
    data = {**VALID[0], "stauts": "verified"}
    assert any("stauts" in e for e in schema.validate(data))


def test_extensions_go_under_x():
    assert schema.validate({**VALID[0], "x": {"anything": [1, 2]}}) == []


def test_observation_needs_content_and_attribution():
    data = {k: v for k, v in VALID[0].items() if k != "asserted_by"}
    assert any("asserted_by" in e for e in schema.validate(data))
    data = {**VALID[0], "renditions": [{**NOTE, "role": "derived", "derived_from": "x",
                                        "fidelity": "llm", "transform": {"name": "t"}}]}
    assert schema.validate(data)


def test_derived_rendition_needs_provenance():
    r = {**NOTE, "role": "derived"}
    errs = schema.validate({**VALID[2], "renditions": VALID[2]["renditions"] + [{**r, "name": "t"}]})
    assert any("derived_from" in e for e in errs)


def test_id_prefix_must_match_kind():
    data = {**VALID[1], "id": "obs-0000000000"}
    assert schema.validate(data)


def test_bad_values():
    assert schema.validate({**VALID[0], "subject": ["Detector/TPC"]})
    assert schema.validate({**VALID[0], "valid": {"runs": [[1]]}})
    assert schema.validate({**VALID[0], "access": "secret"})
    assert schema.validate({**VALID[7], "build": {"tool": "command"}})  # command needs argv


def test_version_mismatch_is_explained():
    errs = schema.validate({**VALID[0], "schema_version": 2})
    assert "kb migrate" in errs[0]


def test_revision_gate():
    schema.check_revision("1.0")
    with pytest.raises(KBError, match="upgrade kb"):
        schema.check_revision("9.0")


def test_every_kind_has_a_schema():
    for k in ids.KINDS:
        assert schema.show(k)["properties"]["kind"]["const"] == k


def test_ids_and_refs():
    i = ids.new_id("observation")
    assert ids.is_id(i) and ids.kind_of(i) == "observation"
    assert ids.parse_ref(i + "#text") == (i, "text")
    assert ids.new_id("person", "ann") == "per-ann"
    with pytest.raises(KBError):
        ids.new_id("observation", "ann")
    with pytest.raises(KBError):
        ids.parse_ref("nonsense")


def test_reference_doc_in_sync():
    from pathlib import Path
    from kb.schemadoc import render
    doc = Path(__file__).parent.parent / "docs" / "reference" / "schema.md"
    assert doc.read_text() == render(), "regenerate with: uv run kb schema --markdown > docs/reference/schema.md"
