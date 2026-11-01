"""Phase 2 of artifacts (decisions/0004): workspaces, journals, runs, hooks, harvest."""

import json
import sys

import pytest
from click.testing import CliRunner

from kb import artifacts, datasets, hooks, journal
from kb.cli import main
from kb.items import Store

from conftest import observation

pytestmark = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")


def kb_cli(root, *args):
    r = CliRunner().invoke(main, ["-C", str(root), *args], catch_exceptions=False)
    assert r.exit_code == 0, r.output
    return r


def test_make_an_artifact_in_a_workspace(kb, tmp_path, monkeypatch):
    noise = observation(kb, title="Coherent noise fell 20%", text="Coherent noise fell 20% after grounding.")
    other = observation(kb, title="Photon gain drifted", subject=("detector/pds",), text="PDS gain drifted 3%.")
    data = authoring_dataset(kb, tmp_path)
    monkeypatch.setenv("KB_SESSION", "sess-1")

    # read something elsewhere in the same session, before the workspace exists: kept apart
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    kb_cli(kb.root, "show", other.id)

    ws = tmp_path / "noise-note"
    kb_cli(kb.root, "artifact", "start", str(ws), "--title", "TPC noise note")
    (ws / "figs" / "drafts").mkdir(parents=True)
    assert journal.workspace(ws / "figs" / "drafts") == ws.resolve()     # found from anywhere inside
    monkeypatch.chdir(ws)
    assert journal.workspace() == ws.resolve()

    # read the knowledge through kb, here: these become the inputs
    kb_cli(kb.root, "show", noise.id)
    kb_cli(kb.root, "item", "content", noise.id, "note")
    kb_cli(kb.root, "show", other.id)                                     # read, but not used: dropped later

    # an LLM session in the workspace: a prompt and an edit, as the hooks see them
    hooks.artifact({"hook_event_name": "UserPromptSubmit", "cwd": str(ws), "session_id": "sess-1",
                    "prompt": "Plot the noise drop and write a paragraph about it."})
    (ws / "note.md").write_text("# TPC noise\n\nCoherent noise fell by 20% [fig].\n")
    hooks.artifact({"hook_event_name": "PostToolUse", "session_id": "sess-1", "tool_name": "Write",
                    "tool_input": {"file_path": str(ws / "note.md")}})
    journal.append("session", kb, start=tmp_path, session="sess-1", model="claude-opus-5-5 (Claude Code)")

    # code that turns knowledge into a figure, under datalad run
    (ws / "plot.py").write_text("import sys\nopen('fig.txt', 'w').write(open(sys.argv[1]).read().upper())\n")
    r = kb_cli(kb.root, "artifact", "run", "--input", data.id, "--", sys.executable, "plot.py",
               f"inputs/{data.id}-noise.csv")
    assert (ws / "fig.txt").read_text() == "RUN,NOISE\n1234,1.2\n"

    rows = {c["id"]: c for c in artifacts.candidates(kb)}
    assert rows[noise.id]["where"] == "here" and rows[noise.id]["reads"] == 2
    assert rows[data.id]["where"] == "here" and "run" in rows[data.id]["how"]

    # finish: the output becomes the artifact
    r = kb_cli(kb.root, "artifact", "finish", "note.md", "--summary", "Noise fell 20% after grounding.",
               "--creators", "A. Person", "--date", "2026-11-02", "--subject", "detector/tpc/noise",
               "--drop", other.id, "--json")
    out = json.loads(r.stdout)
    kb = Store.open(kb.root)
    art = kb.get(out["artifact"])
    assert sorted(d["ref"] for d in art.data["derived_from"]) == sorted([noise.id, data.id])
    assert all("digest" in d for d in art.data["derived_from"])
    assert art.data["doctype"] == "technote" and art.data["provenance"] == "recorded"
    assert art.rendition("original")["locations"][0]["type"] == "datalad"      # it lives in its workspace
    steps = artifacts.steps_view(kb, art.id)["steps"]
    types = [s["type"] for s in steps]
    assert types[0] == "kb" and "code" in types and "llm" in types
    llm = next(s for s in steps if s["type"] == "llm")
    assert llm["model"] == "claude-opus-5-5 (Claude Code)" and llm["files"] == ["note.md"] and llm["prompts"] == 1
    (pid,) = out["prompts"]
    prompt = kb.get(pid)
    assert prompt.data["doctype"] == "prompt" and prompt.data["links"] == [{"rel": "part-of", "ref": art.id}]
    assert prompt.data["created"]["with"] == ["claude-opus-5-5 (Claude Code)"]
    assert "Plot the noise drop" in prompt.text("note")
    text = kb_cli(kb.root, "artifact", "steps", art.id).stdout
    assert "ran " in text and "LLM session" in text

    # finishing again makes a new version
    (ws / "note.md").write_text("# TPC noise\n\nCoherent noise fell by 20%.\n")
    out2 = json.loads(kb_cli(kb.root, "artifact", "finish", "note.md", "--summary", "Second version.",
                             "--creators", "A. Person", "--date", "2026-11-03", "--drop", other.id,
                             "--json").stdout)
    kb = Store.open(kb.root)
    assert kb.get(out2["artifact"]).data["supersedes"] == [art.id] and not kb.is_current(art.id)
    assert out2["prompts"] == []                                          # already harvested


def authoring_dataset(kb, tmp_path):
    from kb import authoring
    f = tmp_path / "noise.csv"
    f.write_text("run,noise\n1234,1.2\n")
    return authoring.add(kb, "dataset", [str(f)], {"title": "Noise by run", "summary": "Coherent noise by run."})


def test_outside_a_workspace(kb, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Exception, match="not in an artifact workspace"):
        artifacts.candidates(kb)
    o = observation(kb)
    kb_cli(kb.root, "show", o.id)                      # outside any workspace: the store's journal
    (e,) = [e for e in journal.entries(kb.cfg.kbdir / journal.JOURNAL) if e["kind"] == "served"]
    assert e["items"][0]["id"] == o.id and e["how"] == "show"


def test_session_hook_sets_the_session(tmp_path, monkeypatch):
    env = tmp_path / "env"
    monkeypatch.setenv("CLAUDE_ENV_FILE", str(env))
    hooks._set_coauthor({"model": "m", "session_id": "abc"})
    assert "export KB_SESSION=abc" in env.read_text()
