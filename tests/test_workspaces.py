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


# -- topical notes in place (decisions/0005): adopting work areas, annex out of the way, sidecars --------

def git_(path, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True).stdout


def a_topic(tmp_path):
    """A work area as in ~/dune/xero/topics/NAME/WORK-TREE: a nested clone, build products, scripts, a note."""
    import subprocess
    top = tmp_path / "topics" / "noise"
    wt = top / "wt"
    for d in ("runs", "notes", "build", "install/bin", "src/lib"):
        (wt / d).mkdir(parents=True)
    code = wt / "code"
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    (code / "a.c").write_text("int main(){}\n")
    git_(code, "add", "a.c")
    git_(code, "commit", "-q", "-m", "c")
    git_(code, "remote", "add", "origin", "https://example.org/dune/code.git")
    (wt / "build" / "a.out").write_text("binary\n")
    (wt / "runs" / "run.sh").write_text("echo run\n")
    (wt / "runs" / "libfit.so").write_bytes(b"\x7fELF" * 10)
    (wt / "runs" / "fit.o").write_bytes(b"\x00" * 100)
    (wt / "runs" / "out.dat").write_bytes(bytes(range(256)) * 8192)           # 2 MB: annexed
    (wt / "notes" / "note.md").write_text("# Noise\n")
    (wt / "notes" / "note.pdf").write_bytes(b"%PDF-1.5\n" + b"x" * 300_000)    # small: plain git
    (wt / "src" / "lib" / "util.py").write_text("x = 1\n")                     # source named lib: kept
    return top


def test_adopt_a_work_area_around_what_is_there(kb, tmp_path):
    top = a_topic(tmp_path)
    before = {p: p.read_bytes() for p in top.rglob("*") if p.is_file() and ".git" not in p.parts}
    r = kb_cli(kb.root, "artifact", "adopt", str(top), "--title", "Noise topic")
    assert str(top) in r.stdout
    assert {p: p.read_bytes() for p in before} == before                          # nothing changed
    tracked = set(git_(top, "ls-files").split())
    assert {"wt/runs/run.sh", "wt/notes/note.md", "wt/notes/note.pdf", "wt/src/lib/util.py",
            "wt/runs/out.dat", ".kb-artifact/workspace.json", ".kb-artifact/repos.json"} <= tracked
    assert not any(t.startswith(("wt/code", "wt/build", "wt/install")) or t.endswith((".so", ".o")) for t in tracked)
    out = top / "wt" / "runs" / "out.dat"
    assert not out.is_symlink() and "key: SHA256E" in git_(top, "annex", "info", "wt/runs/out.dat")   # annexed, unlocked
    out.write_bytes(out.read_bytes() + b"more")                                  # writable: no locked file to trip on
    assert git_(top, "annex", "find", "wt/notes/note.pdf") == ""                  # small: not annexed
    repos = json.loads((top / ".kb-artifact" / "repos.json").read_text())
    assert repos["wt/code"]["remotes"] == {"origin": "https://example.org/dune/code.git"}
    assert repos["wt/code"]["head"] == git_(top / "wt" / "code", "rev-parse", "HEAD").strip()
    assert "SHA256E" in (top / ".gitattributes").read_text()
    assert "ignored the repository wt/code" in r.stderr

    # again: settings refreshed, a new clone picked up, no second workspace
    import subprocess
    subprocess.run(["git", "init", "-q", str(top / "wt" / "more")], check=True)
    r2 = kb_cli(kb.root, "artifact", "adopt", str(top))
    assert "wt/more" in json.loads((top / ".kb-artifact" / "repos.json").read_text())
    assert "wt/more" not in git_(top, "ls-files")
    assert git_(top, "status", "--porcelain").strip() == ""
    assert r2.exit_code == 0
    srcs = [x for x in Store.open(kb.root).items.values() if x.kind == "source"]
    assert len(srcs) == 1


def test_adopt_keeps_a_larger_threshold_and_extra_ignores(kb, tmp_path):
    top = a_topic(tmp_path)
    (top / "wt" / "runs" / "fits.root").write_bytes(b"r" * 10)
    kb.set_setting("workspace", {"ignore": ["*.root"], "largefiles": "100KB"})
    kb_cli(kb.root, "artifact", "adopt", str(top), "--ignore", "scratch/")
    ignores = (top / ".gitignore").read_text().splitlines()
    assert {"*.root", "scratch/", "*.so", "/bin/", "build/"} <= set(ignores)
    assert "largerthan=100KB" in (top / ".gitattributes").read_text()
    assert git_(top, "annex", "find", "wt/notes/note.pdf").strip() == "wt/notes/note.pdf"     # 300 KB: annexed now
    assert not (top / "wt" / "notes" / "note.pdf").is_symlink()


def test_start_makes_a_dataset_with_annex_out_of_the_way(kb, tmp_path):
    ws = tmp_path / "ws"
    kb_cli(kb.root, "artifact", "start", str(ws), "--title", "T")
    assert git_(ws, "config", "annex.addunlocked").strip() == "true"
    (ws / "fig.png").write_bytes(b"\x89PNG" + b"0" * 100)
    (ws / "big.bin").write_bytes(b"1" * 2_000_000)
    (ws / "libx.so").write_bytes(b"elf")
    artifacts._save(ws, "files")
    assert "fig.png" in git_(ws, "ls-files") and git_(ws, "annex", "find", "fig.png") == ""   # binary, small: git
    assert not (ws / "big.bin").is_symlink() and git_(ws, "annex", "find", "big.bin").strip() == "big.bin"
    assert "libx.so" not in git_(ws, "ls-files")
    with pytest.raises(Exception, match="adopt"):
        artifacts.start(Store.open(kb.root), a_topic(tmp_path / "x"), "T2")


def test_a_note_sidecar_and_where_it_is_published(kb, tmp_path):
    from kb.errors import KBError
    ws = tmp_path / "ws"
    kb_cli(kb.root, "artifact", "start", str(ws), "--title", "Note")
    o = observation(kb)
    (ws / "note.org").write_text("* Noise\nRun 1234 was noisy.\n")
    (ws / "note.html").write_text("<html><body><h1>Noise</h1>Run 1234 was noisy.</body></html>")
    (ws / "note.toml").write_text(
        f'title = "Noise note"\nsummary = "Run 1234 was noisy."\nsubject = "detector/tpc/noise"\n'
        f'creators = ["A. Person"]\ndate = 2026-11-09\ntags = ["noise"]\ninputs = ["{o.id}"]\n'
        'source = ["note.org"]\nbuild = "emacs --batch note.org -f org-html-export-to-html"\n')
    r = kb_cli(kb.root, "artifact", "finish", str(ws / "note.html"), "--summary", "Overridden on the command line.",
               "--published-at", "https://claude.ai/artifact/abc-123")
    rec = Store.open(kb.root).get(r.stdout.split()[0])
    d = rec.data
    assert (d["title"], d["doctype"], d["subject"], d["creators"], d["date"], d["tags"]) == (
        "Noise note", "technote", ["detector/tpc/noise"], ["A. Person"], "2026-11-09", ["noise"])
    assert d["summary"] == "Overridden on the command line."
    assert d["identifiers"] == {"url": "https://claude.ai/artifact/abc-123", "claude-artifact": "abc-123"}
    assert [x["ref"] for x in d["derived_from"]] == [o.id]                       # an input from the sidecar
    process = json.loads(rec.text("process"))
    assert process["note"] == {"sidecar": "note.toml", "source": ["note.org"],
                               "build": "emacs --batch note.org -f org-html-export-to-html"}
    (ws / "note.toml").write_text('titel = "typo"\n')
    with pytest.raises(KBError, match="unknown key.*titel"):
        artifacts.read_sidecar(ws / "note.toml")
    (ws / "note.toml").write_text('title = 3\n')
    with pytest.raises(KBError, match="title must be a string"):
        artifacts.read_sidecar(ws / "note.toml")
    # a NAME.note.toml beside the output wins over note.toml
    (ws / "note.note.toml").write_text('title = "Specific"\n')
    assert artifacts.sidecar_path(ws / "note.html", ws) == ws / "note.note.toml"


def test_doctor_unlocks_a_workspace_that_would_lock_files(kb, tmp_path):
    from kb import doctor
    ws = tmp_path / "ws"
    kb_cli(kb.root, "artifact", "start", str(ws), "--title", "T")
    kb = Store.open(kb.root)
    assert doctor.diagnose(kb, ["workspace-annex"]) == []
    git_(ws, "config", "--unset", "annex.addunlocked")
    (f,) = doctor.diagnose(kb, ["workspace-annex"])
    assert f.mode == "auto" and "read-only" in f.summary
    doctor.REMEDIES["workspace-annex"].apply(kb, f)
    assert git_(ws, "config", "annex.addunlocked").strip() == "true"
    assert doctor.diagnose(kb, ["workspace-annex"]) == []
