import json

import pytest

from kb import authoring, store
from kb.errors import KBError
from kb.records import KB

from conftest import observation, set_store


def test_new_observation(kb):
    rec = observation(kb)
    assert rec.text("note") == "Noise was high.\n"
    assert rec.data["status"] == "unverified"
    assert rec.data["valid"] == {"runs": [[1234, 1234]]}
    assert rec.data["created"]["by"] == "per-tester"
    note = rec.rendition("note")
    assert note["locations"] == [{"type": "db", "filename": "note.md"}]
    again = KB.open(kb.root).get(rec.id)                 # it is in the database, not just in memory
    assert again.data == rec.data and again.text("note") == "Noise was high.\n"
    assert [e["action"] for e in kb.events(rec.id)] == ["create"]


def test_invalid_record_is_not_written(kb):
    with pytest.raises(KBError, match="not written"):
        authoring.new(kb, "observation", {"title": "x"}, text="t")   # no asserted_by
    assert KB.open(kb.root).records == {}
    assert kb.conn.execute("SELECT count(*) FROM blobs").fetchone()[0] == 0   # the content was rolled back too


def test_dangling_reference_is_refused(kb):
    with pytest.raises(KBError, match="any reachable store"):
        observation(kb, related=["obs-0000000000"])
    rec = authoring.new(kb, "observation", authoring.fields_from_options(
        title="T", summary="S", by="x", related=["obs-0000000000"]), text="t", allow_dangling=True)
    assert rec.data["related"] == ["obs-0000000000"]


def test_parse_helpers():
    assert authoring.parse_runs(["1", "10-12", "20:21,30"]) == [[1, 1], [10, 12], [20, 21], [30, 30]]
    with pytest.raises(KBError):
        authoring.parse_runs(["5-1"])
    assert authoring.parse_set(["a.b=1", "c=text", 'd=["x"]']) == {"a": {"b": 1}, "c": "text", "d": ["x"]}


def test_add_small_file_goes_into_the_store(kb, tmp_path):
    f = tmp_path / "noise.csv"
    f.write_text("run,rms\n1234,3.1\n")
    rec = authoring.add(kb, "dataset", [str(f)], {"title": "Noise", "summary": "RMS per run."})
    r = rec.rendition("original")
    assert r["locations"] == [{"type": "db", "filename": "noise.csv"}]
    assert rec.content("original") == f.read_bytes() and r["media_type"] == "text/csv"
    assert store.resolve(kb, rec, r).read_bytes() == f.read_bytes()      # materialized for tools


def test_add_large_file_goes_to_content_store(root, tmp_path):
    cas = set_store(root)
    kb = KB.open(root)
    big = tmp_path / "big.root"
    big.write_bytes(b"x" * (kb.cfg.max_inline_bytes + 1))
    rec = authoring.add(kb, "dataset", [str(big)], {"title": "Big", "summary": "A big file."})
    r = rec.rendition("original")
    assert r["locations"] == [{"type": "cas", "store": "default"}]
    assert store.blob_path(cas, r["sha256"]).exists()
    assert store.resolve(kb, rec, r) == store.blob_path(cas, r["sha256"])
    assert not kb.has_blob(r["sha256"])


def test_add_large_file_without_store_is_local_only(kb, tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"y" * (kb.cfg.max_inline_bytes + 1))
    rec = authoring.add(kb, "dataset", [str(big)], {"title": "Big", "summary": "Only here."})
    loc = rec.rendition("original")["locations"][0]
    assert loc["type"] == "local" and loc["host"] == kb.cfg.host
    assert store.resolve(kb, rec, rec.rendition("original")) == big.resolve()


def test_add_url_fetches_and_hashes(kb, tmp_path):
    f = tmp_path / "slides.md"
    f.write_text("# Slides\n")
    rec = authoring.add(kb, "document", [f.as_uri()], {"title": "Talk", "summary": "A talk.", "doctype": "presentation",
                                                       "date": "2026-10-28", "creators": ["A"]})
    r = rec.rendition("original")
    assert r["sha256"] == store.sha256_file(f)[0]
    assert [l["type"] for l in r["locations"]] == ["url", "db"] and "retrieved" in r["locations"][0]


def test_add_url_without_fetch(kb):
    rec = authoring.add(kb, "document", ["https://example.invalid/x.pdf"],
                        {"title": "T", "summary": "S", "doctype": "paper", "date": "2026", "creators": ["A"]},
                        fetch=False)
    r = rec.rendition("original")
    assert "sha256" not in r and r["locations"] == [{"type": "url", "url": "https://example.invalid/x.pdf"}]


def test_add_local_copy_with_canonical_url(kb, tmp_path):
    f = tmp_path / "s.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    rec = authoring.add(kb, "document", [str(f)], {"title": "T", "summary": "S", "doctype": "presentation",
                                                   "date": "2026", "creators": ["A"]},
                        url="https://indico.example/e/1/s.pdf")
    assert [l["type"] for l in rec.rendition("original")["locations"]] == ["db", "url"]


def test_add_refuses_duplicate_identifier(kb, tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("paper")
    fields = {"title": "P", "summary": "S", "doctype": "paper", "date": "2026", "creators": ["A"],
              "identifiers": {"arxiv": "2601.00001"}}
    authoring.add(kb, "document", [str(f)], fields)
    with pytest.raises(KBError, match="already in the KB"):
        authoring.add(kb, "document", [str(f)], fields)


def test_add_several_files(kb, tmp_path):
    a, b = tmp_path / "data.csv", tmp_path / "make.py"
    a.write_text("x\n1\n")
    b.write_text("print(1)\n")
    rec = authoring.add(kb, "dataset", [str(a), str(b)], {"title": "D", "summary": "S"})
    assert [r["name"] for r in rec.renditions] == ["data", "make"]


def test_supersede_and_retract(kb):
    old = observation(kb)
    new = authoring.supersede(kb, old.id, {"valid": {"runs": [[1243, 1243]]}}, text="It was run 1243.")
    assert new.data["supersedes"] == [old.id]
    assert new.data["subject"] == old.data["subject"]
    assert new.data["valid"]["runs"] == [[1243, 1243]]
    assert not kb.is_current(old.id) and kb.is_current(new.id)
    with pytest.raises(KBError, match="already superseded"):
        authoring.supersede(kb, old.id, {}, text="again")
    with pytest.raises(KBError, match="corrected text"):
        authoring.supersede(kb, new.id, {})
    gone = authoring.retract(kb, new.id, "Measurement artefact.", {})
    assert gone.data["retracts"] == [new.id] and not kb.is_current(new.id)


def test_from_json(kb):
    rec = authoring.from_json(kb, {"kind": "config", "title": "Sim drift", "summary": "Drift velocity used.",
                                   "system": "production/sim", "valid": {"time": {"start": "2025-01-01"}},
                                   "parameters": {"drift_mm_us": 1.6}})
    stored = json.loads(kb.conn.execute("SELECT json FROM items WHERE id = ?", (rec.id,)).fetchone()[0])
    assert stored["parameters"] == {"drift_mm_us": 1.6}


def test_locate_mirror_on_frozen_source(root, tmp_path):
    from kb import checks, edit
    kb = KB.open(root)
    big = tmp_path / "big.bin"
    big.write_bytes(b"q" * (kb.cfg.max_inline_bytes + 1))
    rec = authoring.add(kb, "dataset", [str(big)], {"title": "B", "summary": "Big."})
    cas = set_store(root, publish=False)
    kb = KB.open(root)
    loc = edit.add_location(kb, f"{rec.id}#original", "default")
    assert loc == {"type": "cas", "store": "default"}
    assert store.blob_path(cas, rec.rendition("original")["sha256"]).exists()
    assert [p for p in checks.check(KB.open(root)) if p.level == "error"] == []
    other = tmp_path / "other.bin"
    other.write_bytes(b"different")
    with pytest.raises(KBError, match="records"):
        edit.add_location(kb, f"{rec.id}#original", "default", file=other)
