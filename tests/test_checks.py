import copy
import json
import sqlite3

import pytest

from kb import authoring, checks, edit, lint
from kb.errors import KBError
from kb.items import Store, Item

from conftest import observation


def codes(probs, level="error"):
    return sorted({p.code for p in probs if p.level == level})


def test_clean_store_passes(kb):
    observation(kb)
    assert checks.check(kb) == []


# -- the database guards -------------------------------------------------------------

def test_frozen_items_cannot_change_what_they_assert(kb):
    rec = observation(kb)
    data = copy.deepcopy(rec.data)
    data["valid"] = {"runs": [[1243, 1243]]}
    with pytest.raises(KBError, match="append-only"):
        kb.save(Item(data, kb))
    assert Store.open(kb.root).get(rec.id).data["valid"] == {"runs": [[1234, 1234]]}


def test_frozen_content_cannot_change(kb):
    rec = observation(kb)
    sha, size = kb.put_blob(b"Rewritten.\n")
    data = copy.deepcopy(rec.data)
    data["renditions"][0].update(sha256=sha, size=size)
    with pytest.raises(KBError, match="append-only"):
        kb.save(Item(data, kb))


def test_frozen_items_cannot_be_deleted_even_with_raw_sql(kb):
    rec = observation(kb)
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        kb.conn.execute("DELETE FROM items WHERE id = ?", (rec.id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        kb.conn.execute("UPDATE items SET core = 'x' WHERE id = ?", (rec.id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        kb.conn.execute("DELETE FROM events")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        kb.conn.execute("UPDATE blobs SET data = x'00'")


def test_relationship_fields_may_change(kb):
    rec = observation(kb)
    edit.set_fields(kb, rec.id, ["summary=Better summary.", "status=disputed"], add=["subject=detector/daq"])
    got = Store.open(kb.root).get(rec.id).data
    assert got["status"] == "disputed" and "detector/daq" in got["subject"]
    assert [e["action"] for e in kb.events(rec.id)] == ["create", "set"]


def test_recording_a_missing_hash_is_allowed_once(kb):
    rec = authoring.add(kb, "document", ["https://example.invalid/x.pdf"],
                        {"title": "T", "summary": "S", "doctype": "paper", "date": "2026", "creators": ["A"]},
                        fetch=False)
    data = copy.deepcopy(rec.data)
    data["renditions"][0].update(sha256="a" * 64, size=3)
    kb.save(Item(data, kb), "verify")                              # first sight is recorded
    data["renditions"][0].update(sha256="b" * 64)
    with pytest.raises(KBError, match="append-only"):
        kb.save(Item(data, kb), "verify")                          # but never changed afterwards


def test_set_refuses_frozen_fields(kb):
    rec = observation(kb)
    with pytest.raises(KBError, match="supersede"):
        edit.set_fields(kb, rec.id, ["valid.runs=[[1,2]]"])
    with pytest.raises(KBError, match="cannot set"):
        edit.set_fields(kb, rec.id, ["id=obs-zzzzzzzzzz"])
    with pytest.raises(KBError, match="not changed"):
        edit.set_fields(kb, rec.id, ["status=sure"])


def test_editable_items_change_freely(kb):
    o = observation(kb)
    t = authoring.new(kb, "topic", {"title": "T", "summary": "S", "subject": ["a"], "derived_from": [o.id]},
                      text=f"Page [{o.id}]")
    edit.set_content(kb, t.id, "Revised page.\n")
    assert Store.open(kb.root).get(t.id).text("page") == "Revised page.\n"
    with pytest.raises(KBError, match="frozen"):
        edit.set_content(kb, o.id, "Rewritten observation.\n")


def test_transactions_roll_back_together(kb):
    with pytest.raises(RuntimeError):
        with kb.tx():
            observation(kb, title="First")
            raise RuntimeError("boom")
    assert Store.open(kb.root).items == {}


def test_network_filesystem_refused(tmp_path, monkeypatch):
    from kb import db
    monkeypatch.setattr(db, "fs_type", lambda p: "nfs4")
    with pytest.raises(KBError, match="network filesystem"):
        authoring_store = tmp_path / "s"
        from kb.scaffold import init
        init(authoring_store)
    monkeypatch.setenv("KB_ALLOW_NETWORK_FS", "1")
    from kb.scaffold import init
    init(tmp_path / "t")


# -- the consistency scan -----------------------------------------------------------

def test_tampering_outside_kb_is_found(kb):
    rec = observation(kb)
    kb.conn.execute("UPDATE items SET json = replace(json, '1234', '999') WHERE id = ?", (rec.id,))
    assert codes(checks.check(Store.open(kb.root))) == ["tamper"]


def test_missing_content(kb):
    rec = observation(kb)
    data = copy.deepcopy(rec.data)
    data["renditions"].append({"name": "extra", "role": "derived", "media_type": "text/plain", "sha256": "c" * 64,
                               "size": 1, "locations": [{"type": "db"}], "derived_from": "note",
                               "fidelity": "extracted", "transform": {"name": "x"}})
    kb.save(Item(data, kb))
    assert codes(checks.check(kb)) == ["missing"]


def test_access_rules(kb):
    observation(kb, access="private")             # this store is "group"
    assert codes(checks.check(kb)) == ["access"]
    o = observation(kb, access="group")
    t = authoring.new(kb, "topic", {"title": "T", "summary": "S", "subject": ["a"], "access": "public",
                                    "derived_from": [o.id]}, text="y")
    assert any(p.where == t.id and p.code == "access" for p in checks.check(kb))


def test_verified_needs_real_evidence(kb, tmp_path):
    rec = observation(kb)
    f = tmp_path / "s.md"
    f.write_text("slides")
    doc = authoring.add(kb, "document", [str(f)], {"title": "D", "summary": "S", "doctype": "presentation",
                                                   "date": "2026", "creators": ["A"]})
    digest = tmp_path / "digest.md"
    digest.write_text("An LLM read the plots.")
    edit.add_rendition(kb, doc.id, digest, "digest", "derived", "llm", "original", "claude")
    edit.set_fields(kb, rec.id, ["status=verified"], add=[f"evidence={doc.id}#digest"])
    probs = checks.check(Store.open(kb.root))
    assert codes(probs) == ["evidence"] and "not evidence" in probs[0].hint
    edit.set_fields(kb, rec.id, remove=[f"evidence={doc.id}#digest"], add=[f"evidence={doc.id}#original"])
    assert checks.check(Store.open(kb.root)) == []


def test_dangling_references_are_warnings(kb):
    authoring.new(kb, "observation", authoring.fields_from_options(
        title="T", summary="S", by="x", related=["obs-0000000000"]), text="t", allow_dangling=True)
    probs = checks.check(kb)
    assert codes(probs) == [] and codes(probs, "warning") == ["dangling"]


def test_lint_findings(kb, tmp_path):
    a = observation(kb, runs=("1200-1300",))
    b = observation(kb, runs=("1250",), summary="Different claim about the same runs.")
    observation(kb, runs=("2000",), subject=("detector/pds",))
    big = tmp_path / "big.bin"
    big.write_bytes(b"z" * (kb.cfg.max_inline_bytes + 1))
    d = authoring.add(kb, "dataset", [str(big)], {"title": "Big data", "summary": "Big data"})
    found = {p.code: p for p in lint.lint(kb)}
    assert set(found) >= {"overlap", "local-only", "summary", "subject"}
    assert {a.id, b.id} == set(found["overlap"].where.split())
    assert found["local-only"].where == f"{d.id}#original"


def test_lint_old_unverified(kb):
    rec = observation(kb)
    data = copy.deepcopy(rec.data)
    data["created"]["at"] = "2020-01-01T00:00:00Z"
    kb.conn.execute("DROP TRIGGER items_frozen")          # simulate an old item
    kb.save(Item(data, kb))
    assert any(p.code == "unverified" for p in lint.lint(Store.open(kb.root)))


def test_json_columns_round_trip(kb):
    rec = observation(kb)
    row = kb.conn.execute("SELECT json FROM items WHERE id = ?", (rec.id,)).fetchone()
    assert json.loads(row[0]) == rec.data
