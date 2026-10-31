import copy
import shutil

import pytest

from kb import authoring, bundle, federated, federation, push
from kb.edit import set_fields
from kb.errors import KBError
from kb.records import KB, Record
from kb.transfer import expand

from conftest import git, make_store, observation


@pytest.fixture
def world(tmp_path):
    """A project store in ./kb of a code repo, a home store, and a group store above home."""
    group = make_store(tmp_path / "group", "group", "group-x")
    home = make_store(tmp_path / "home" / "kb", "home", "home")
    proj_dir = tmp_path / "work" / "proj"
    proj_dir.mkdir(parents=True)
    git(proj_dir, "init", "-q", "-b", "main")
    project = make_store(proj_dir / "kb", "project", up=home.root)
    federation.add(home, "group-x", "up", path=str(group.root))
    return {"project": KB.open(project.root), "home": KB.open(home.root), "group": KB.open(group.root),
            "proj_dir": proj_dir}


def test_project_store_layout(world):
    p, pd = world["project"], world["proj_dir"]
    assert p.cfg.scope == "project" and p.cfg.access == "private" and p.cfg.name == "proj"
    assert (pd / "CLAUDE.md").exists() and (pd / ".claude" / "skills" / "kb-promote" / "SKILL.md").exists()
    assert (p.root / ".gitignore").read_text().splitlines()[-1] == "*"     # the code repo doesn't version the store
    from kb.config import find_root
    (pd / "src").mkdir()
    assert find_root(pd) == p.root and find_root(pd / "src") == p.root


def test_neighborhood_and_edge_defaults(world):
    p = world["project"]
    rows = {r["label"]: r for r in federation.describe(p, depth=3)}
    assert rows["home"]["relation"] == "up" and rows["home"]["scope"] == "home"
    assert rows["home>group-x"]["distance"] == 2 and rows["home>group-x"]["scope"] == "group"
    nb = federation.neighbor(p, "home")
    assert (nb.push, nb.pull, nb.mode) == ("select", "all", "apply")
    federation.add(world["group"], "home", "down", path=str(world["home"].root))
    d = federation.neighbor(KB.open(world["group"].root), "home")
    assert (d.push, d.pull) == ("all", "select")
    federation.add(world["home"], "lab", "peer", path=str(world["group"].root), pull="none", prefer="theirs")
    pe = federation.neighbor(KB.open(world["home"].root), "lab")
    assert (pe.push, pe.pull, pe.accept, pe.prefer) == ("select", "none", "review", "theirs")


def test_cycles_are_harmless(world):
    federation.add(world["group"], "home", "down", path=str(world["home"].root))
    federation.add(world["group"], "proj", "down", path=str(world["project"].root))
    labels = [r.label for r in federation.reachable(world["project"], 5, relations=("up", "peer", "down"))]
    assert sorted(labels) == ["home", "home>group-x"]


def test_federated_reads_are_read_only(world):
    g = world["group"]
    observation(g, title="Group-wide grounding scheme", text="All crates share one ground.")
    observation(world["project"], title="Local grounding test", text="Grounding test passed.")
    before = (g.root / "kb.sqlite").stat().st_mtime_ns
    hits = federated.search(world["project"], "grounding", reach=2)
    assert {h["store"] for h in hits} == {"here", "home>group-x"}
    ctx = federated.context(world["project"], "detector/tpc", reach=2)
    assert "# From home>group-x" in ctx and "read-only here" in ctx
    assert (g.root / "kb.sqlite").stat().st_mtime_ns == before
    reader = federation.open_store(g.root)
    with pytest.raises(KBError, match="read-only"):
        reader.set_setting("lint", {})


def test_snapshot_neighbor(world):
    g = world["group"]
    observation(g, title="In the group")
    snap = g.snapshot(g.root.parent / "published-group.sqlite")     # a consistent single file, as published
    federation.add(world["home"], "upstream", "peer", url=snap.as_uri(), shared=True)
    home = KB.open(world["home"].root)
    assert "upstream" in home.setting("neighbors")
    assert not federation.neighbor(home, "upstream").available
    assert federation.fetch(home) == [("upstream", "fetched")]
    nb = federation.neighbor(home, "upstream")
    assert nb.available and not nb.writable
    hits = federated.search(home, "group", only=["upstream"])
    assert any(h["store"] == "upstream" for h in hits)


# -- knowledge sets -------------------------------------------------------------------

def _r(i, kind="observation", **kw):
    return {"id": i, "kind": kind, **kw}


def test_expand_shallow_deep_nested():
    db = {r["id"]: r for r in (_r("obs-aaa", related=["obs-bbb"]), _r("obs-bbb", evidence=["obs-ccc", "https://x"]),
                               _r("obs-ccc"), _r("ks-deepnest", "kset", members=["obs-aaa"], depth="deep"))}
    look = db.get
    s = {"id": "ks-root", "kind": "kset", "members": ["obs-aaa"], "depth": "shallow"}
    assert expand(s, look).items == ["ks-root", "obs-aaa"]
    s["depth"] = "deep"
    assert expand(s, look).items == ["ks-root", "obs-aaa", "obs-bbb", "obs-ccc"]
    s = {"id": "ks-root", "kind": "kset", "members": ["ks-deepnest"], "depth": "shallow", "nested": "follow"}
    assert expand(s, look).items == ["ks-root", "ks-deepnest", "obs-aaa", "obs-bbb", "obs-ccc"]
    s["nested"] = "inherit"
    assert expand(s, look).items == ["ks-root", "ks-deepnest", "obs-aaa"]
    s = {"id": "ks-root", "kind": "kset", "members": ["obs-aaa"], "depth": "deep", "follow": ["related"]}
    assert expand(s, look).items == ["ks-root", "obs-aaa", "obs-bbb"]
    e = expand({"id": "ks-root", "kind": "kset", "members": ["obs-zzz", "obs-bbb"], "depth": "deep"}, look)
    assert e.missing_members == [("ks-root", "obs-zzz")] and e.items == ["ks-root", "obs-bbb", "obs-ccc"]


# -- push --------------------------------------------------------------------------

def test_candidates_and_push(world):
    p, home = world["project"], world["home"]
    a = observation(p, title="Drift velocity measured", summary="Measured 1.58 mm/us in run 1250.",
                    status="verified", evidence=["https://elog.example/1"], tags=["promote"])
    b = observation(p, title="Scratch note", summary="A scratch note about nothing much.")
    c = observation(p, title="Personal reminder", summary="Do not share this one.", tags=["no-promote"])
    ids = [x.id for x in push.candidates(p, "home")]
    assert ids[0] == a.id and b.id in ids and c.id not in ids

    r = push.push(p, "home", [a.id])
    assert r["items"]["new"] == [r["set"], a.id] and r["approved"] == "human"
    home = KB.open(home.root)
    copy_ = home.get(a.id)
    o = copy_.data["origin"]
    assert o["store"] == p.cfg.id and o["path"] == [p.cfg.id] and o["set"] == r["set"]
    assert copy_.digest() == p.get(a.id).digest() and copy_.text("note") == p.get(a.id).text("note")
    assert home.get(r["set"]).data["members"] == [a.id]
    assert [e["action"] for e in home.events(a.id)] == ["transfer-in"]
    assert a.id not in [x.id for x in push.candidates(KB.open(p.root), "home")]
    hits = federated.search(KB.open(p.root), "drift", reach=1)
    assert len(hits) == 1 and hits[0]["also_in"] == ["home"]

    fix = authoring.supersede(KB.open(p.root), a.id, {}, text="Measured 1.60 mm/us; unit slip before.")
    top = push.candidates(KB.open(p.root), "home")[0]
    assert top.id == fix.id and any("corrects" in r for r in top.reasons)


def test_shallow_and_deep_push(world):
    from kb.checks import check
    p = world["project"]
    o = observation(p)
    t = authoring.new(p, "topic", {"title": "T", "summary": "Topic.", "subject": ["detector/tpc/noise"],
                                   "derived_from": [o.id]}, text=f"See [{o.id}].")
    r = push.push(p, "home", [t.id])
    assert set(r["items"]["new"]) == {r["set"], t.id} and r["dangling"] == [o.id]
    h = KB.open(world["home"].root)
    assert [x.code for x in check(h)] == ["dangling"]                      # a warning, not an error
    r = push.push(KB.open(p.root), "home", [t.id], deep=True)
    assert o.id in r["items"]["new"] and t.id in r["items"]["same"]


def test_access_relabel_is_deliberate(world):
    home = world["home"]
    o = observation(home, title="Group-worthy fact", summary="Something the group should know.")
    with pytest.raises(KBError, match="relabel deliberately"):
        push.push(home, "group-x", [o.id])
    push.push(KB.open(home.root), "group-x", [o.id], access="group")
    assert KB.open(world["group"].root).get(o.id).data["access"] == "group"


def test_push_into_inbox_then_review(world):
    home, g = world["home"], world["group"]
    o = observation(home, access="group", title="For the group")
    r = push.push(home, "group-x", [o.id], mode="inbox", message="Please review")
    assert r["land"] == "inbox" and r["status"] == "pending"
    gk = KB.open(g.root)
    assert o.id not in gk.records
    [e] = bundle.entries(gk)
    bundle.review(gk, e.bundle, "accept", "Well attributed, adds a run range we lacked.", kind="llm")
    bundle.review(gk, e.bundle, "comment", "Fine by me.")
    assert [rv["reviewer_kind"] for rv in bundle.get(gk, e.bundle).reviews] == ["llm", "human"]
    rep = bundle.accept(gk, e.bundle, note="ok")
    assert o.id in rep["items"]["new"] and o.id in KB.open(g.root).records
    assert bundle.get(gk, e.bundle).status == "accepted"
    with pytest.raises(KBError, match="already accepted"):
        bundle.accept(gk, e.bundle)


def test_repush_and_multipath(world):
    p, home, g = world["project"], world["home"], world["group"]
    o = observation(p, access="group")
    push.push(p, "home", [o.id])
    push.push(KB.open(home.root), "group-x", [o.id])                  # forwarded: project -> home -> group
    assert KB.open(g.root).get(o.id).data["origin"]["path"] == [p.cfg.id, home.cfg.id]

    set_fields(KB.open(p.root), o.id, ["status=disputed", "summary=Now disputed by a second measurement."])
    p = KB.open(p.root)
    assert {x.id: x for x in push.candidates(p, "home")}[o.id].state == "changed"
    push.push(p, "home", [o.id])                                       # same path: the update lands
    assert KB.open(home.root).get(o.id).data["status"] == "disputed"

    federation.add(p, "group-x", "up", path=str(g.root))               # a second path, directly
    set_fields(KB.open(p.root), o.id, ['tags=["cross-check"]'])
    r = push.push(KB.open(p.root), "group-x", [o.id])
    gk = KB.open(g.root)
    assert gk.get(o.id).data["status"] == "unverified" and "cross-check" in gk.get(o.id).data["tags"]
    assert any("kept our status" in n for n in r["notes"][o.id])


def test_conflicting_core_refuses_whole_transfer(world):
    p, home = world["project"], world["home"]
    o = observation(p)
    other = observation(p, title="Innocent")
    push.push(p, "home", [o.id])
    hk = KB.open(home.root)
    hk.conn.execute("DROP TRIGGER items_frozen")                        # someone bypassed kb in home
    data = copy.deepcopy(hk.get(o.id).data)
    data["valid"] = {"runs": [[1, 2]]}
    hk.save(Record(data, hk))
    with pytest.raises(KBError, match="conflicts"):
        push.push(KB.open(p.root), "home", [o.id, other.id])
    assert other.id not in KB.open(home.root).records                   # atomic: nothing landed


def test_agent_autonomy(world, monkeypatch):
    p = world["project"]
    o = observation(p)
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(KBError, match="approval"):
        push.push(KB.open(p.root), "home", [o.id])
    r = push.push(KB.open(p.root), "home", [o.id], approved="yes, push the drift one")
    assert r["approved"] == "human: yes, push the drift one"
    assert KB.open(world["home"].root).events(o.id)[-1]["approved"] == r["approved"]
    from kb import policy
    with pytest.raises(KBError, match="granted by the human"):
        policy.set_autonomy(KB.open(p.root), ["push.home=auto"])


# -- pull, obtain ---------------------------------------------------------------------

def test_pull_all_from_up(world):
    home, p = world["home"], world["project"]
    a = observation(home, title="Home fact one")
    b = observation(home, title="Home fact two")
    r = push.pull(p, "home", [])                       # the up edge's default pull is "all"
    assert set(r["items"]["new"]) >= {a.id, b.id}
    pk = KB.open(p.root)
    assert pk.get(a.id).data["origin"]["store"] == home.cfg.id
    assert pk.get(r["set"]).data["purpose"] == "transfer"
    assert push.pull(pk, "home", [])["result"] == "nothing new or changed"


def test_pull_from_peer_goes_to_inbox(world):
    home, g = world["home"], world["group"]
    federation.add(home, "lab", "peer", path=str(g.root))        # peers: accept = review
    o = observation(g)
    r = push.pull(KB.open(home.root), "lab", [o.id])
    assert r["land"] == "inbox" and o.id not in KB.open(home.root).records
    hk = KB.open(home.root)
    bundle.accept(hk, r["bundle"])
    assert o.id in KB.open(home.root).records


def test_obtain_dangling(world):
    g, p = world["group"], world["project"]
    o = observation(g, title="Group calibration constant")
    mine = observation(p, related=[o.id])                         # resolvable in a reachable store
    with pytest.raises(KBError, match="any reachable store"):
        observation(KB.open(p.root), related=["obs-nowherexyz"])
    reps = push.obtain(KB.open(p.root), dangling=True)
    assert reps[0]["via"] == "home>group-x" and o.id in reps[0]["items"]["new"]
    assert o.id in KB.open(p.root).records and mine.id in KB.open(p.root).records


# -- twins --------------------------------------------------------------------------

def test_twin_sync(tmp_path):
    from kb.sync import sync
    a = make_store(tmp_path / "laptop", "home", "me")
    shared = observation(a, title="Before the copy")
    a.close()
    shutil.copytree(tmp_path / "laptop", tmp_path / "desktop", ignore=shutil.ignore_patterns(".kb"))
    a, b = KB.open(tmp_path / "laptop"), KB.open(tmp_path / "desktop")
    assert a.cfg.id == b.cfg.id
    federation.add(a, "desk", "twin", path=str(b.root))
    oa = observation(KB.open(a.root), title="On the laptop")
    ob = observation(KB.open(b.root), title="On the desktop")
    set_fields(KB.open(a.root), shared.id, ["summary=Edited on the laptop first."])
    set_fields(KB.open(b.root), shared.id, ["summary=Edited on the desktop later."])
    rep = sync(KB.open(a.root))[0]
    assert ob.id in rep["pulled"]["new"] and oa.id in rep["pushed"]["new"]
    for k in (KB.open(a.root), KB.open(b.root)):
        assert {oa.id, ob.id} <= set(k.records)
        assert k.get(shared.id).data["summary"] == "Edited on the desktop later."   # the later change wins
        assert "origin" not in k.get(oa.id).data and "origin" not in k.get(ob.id).data
    with pytest.raises(KBError, match="different store"):
        federation.add(a, "nope", "twin", path=str(make_store(tmp_path / "other", "home").root))
