"""Source items in general, and filesystem sources (no DataLad needed)."""

import pytest

from kb import assets, bundle
from kb.errors import KBError
from kb.items import Store
from kb.push import make_kset


def test_plain_directories_need_type_filesystem(kb, tmp_path):
    from kb import sources
    tree = tmp_path / "talks"
    (tree / "v1").mkdir(parents=True)
    (tree / "v1" / "slides.txt").write_text("one\n")
    g = sources.guess([str(tree)])
    assert g.type is None and "requires --type filesystem" in g.explain()
    with pytest.raises(KBError, match="--type"):
        sources.add(kb, [str(tree)])
    rec = sources.add(kb, [str(tree)], "filesystem", name="group-talks")
    assert (rec.data["source_type"], rec.data["source_id"]) == ("filesystem", "group-talks")
    with pytest.raises(KBError, match="names are lower case"):
        sources.add(kb, [str(tree)], "filesystem", name="Bad Name")
    # an fs binding by that name finds the tree through the source item, and carries it
    a = assets.new(kb, "fileset", {"title": "Talk", "summary": "Talk."})
    assets.bind(Store.open(kb.root), a.id, "copies", "fs", {"root": "group-talks"}, versioning="pattern",
                pattern="{version}")
    kb = Store.open(kb.root)
    assert kb.get(a.id).data["bindings"][0]["locator"]["source"] == rec.id
    seen = assets.scan(kb, a.id)
    assert [(s.offer.native, s.status) for s in seen] == [("v1", "new")]
    ks = make_kset(kb, [a.id], "shallow")
    assert rec.id in {i["data"]["id"] for i in bundle.make(Store.open(kb.root), ks.id)["items"]}
    from kb.sources import report
    assert report(Store.open(kb.root), rec)["here"] == str(tree.resolve())


def _git_repo(path):
    import subprocess
    g = lambda *a: subprocess.run(["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@x", *a],  # noqa: E731
                                  check=True, capture_output=True)
    path.mkdir(parents=True)
    g("init", "-q")
    (path / "cfg").mkdir()
    (path / "cfg" / "drift.txt").write_text("drift 1.6\n")
    g("add", ".")
    g("commit", "-q", "-m", "v1")
    g("tag", "v1")
    (path / "cfg" / "drift.txt").write_text("drift 1.62\n")
    g("commit", "-qam", "v2")
    g("tag", "v2")
    return path


def test_git_sources(kb, tmp_path, monkeypatch):
    from kb import datasets, sources
    repo = _git_repo(tmp_path / "calib")
    assert sources.guess([str(repo)]).type == "git"
    assert sources.guess([str(repo), repo.as_uri()]).type == "git"
    assert "inside the git repository" in sources.guess([str(repo / "cfg")]).explain()
    rec = sources.add(kb, [str(repo), repo.as_uri()])
    assert rec.data["source_type"] == "git" and rec.data["source_id"].startswith("git:")
    assert rec.data["urls"] == [repo.as_uri()]
    a = assets.new(kb, "file", {"title": "Drift constant", "summary": "Drift velocity file."})
    assets.bind(Store.open(kb.root), a.id, "repo", "git", {"source": rec.id, "path": "cfg/drift.txt"},
                versioning="native")
    seen = assets.scan(Store.open(kb.root), a.id)
    assert [(s.offer.native, s.status) for s in seen] == [("v1", "new"), ("v2", "new")]
    assets.register(Store.open(kb.root), a.id, seen, keep="no")
    assert assets.resolve(Store.open(kb.root), a.id, "v1").files["drift.txt"].read_text() == "drift 1.6\n"

    # another machine: no registry, another host, so it clones (with git; no DataLad needed)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))
    kb.set_setting("host", "elsewhere")
    kb.set_setting("sources", {"git": {"dir": str(tmp_path / "clones")}})
    monkeypatch.setattr(datasets, "available", lambda: "the DataLad Python package is not installed")
    kb = Store.open(kb.root)
    assert datasets.locate(kb, kb.get(rec.id)) is None
    assert assets.resolve(kb, a.id, "v2").files["drift.txt"].read_text() == "drift 1.62\n"
    assert datasets.locate(kb, kb.get(rec.id)).parent == tmp_path / "clones"
