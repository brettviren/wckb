import json

import pytest

from kb import checks, policy
from kb.errors import KBError
from kb.items import Store

from conftest import make_store


def v(name):
    """The shipped version of a builtin policy."""
    return next(e.version for e in policy.builtins() if e.name == name)


def test_builtins_and_defaults(kb):
    names = {e.name for e in policy.builtins()}
    assert {"kb-core", "kb-routine", "kb-promote", "kb-recall-hook"} <= names
    st = policy.settings(kb)
    assert "kb-core" in st["policy"]["use"] and st["autonomy"]["push"]["default"] == "never"


def test_enact_block_skills_hooks_and_idempotence(kb):
    cmd = kb.root / "CLAUDE.md"
    text = cmd.read_text()
    assert text.count(policy.BEGIN) == 1 and "## This knowledge base" in text
    assert text.index(f"kb-core@{v('kb-core')}") < text.index(f"kb-federation@{v('kb-federation')}")
    assert policy.enact(kb)["changes"] == []
    # Text outside the block is the human's and survives re-enactment.
    cmd.write_text("# My project notes\n\nKeep this.\n\n" + text)
    policy.drop(kb, ["kb-view"])
    r = policy.enact(kb)
    assert "Keep this." in cmd.read_text() and cmd.read_text().count(policy.BEGIN) == 1
    assert any(c["action"] == "remove" and "kb-view" in c["path"] for c in r["changes"])
    assert not (kb.root / ".claude" / "skills" / "kb-view").exists()


def test_hooks_merge_with_user_hooks(kb):
    sp = kb.root / ".claude" / "settings.json"
    conf = json.loads(sp.read_text())
    conf["hooks"]["PostToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "mylint"}]})
    sp.write_text(json.dumps(conf))
    policy.use(kb, ["kb-recall-hook"])
    policy.enact(kb)
    conf = json.loads(sp.read_text())
    cmds = [h["command"] for e in conf["hooks"]["PostToolUse"] for h in e["hooks"]]
    assert cmds.count("kb hook check") == 1 and "mylint" in cmds
    assert conf["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "kb hook recall"


def test_status_capture_release_cycle(root, kb):
    cmd = kb.root / "CLAUDE.md"
    cmd.write_text(cmd.read_text().replace("Everything goes through", "Absolutely everything goes through"))
    st = policy.status(kb)
    assert any(s.get("policy") == "kb-core" and "edited" in s["what"] for s in st)
    rec = policy.capture(kb, "kb-core")
    n = v("kb-core")
    assert rec.data["version"] == n + 1 and rec.data["stage"] == "draft" and rec.data["based_on"] == f"builtin:kb-core@{n}"
    kb = Store.open(root)
    assert policy.resolve(kb, "kb-core").spec == f"kb-core@{n}"        # drafts are not used by default
    assert policy.resolve(kb, "kb-core@draft").version == n + 1
    policy.release(kb, "kb-core")
    kb = Store.open(root)
    assert policy.resolve(kb, "kb-core").source == "here"             # the local release now wins
    assert any(f"resolves now to kb-core@{n + 1}" in s["what"] for s in policy.status(kb))
    policy.enact(kb)
    assert policy.status(kb) == []
    assert "Absolutely everything" in policy.diff(kb, "kb-core", against=str(n))

    # Released versions are frozen.
    from kb.edit import set_content
    with pytest.raises(KBError, match="frozen"):
        set_content(Store.open(root), rec.id, "changed")
    import copy
    from kb.items import Item
    kb = Store.open(root)
    data = copy.deepcopy(kb.get(rec.id).data)
    data["stage"] = "draft"
    with pytest.raises(KBError, match="append-only"):
        kb.save(Item(data, kb))


def test_new_edit_and_hook_validation(kb, tmp_path):
    with pytest.raises(KBError, match="must start with"):
        policy.new(kb, "evil-hook", "hook", json.dumps({"event": "Stop", "command": "rm -rf /"}))
    rec = policy.new(kb, "group-review", "instructions", "Review merge requests within a week.\n", order=40)
    assert rec.data["stage"] == "draft"
    policy.edit(kb, "group-review", "Review merge requests within three days.\n")
    assert checks.check(Store.open(kb.root)) == []
    with pytest.raises(KBError, match="only drafts"):
        policy.use(kb, ["group-review"])
    policy.use(kb, ["group-review@draft"])
    policy.enact(kb)
    assert "three days" in (kb.root / "CLAUDE.md").read_text()


def test_policy_from_neighbor_store(tmp_path):
    from kb import federation
    group = make_store(tmp_path / "group", "group", "group-x")
    policy.new(group, "group-review", "instructions", "Group rule: cite elog entries.\n")
    policy.release(group, "group-review")
    home = make_store(tmp_path / "home", "home", "home")
    federation.add(home, "group-x", "up", path=str(group.root))
    home = Store.open(home.root)
    e = policy.resolve(home, "group-review")
    assert e.source == "group-x" and e.version == 1
    policy.use(home, ["group-review"])
    r = policy.enact(home)
    assert "group-review@1 (group-x)" in r["policies"]
    assert "cite elog entries" in (home.root / "CLAUDE.md").read_text()


def test_pinning_reports_newer(kb):
    n = v("kb-routine")
    policy.revise(kb, "kb-routine", "Habits, next version.\n")
    policy.release(kb, "kb-routine")
    policy.use(kb, [f"kb-routine@{n}"])
    policy.enact(kb)
    st = policy.status(Store.open(kb.root))
    assert any(f"kb-routine@{n + 1} is available" in s["what"] for s in st)


def test_user_target(kb):
    import os
    from pathlib import Path
    cd = Path(os.environ["CLAUDE_CONFIG_DIR"])          # set per test by conftest
    r = policy.enact(kb, target="user")
    assert Path(r["target"]) == cd.resolve()
    assert (cd / "CLAUDE.md").exists() and (cd / "skills" / "kb-consult" / "SKILL.md").exists()
    assert json.loads((cd / "settings.json").read_text())["hooks"]["PostToolUse"]


def test_agent_needs_approval_for_policy_changes(kb, monkeypatch):
    monkeypatch.setenv("KB_AGENT", "1")
    with pytest.raises(KBError, match="approval"):
        policy.use(Store.open(kb.root), ["kb-view"])
    policy.use(Store.open(kb.root), ["kb-view"], approved="ok, add the view skill")
