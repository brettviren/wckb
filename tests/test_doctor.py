import json

from click.testing import CliRunner

from kb import doctor, federation, policy
from kb.cli import main
from kb.items import Store
from kb.status import status

from conftest import git, make_store, observation


def codes(kb, **kw):
    return sorted({f.code for f in doctor.diagnose(Store.open(kb.root), **kw)})


def yes(f):
    return True


def no(f):
    return False


def test_a_fresh_store_is_healthy(kb):
    observation(kb)
    assert doctor.diagnose(Store.open(kb.root)) == []


def test_query_tables_are_rebuilt(kb):
    observation(kb)
    kb.conn.execute("DELETE FROM fts")
    found = doctor.diagnose(kb, ["query-tables"])
    assert [f.mode for f in found] == ["auto"]
    assert doctor.treat(kb, found, no)[0]["result"] == "fixed"          # auto fixes need no confirmation
    assert codes(kb) == []


def test_store_in_a_git_working_tree_gets_ignored(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    git(proj, "init", "-q")
    kb = make_store(proj / "kb", "project")
    (kb.root / ".gitignore").unlink()
    found = doctor.diagnose(kb, ["gitignore"])
    assert [f.mode for f in found] == ["auto"]
    doctor.treat(kb, found, no)
    assert codes(kb, codes=["gitignore"]) == []


def test_policy_enacted_when_safe_but_hand_edits_are_left_to_people(tmp_path):
    kb = make_store(tmp_path / "s", "home", target=str(tmp_path / "s"))
    policy.use(kb, ["kb-view"])
    found = doctor.diagnose(Store.open(kb.root), ["policy"])
    assert found and all(f.mode == "auto" for f in found)
    doctor.treat(Store.open(kb.root), found, no)
    assert codes(kb, codes=["policy"]) == []
    md = tmp_path / "s" / "CLAUDE.md"
    md.write_text(md.read_text().replace("append-only", "append-only (really)"))
    found = doctor.diagnose(Store.open(kb.root), ["policy"])
    assert found and all(f.mode == "manual" for f in found) and "capture" in found[0].fix


def test_snapshot_neighbor_is_fetched(tmp_path):
    g = make_store(tmp_path / "g", "group")
    (tmp_path / "pub").mkdir()
    snap = g.snapshot(tmp_path / "pub" / "g.sqlite")
    kb = make_store(tmp_path / "h", "home")
    federation.add(kb, "g", "up", url=snap.as_uri())
    kb = Store.open(kb.root)
    found = doctor.diagnose(kb, ["edges"])
    assert [(f.mode, f.where) for f in found] == [("auto", "g")]
    assert doctor.treat(kb, found, no)[0]["result"] == "fixed"
    assert codes(kb, codes=["edges"]) == []


def test_missing_neighbor_and_content_store_are_advice(kb, tmp_path):
    import shutil
    from kb import authoring
    gone = make_store(tmp_path / "gone", "group")
    federation.add(kb, "gone", "up", path=str(gone.root))
    shutil.rmtree(gone.root)
    found = doctor.diagnose(Store.open(kb.root), ["edges"])
    assert [f.mode for f in found] == ["manual"] and "no store at" in found[0].summary

    local = kb.root / ".kb" / "local.toml"
    local.write_text(local.read_text() + f'[cas.default]\npath = "{tmp_path / "cas"}"\n')
    kb = Store.open(kb.root)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (kb.cfg.max_inline_bytes + 1))
    authoring.add(kb, "dataset", [str(big)], {"title": "Big", "summary": "A big file."})
    assert doctor.diagnose(Store.open(kb.root), ["cas"]) == []
    local.write_text(local.read_text().replace(str(tmp_path / "cas"), str(tmp_path / "unmounted")))
    found = doctor.diagnose(Store.open(kb.root), ["cas"])
    assert [f.mode for f in found] == ["manual"] and "not there" in found[0].summary


def test_token_file_permissions_ask_first(kb, tmp_path, monkeypatch):
    tok = tmp_path / "token"
    tok.write_text("secret\n")
    tok.chmod(0o644)
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[forges.gh]\ntype = "github"\nhost = "github.com"\ntoken = {{file = "{tok}"}}\n')
    monkeypatch.setenv("KB_CONFIG", str(cfg))
    kb = Store.open(kb.root)
    found = doctor.diagnose(kb, ["secret-perms"])
    assert [f.mode for f in found] == ["ask"] and "secret" not in found[0].summary
    assert doctor.treat(kb, found, no)[0]["result"] == "skipped"
    assert doctor.treat(kb, found, yes)[0]["result"] == "fixed"
    assert tok.stat().st_mode & 0o077 == 0


def test_agents_follow_autonomy(kb, monkeypatch):
    observation(kb)
    kb.conn.execute("DELETE FROM fts")
    monkeypatch.setenv("KB_AGENT", "1")
    kb.set_setting("autonomy", {**kb.setting("autonomy", {}), "curate": "never"})
    kb = Store.open(kb.root)
    r = doctor.treat(kb, doctor.diagnose(kb, ["query-tables"]), yes)
    assert r[0]["result"] == "skipped" and "reserved for the human" in r[0]["detail"]


def test_dangling_references_are_fetched_after_asking(tmp_path):
    g = make_store(tmp_path / "g", "group")
    o = observation(g, title="Group calibration")
    kb = make_store(tmp_path / "h", "home", up=g.root)
    observation(kb, related=[o.id])
    found = doctor.diagnose(Store.open(kb.root), ["dangling"])
    assert [f.mode for f in found] == ["ask"]
    assert doctor.diagnose(Store.open(kb.root), quick=True) == []         # a full diagnosis only
    r = doctor.treat(Store.open(kb.root), found, yes)
    assert r[0]["result"] == "fixed" and o.id in Store.open(kb.root).items


def test_a_broken_diagnosis_is_reported_not_raised(kb, monkeypatch):
    def boom(kb):
        raise RuntimeError("bad")
    monkeypatch.setitem(doctor.REMEDIES, "boom", doctor.Remedy("boom", "Broken", boom))
    found = doctor.diagnose(kb)
    assert [f.code for f in found] == ["boom"] and "bad" in found[0].summary


def test_status(kb, tmp_path):
    observation(kb)
    v = status(Store.open(kb.root))
    assert v["items"]["by_kind"] == {"observation": 1} and v["inbox"]["pending"] == 0
    assert v["attention"] == [] and v["last_change"]["action"] == "create"


def test_cli_status_and_doctor(kb):
    run = lambda *a: CliRunner().invoke(main, ["-C", str(kb.root), *a])  # noqa: E731
    observation(kb)
    assert "Nothing needs attention" in run("status").output
    kb.conn.execute("DELETE FROM fts")
    r = run("status")
    assert "query-tables" in r.output and "kb doctor --fix" in r.output
    r = run("doctor")
    assert r.exit_code == 1 and "search covers 0 of 1" in r.output
    r = run("doctor", "--fix")
    assert r.exit_code == 0 and "fixed" in r.output
    assert run("doctor").exit_code == 0
    assert "query-tables" in run("doctor", "--list").output
    assert json.loads(run("status", "--json").output)["attention"] == []
    assert "no remedy" in run("doctor", "nonsense").output


def test_user_is_set_up_in_two_steps(kb, tmp_path, monkeypatch):
    from kb import authoring
    cfg = tmp_path / "config.toml"
    cfg.write_text('[forges.gh]\ntype = "github"\nhost = "github.com"\n')
    monkeypatch.setenv("KB_CONFIG", str(cfg))
    monkeypatch.delenv("KB_USER")
    monkeypatch.setattr("getpass.getuser", lambda: "bv")
    kb = Store.open(kb.root)
    f = doctor.diagnose(kb, ["user"])
    assert [x.mode for x in f] == ["manual"] and "--slug bv" in f[0].fix
    authoring.new(kb, "person", {"title": "Brett", "summary": "Brett.", "name": "Brett Viren"}, slug="bv")
    f = doctor.diagnose(Store.open(kb.root), ["user"])
    assert [(x.mode, x.where) for x in f] == [("ask", "per-bv")]
    assert doctor.treat(Store.open(kb.root), f, yes)[0]["result"] == "fixed"
    import tomllib
    conf = tomllib.loads(cfg.read_text())
    assert conf["user"] == "per-bv" and conf["forges"]["gh"]["host"] == "github.com"
    assert doctor.diagnose(Store.open(kb.root), ["user"]) == []
