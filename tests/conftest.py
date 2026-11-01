import subprocess
from pathlib import Path

import pytest

from kb import authoring
from kb.items import Store
from kb.scaffold import init


def git(root: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path_factory):
    """No user config, a fixed user, no stray KB_ROOT or KB_CONTEXT, and logs in the test's cache."""
    from kb import config, logs
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setattr(config, "INVOCATION", config.Invocation())
    monkeypatch.delenv("KB_CONTEXT", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("KB_USER", "per-tester")
    monkeypatch.delenv("KB_ROOT", raising=False)
    for v in ("KB_AGENT", "CLAUDECODE", "AI_AGENT"):   # tests may run inside Claude Code
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "config"))         # no user git ignore rules
    (home / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@example.org")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@example.org")
    yield
    logs.reset()


@pytest.fixture
def root(tmp_path) -> Path:
    r = tmp_path / "kb"
    init(r, name="test")
    return r


@pytest.fixture
def kb(root) -> Store:
    return Store.open(root)


def commit(root: Path, msg: str = "commit") -> None:
    """For git repositories in tests (exchange repositories, project working trees)."""
    git(root, "add", "-A")
    if git(root, "status", "--porcelain").strip():
        git(root, "commit", "-q", "--no-verify", "-m", msg)


def observation(kb: Store, runs=("1234",), subject=("detector/tpc/noise",), text="Noise was high.", **kw):
    fields = authoring.fields_from_options(
        title=kw.pop("title", "Noisy run"), summary=kw.pop("summary", "Elevated coherent noise was seen."),
        subject=subject, runs=runs, by=kw.pop("by", "A. Colleague"), how="verbal", when="2026-10-28", **kw)
    return authoring.new(kb, "observation", fields, text=text)


def set_store(root: Path, name="default", publish=True) -> Path:
    """A content-addressed store (and a publish area) for this store, as machine-local settings."""
    store = root.parent / f"cas-{name}"
    pub = root.parent / "published"
    (root / ".kb").mkdir(exist_ok=True)
    text = f'[cas.{name}]\npath = "{store}"\n'
    if publish:
        text += f'[views]\npublish_path = "{pub}"\npublish_url = "https://example.org/views"\n'
    (root / ".kb" / "local.toml").write_text(text)
    return store


def make_store(path: Path, scope: str, name: str | None = None, **kw) -> Store:
    init(path, name=name, scope=scope, **kw)
    return Store.open(path)
