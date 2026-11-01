"""Phase 1 of artifacts (decisions/0004): co-authors, LLM summaries, artifacts as documents, HTML in place."""

import pytest
from click.testing import CliRunner

from kb import artifacts, authoring, datasets, doctor, edit, graph, hooks, opener, search
from kb.cli import main
from kb.errors import KBError
from kb.items import Store

from conftest import observation

DOC = {"title": "Noise technote", "summary": "What we know about TPC noise.", "doctype": "technote",
       "creators": ["A"], "date": "2026-11"}


def kb_cli(root, *args, env=None):
    return CliRunner().invoke(main, ["-C", str(root), *args], catch_exceptions=False, env=env)


def test_coauthors_on_items_and_events(kb, monkeypatch):
    r = kb_cli(kb.root, "--coauthor", "claude-opus-5-5 (Claude Code)", "new", "observation", "--title", "T",
               "--summary", "S.", "--by", "claude", "--how", "inferred", "--text", "x")
    rec = Store.open(kb.root).get(r.stdout.split()[0])
    assert rec.data["created"]["with"] == ["claude-opus-5-5 (Claude Code)"]
    log = kb_cli(kb.root, "log", rec.id).stdout
    assert "with claude-opus-5-5 (Claude Code)" in log
    monkeypatch.setenv("KB_COAUTHOR", "model-x; Bob")
    kb2 = Store.open(kb.root)
    o = observation(kb2)
    assert o.data["created"]["with"] == ["model-x", "Bob"]
    assert kb2.events(o.id)[0]["detail"]["with"] == ["model-x", "Bob"]


def test_session_hook_sets_the_coauthor(tmp_path, monkeypatch):
    env = tmp_path / "env"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env))
    hooks._set_coauthor({"model": "claude-opus-5-5"})
    assert env.read_text() == "export KB_COAUTHOR='claude-opus-5-5 (Claude Code)'\n"
    assert hooks.coauthor({}) == "Claude Code"


def test_llm_summary_is_indexed_and_goes_stale(kb, tmp_path):
    t = authoring.new(kb, "topic", {"title": "Noise", "summary": "Noise.", "subject": ["detector/tpc"],
                                    "derived_from": [observation(kb).id]}, text="Run 1234 was noisy.")
    s = tmp_path / "summary.md"
    s.write_text("Mostly about grounding loops and their cure.\n")
    r = edit.set_summary(kb, t.id, s, model="claude-opus-5-5")
    assert (r["name"], r["fidelity"], r["derived_from"], r["transform"]) == (
        "summary", "llm", "page", {"name": "llm", "version": "claude-opus-5-5"})
    kb = Store.open(kb.root)
    (hit,) = search.search(kb, "grounding loops")
    assert hit["rendition"] == "summary" and hit["from"] == "page"
    edit.set_content(kb, t.id, "Run 1243, not 1234, was noisy.\n")       # what it summarizes changed
    found = [f for f in doctor.diagnose(Store.open(kb.root), ["stale"]) if f.where == f"{t.id}#summary"]
    assert found and found[0].mode == "manual" and "kb rendition summary" in found[0].fix
    s.write_text("About run 1243.\n")
    edit.set_summary(Store.open(kb.root), t.id, s, model="claude-opus-5-5")       # replaced, not added
    kb = Store.open(kb.root)
    assert [x["name"] for x in kb.get(t.id).renditions].count("summary") == 1
    with pytest.raises(KBError, match="which model wrote it"):
        edit.set_summary(kb, t.id, s)


def test_an_artifact_goes_stale_and_is_restamped(kb, tmp_path):
    o = observation(kb)
    pdf = tmp_path / "note.md"
    pdf.write_text("# Noise\nRun 1234 was noisy.\n")
    a = authoring.add(kb, "document", [str(pdf)], {**DOC, "provenance": "cited", "derived_from": [o.id]})
    assert a.data["derived_from"][0]["ref"] == o.id and "digest" in a.data["derived_from"][0]   # pinned
    kb = Store.open(kb.root)
    (row,) = artifacts.listing(kb)
    assert (row["id"], row["doctype"], row["provenance"], row["inputs"], row["stale"]) == (
        a.id, "technote", "cited", 1, [])
    fixed = authoring.supersede(kb, o.id, {}, text="Run 1243, not 1234.")
    kb = Store.open(kb.root)
    (row,) = artifacts.listing(kb, stale_only=True)
    assert "superseded" in row["stale"][0]
    assert [s.what for s in graph.stale(kb)] == ["artifact"]
    edit.set_fields(kb, a.id, replace_ref=[f"{o.id}={fixed.id}"])      # revised: now it presents the fix
    graph.stamp(Store.open(kb.root), a.id)                              # a frozen document can be restamped
    assert artifacts.listing(Store.open(kb.root), stale_only=True) == []
    assert "STALE" not in kb_cli(kb.root, "artifact", "list").stdout


@pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")
def test_a_demo_opens_in_its_source(kb, tmp_path):
    import datalad.api as dl
    ws = tmp_path / "demo"
    dl.create(str(ws), result_renderer="disabled")
    (ws / "site").mkdir()
    (ws / "site" / "index.html").write_text('<html><body><script src="plot.js"></script>A demo.</body></html>')
    (ws / "site" / "plot.js").write_text("console.log('plot');\n")
    dl.save(dataset=str(ws), result_renderer="disabled")
    mine = tmp_path / "mine"                                  # a clone, as another machine would have
    dl.clone(source=str(ws), path=str(mine), result_renderer="disabled")
    dl.get(str(mine / "site"), dataset=str(mine), result_renderer="disabled")
    a = authoring.add(kb, "document", [str(mine / "site" / "index.html")], {**DOC, "doctype": "demo"}, guess=False)
    assert a.rendition("original")["locations"][0]["type"] == "datalad"
    dl.drop(str(mine / "site"), dataset=str(mine), recursive=True, result_renderer="disabled")
    assert not (mine / "site" / "plot.js").exists()
    _, _, p = opener.path(Store.open(kb.root), a.id)
    assert p == mine / "site" / "index.html"                 # in the tree, beside its script
    assert (mine / "site" / "plot.js").read_text() == "console.log('plot');\n"   # got with it


def test_kinds_and_doctypes_are_discoverable(root):
    out = CliRunner().invoke(main, ["new", "document", "--help"]).output
    assert "demo" in out and "notebook" in out and "prompt" in out and "provenance" in out
