"""Store contexts, the cascade of config files, and logging."""

import os
from datetime import date
from pathlib import Path

import pytest
from click.testing import CliRunner

from kb import config
from kb.cli import main
from kb.errors import KBError
from kb.scaffold import init

from conftest import git


def kb(*args, cwd: Path | None = None):
    old = os.getcwd()
    if cwd:
        os.chdir(cwd)
    try:
        return CliRunner().invoke(main, list(args), catch_exceptions=False)
    finally:
        os.chdir(old)


@pytest.fixture
def work(tmp_path) -> Path:
    """A git working tree with a subdirectory."""
    w = tmp_path / "src" / "ana"
    (w / "sub").mkdir(parents=True)
    git(w, "init", "-q")
    return w


def user_config(text: str) -> Path:
    f = config.user_config_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f


def test_names_from_directories(work, monkeypatch):
    ctx = config.resolve_context(work / "sub")
    assert ctx.name == config.context_name(work) == str(work).strip("/").replace("/", "-")
    assert ctx.named_by == "the default" and ctx.directory == work
    assert ctx.location == config.data_home() / "contexts" / ctx.name and not ctx.exists
    monkeypatch.setenv("KB_CONTEXT", "current-directory")
    assert config.resolve_context(work / "sub").name == config.context_name(work / "sub")


def test_name_precedence(work, monkeypatch):
    user_config('kb_context = "from-config"\n')
    assert config.resolve_context(work).name == "from-config"
    monkeypatch.setenv("KB_CONTEXT", "from-env")
    assert config.resolve_context(work).name == "from-env"
    monkeypatch.setattr(config, "INVOCATION", config.Invocation(context="from-option"))
    ctx = config.resolve_context(work)
    assert (ctx.name, ctx.named_by) == ("from-option", "--context")


def test_no_name_is_an_error(tmp_path):
    with pytest.raises(KBError, match="not in a git working tree.*--context NAME"):
        config.resolve_context(tmp_path)
    # but a store above names the context after its directory
    init(tmp_path / "kb", name="mine", enact=False)
    ctx = config.resolve_context(tmp_path / "kb")
    assert ctx.location == tmp_path / "kb" and ctx.name == config.context_name(tmp_path / "kb")


def test_locations(work, tmp_path):
    # the nearest store above, in any of the three places
    init(work / ".kb", name="hidden", enact=False)
    assert config.resolve_context(work / "sub").location == work / ".kb"
    # a configured location wins, relative to the directory its file is for
    (work / ".kb.toml").write_text(f'[context.{config.context_name(work)}]\nlocation = "../../stores/ana"\n')
    ctx = config.resolve_context(work / "sub")
    assert ctx.location == tmp_path / "stores" / "ana" and str(work / ".kb.toml") in ctx.located_by
    with pytest.raises(KBError, match="holds no kb.sqlite.*kb init"):
        config.store_context(work / "sub")


def test_cascade(work, tmp_path, monkeypatch):
    user_config('a = "user"\nb = "user"\nc = "user"\n[t]\nx = 1\n')
    (work.parent / "kb.toml").write_text('b = "above"\nc = "above"\n[t]\ny = 2\n')
    (work / ".kb").mkdir()
    (work / ".kb" / "config.toml").write_text('c = "here"\n')
    raw = config.cascade(work / "sub")
    assert (raw["a"], raw["b"], raw["c"], raw["t"]) == ("user", "above", "here", {"x": 1, "y": 2})
    extra = tmp_path / "extra.toml"
    extra.write_text('a = "extra"\n')
    monkeypatch.setattr(config, "INVOCATION", config.Invocation(config=extra, no_init_file=True))
    assert config.cascade(work / "sub") == {"a": "extra"}
    monkeypatch.setattr(config, "INVOCATION", config.Invocation(config=extra))
    raw = config.cascade(work / "sub")
    assert (raw["a"], raw["c"]) == ("extra", "here")


def test_cascade_reaches_store_settings(root, tmp_path, monkeypatch):
    from kb.items import Store
    (root / "kb.toml").write_text('user = "per-from-toml"\n')
    monkeypatch.delenv("KB_USER")
    monkeypatch.chdir(root)
    assert Store.open(root).cfg.user == "per-from-toml"


def test_init_and_use_the_context_store(work):
    r = kb("init", "--scope", "project", cwd=work / "sub")
    assert r.exit_code == 0, r.output
    name = config.context_name(work)
    store = config.data_home() / "contexts" / name
    assert (store / "kb.sqlite").exists() and (work / "CLAUDE.md").exists()   # policy goes to the work tree
    r = kb("status", cwd=work / "sub")
    assert r.exit_code == 0 and f"context   {name}" in r.stdout and str(store) in r.stdout
    r = kb("init", cwd=work)
    assert r.exit_code != 0 and "has a store already" in r.output


def test_logging(root, tmp_path):
    from kb import logs
    r = kb("-C", str(root), "show", "obs-0000000000")
    assert r.exit_code == 1
    log = logs.log_dir() / f"kb-{date.today().isoformat()}.log"
    first = log.read_text()
    assert "ERROR" in first and "no item obs-0000000000" in first
    kb("-C", str(root), "show", "obs-0000000000")
    assert log.read_text().startswith(first) and log.read_text().count("ERROR") == 2   # appended

    r = kb("-C", str(root), "--debug", "ls")
    assert "DEBUG [" in r.stdout and "opened the store" in r.stdout and config.context_name(root) in r.stdout

    sink = tmp_path / "my.log"
    kb("-C", str(root), "-l", "info", "-L", str(sink), "store", "reindex")
    assert "INFO" in sink.read_text() and "reindexed" in sink.read_text()

    # a message for the person is not shown twice when the log goes to the terminal too
    r = kb("-C", str(root), "-l", "info", "-L", "stderr", "store", "reindex")
    assert r.stderr.count("reindexed") == 1 and "INFO" in r.stderr
    r = kb("-C", str(root), "store", "reindex")
    assert r.stderr.startswith("reindexed") and r.stdout == ""
