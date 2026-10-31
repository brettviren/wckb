"""DataLad datasets as sources: source items, locations, clones, transfer, assets."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kb import assets, authoring, bundle, content, datasets
from kb.config import source_dir
from kb.errors import KBError
from kb.items import Store
from kb.push import make_kset

from conftest import make_store

pytestmark = pytest.mark.skipif(datasets.available() is not None, reason=f"DataLad: {datasets.available()}")


def _write(path: Path, text: str) -> None:
    if os.path.lexists(path):
        path.unlink()                     # annexed files are read-only symlinks
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def origin(tmp_path) -> Path:
    """A dataset with a talk in two tagged versions and a small config kept in git."""
    import datalad.api as dl
    root = tmp_path / "origin"
    dl.create(str(root), result_renderer="disabled")
    _write(root / "talks" / "noise" / "slides.txt", "TPC noise, version one\n")
    _write(root / "talks" / "noise" / "notes.txt", "speaker notes\n")
    dl.save(dataset=str(root), message="v1", result_renderer="disabled")
    subprocess.run(["git", "-C", str(root), "tag", "v1"], check=True)
    _write(root / "talks" / "noise" / "slides.txt", "TPC noise, version two\n")
    dl.save(dataset=str(root), message="v2", result_renderer="disabled")
    subprocess.run(["git", "-C", str(root), "tag", "v2"], check=True)
    return root


def _doc(kb, path):
    return authoring.add(kb, "document", [str(path)], {"title": "TPC noise", "summary": "Slides.",
                                                       "doctype": "presentation", "date": "2026", "creators": ["A"]})


def test_adding_a_committed_file_keeps_it_in_the_dataset(kb, origin):
    rec = _doc(kb, origin / "talks" / "noise" / "slides.txt")
    loc = rec.rendition("original")["locations"]
    assert [l["type"] for l in loc] == ["datalad"] and loc[0]["path"] == "talks/noise/slides.txt"
    assert loc[0]["key"].startswith(("MD5E-", "SHA256E-"))
    src = Store.open(kb.root).get(loc[0]["source"])
    assert src.kind == "source" and src.data["source_id"] == datasets.dataset_id(origin)
    assert src.data["paths"] == [{"host": kb.cfg.host, "path": str(origin)}]
    kb = Store.open(kb.root)
    p = content.resolve(kb, kb.get(rec.id), kb.get(rec.id).rendition("original"))
    assert p.read_text() == "TPC noise, version two\n"
    assert _doc(kb, origin / "talks" / "noise" / "notes.txt").rendition("original")["locations"][0]["source"] == src.id


def test_uncommitted_files_are_placed_as_before(kb, origin):
    _write(origin / "draft.txt", "not saved yet\n")
    rec = _doc(kb, origin / "draft.txt")
    assert rec.rendition("original")["locations"][0]["type"] == "db"
    with pytest.raises(KBError, match="not committed"):
        authoring.add(kb, "document", [str(origin / "draft.txt")], {"title": "D", "summary": "S",
                      "doctype": "note", "date": "2026", "creators": ["A"]}, mode="datalad")


def test_a_kset_carries_its_sources_and_a_receiver_reuses_the_clone(kb, origin, tmp_path):
    rec = _doc(kb, origin / "talks" / "noise" / "slides.txt")
    ks = make_kset(Store.open(kb.root), [rec.id], "shallow")          # shallow: the source comes along anyway
    b = bundle.make(Store.open(kb.root), ks.id)
    src_id = rec.rendition("original")["locations"][0]["source"]
    assert src_id in {i["data"]["id"] for i in b["items"]}
    other = make_store(tmp_path / "other", "group", "other")
    bundle.apply(other, b)
    other = Store.open(other.root)
    p = content.resolve(other, other.get(rec.id), other.get(rec.id).rendition("original"))
    assert p is not None and Path(str(p)).resolve().is_relative_to(origin.resolve())     # the same clone, reused


def test_a_new_machine_clones_into_the_source_directory(kb, origin, tmp_path, monkeypatch):
    src = datasets.describe(kb, origin.as_uri())                   # by URL: cloned to learn its identity
    assert src.data["urls"] == [origin.as_uri()] and src.data["paths"] == []
    rec = _doc(kb, origin / "talks" / "noise" / "slides.txt")
    assert rec.rendition("original")["locations"][0]["source"] == src.id      # the dataset's existing source item
    b = bundle.make(Store.open(kb.root), make_kset(Store.open(kb.root), [rec.id], "shallow").id)

    # Another machine: another host name, another user config (so no registry), its own clone directory.
    monkeypatch.setenv("KB_CONFIG", str(tmp_path / "elsewhere.toml"))
    there = make_store(tmp_path / "there", "home", "there")
    there.set_setting("host", "elsewhere")
    there.set_setting("sources", {"datalad": {"dir": str(tmp_path / "clones")}})
    bundle.apply(Store.open(there.root), b)
    there = Store.open(there.root)
    used = there.get(src.id)
    p = content.resolve(there, there.get(rec.id), there.get(rec.id).rendition("original"))
    assert p.read_text() == "TPC noise, version two\n"
    clone = datasets.locate(there, used)
    assert clone.parent == tmp_path / "clones" and datasets.dataset_id(clone) == datasets.dataset_id(origin)


def test_ask_for_a_location_or_stop_with_instructions(kb, origin, tmp_path, monkeypatch):
    src = datasets.describe(kb, str(origin))
    monkeypatch.setenv("KB_CONFIG", str(tmp_path / "fresh.toml"))
    kb.set_setting("host", "elsewhere")
    kb.set_setting("sources", {"datalad": {"new": "ask"}})
    kb = Store.open(kb.root)
    datasets.add_places(kb, kb.get(src.id), [origin.as_uri()], [])
    kb = Store.open(kb.root)
    monkeypatch.setattr(datasets, "ASK", None)
    with pytest.raises(KBError, match="kb source clone"):
        datasets.locate(kb, kb.get(src.id), clone=True)
    monkeypatch.setattr(datasets, "ASK", lambda rec, suggestion: tmp_path / "chosen")
    assert datasets.locate(kb, kb.get(src.id), clone=True) == (tmp_path / "chosen").resolve()


def test_two_source_items_for_one_dataset_share_a_clone(kb, origin, tmp_path):
    mine = datasets.describe(kb, str(origin), title="my copy")
    other = make_store(tmp_path / "other", "group", "other")
    theirs = datasets.describe(other, [str(origin)], title="their description")
    assert mine.id != theirs.id
    rec = _doc(other, origin / "talks" / "noise" / "slides.txt")
    from kb.transfer import plan
    p = plan(Store.open(other.root), Store.open(kb.root), make_kset(Store.open(other.root), [rec.id], "shallow").data)
    note = [n for e in p.items if e.id == theirs.id for n in e.notes]
    assert note and mine.id in note[0]


def test_asset_versions_from_tags(kb, origin):
    src = datasets.describe(kb, str(origin))
    a = assets.new(kb, "fileset", {"title": "TPC noise talk", "summary": "Slides and notes."})
    assets.bind(kb, a.id, "dataset", "datalad", {"source": src.id, "path": "talks/noise"}, versioning="native")
    seen = assets.scan(Store.open(kb.root), a.id)
    assert [(s.offer.native, s.status) for s in seen] == [("v1", "new"), ("v2", "new")]
    assets.register(Store.open(kb.root), a.id, seen, keep="no")
    res = assets.resolve(Store.open(kb.root), a.id, "v1")
    assert res.files["slides.txt"].read_text() == "TPC noise, version one\n"
    assert set(res.files) == {"slides.txt", "notes.txt"}
    kb = Store.open(kb.root)
    from kb.lint import dangling
    assert dangling(kb) == []
    assert src.id in [r[0] for r in kb.conn.execute("SELECT dst FROM refs WHERE src = ? AND rel = 'source'", (a.id,))]


def test_types_and_missing_support(kb, origin, monkeypatch):
    rows = {(r["interface"], r["type"]): r for r in __import__("kb.locations", fromlist=["x"]).sources_table()
            + assets.sources_table()}
    assert rows[("location", "datalad")]["status"] == "implemented" and rows[("asset", "datalad")]["available"]
    monkeypatch.setattr(datasets, "available", lambda: "the DataLad Python package is not installed")
    rec = _doc(kb, origin / "talks" / "noise" / "slides.txt")       # auto: placed as before
    assert rec.rendition("original")["locations"][0]["type"] == "db"
    assert not assets.sources_table()[3]["available"]


def test_url_downloads_go_to_the_url_source_directory(kb, tmp_path):
    f = tmp_path / "paper.txt"
    f.write_text("a paper\n")
    rec = authoring.add(kb, "document", [f.as_uri()], {"title": "P", "summary": "S", "doctype": "paper",
                                                       "date": "2026", "creators": ["A"]})
    sha = rec.rendition("original")["sha256"]
    assert (source_dir(kb.cfg.raw, "url") / "sha256" / sha[:2] / sha).is_file()
    assert str(source_dir(kb.cfg.raw, "url")).endswith("wckb/url")
    shutil.rmtree(source_dir(kb.cfg.raw, "url"))


def test_doctor_knows_datalad(kb, origin, tmp_path, monkeypatch):
    from kb import doctor
    rec = _doc(kb, origin / "talks" / "noise" / "slides.txt")
    src = rec.rendition("original")["locations"][0]["source"]
    kb = Store.open(kb.root)
    assert doctor.diagnose(kb, ["datalad-support", "gitattributes", "clones"]) == []
    with monkeypatch.context() as m:
        m.setattr(datasets, "available", lambda: "the DataLad Python package is not installed")
        assert [f.code for f in doctor.diagnose(kb, ["datalad-support"])] == ["datalad-support"]
    ignore = Path(os.environ["XDG_CONFIG_HOME"]) / "git" / "ignore"
    ignore.parent.mkdir(parents=True, exist_ok=True)
    ignore.write_text(".gitattributes\n")
    assert [f.code for f in doctor.diagnose(kb, ["gitattributes"])] == ["gitattributes"]
    ignore.unlink()
    datasets.add_places(kb, kb.get(src), [origin.as_uri()], [])
    monkeypatch.setenv("KB_CONFIG", str(tmp_path / "fresh.toml"))           # a machine without the clone
    kb = Store.open(kb.root)
    kb.set_setting("host", "elsewhere")
    kb = Store.open(kb.root)
    found = doctor.diagnose(kb, ["clones"])
    assert [(f.mode, f.where) for f in found] == [("ask", src)]
    assert doctor.treat(kb, found, lambda f: True)[0]["result"] == "fixed"
    assert doctor.diagnose(Store.open(kb.root), ["clones"]) == []


def test_guessing_the_type(origin, tmp_path):
    from kb import sources
    plain = tmp_path / "plain"
    plain.mkdir()
    subprocess.run(["git", "-C", str(plain), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(plain), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                    "--allow-empty", "-m", "x"], check=True)
    assert sources.guess([str(origin)]).type == "datalad"
    assert sources.guess([origin.as_uri()]).type == "datalad"                  # a git-annex branch over there
    assert sources.guess([str(origin), origin.as_uri()]).type == "datalad"
    g = sources.guess([str(plain)])
    assert g.type is None and "a git repository, not a DataLad dataset" in g.explain()
    assert "without git-annex" in sources.guess([plain.as_uri()]).explain()
    assert "inside the DataLad dataset" in sources.guess([str(origin / "talks")]).explain()
    assert "not a URL" in sources.guess(["no/such/thing"]).explain()


def test_a_clone_and_its_url_together(kb, origin, tmp_path):
    from kb import sources
    import datalad.api as dl
    mine = tmp_path / "mine"
    dl.clone(source=str(origin), path=str(mine), result_renderer="disabled")
    rec = sources.add(kb, [str(mine), origin.as_uri()])
    assert rec.data["source_type"] == "datalad" and origin.as_uri() in rec.data["urls"]
    assert rec.data["paths"] == [{"host": kb.cfg.host, "path": str(mine.resolve())}]
    assert not (source_dir(kb.cfg.raw, "datalad")).exists()                   # nothing was cloned
    other = tmp_path / "other"
    dl.create(str(other), result_renderer="disabled")
    with pytest.raises(KBError, match="does not share history"):
        sources.add(kb, [str(mine), other.as_uri()])
    with pytest.raises(KBError, match="different datasets"):
        sources.add(kb, [str(mine), str(other)])
    with pytest.raises(KBError, match="not a DataLad dataset"):
        sources.add(kb, [str(tmp_path)], "datalad")


def test_cli_source_add(kb, origin, tmp_path):
    from click.testing import CliRunner
    from kb.cli import main
    run = lambda *a: CliRunner().invoke(main, ["-C", str(kb.root), *a])  # noqa: E731
    r = run("source", "add", "--just-guess", str(origin))
    assert r.exit_code == 0 and r.output.splitlines()[0] == "datalad"
    r = run("source", "add", "--just-guess", str(tmp_path))
    assert r.exit_code == 1 and "cannot guess" in r.output and "not a DataLad dataset" in r.output
    assert run("source", "add", "--just-guess", "--type", "nonsense", str(origin)).exit_code != 0
    assert Store.open(kb.root).items == {}                                     # guessing adds nothing
    r = run("source", "add", str(origin), "--title", "Origin")
    assert r.exit_code == 0 and r.output.startswith("src-")
    assert "Origin" in run("source", "list").output
