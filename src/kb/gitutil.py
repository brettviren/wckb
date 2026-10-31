"""The little git kb needs, for exchange repositories (git repositories of bundle files)."""

import subprocess
from pathlib import Path

from .errors import KBError


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def resolve(root: Path, ref: str) -> str | None:
    r = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return r.stdout.strip() if r.returncode == 0 else None


def commit(root: Path, paths: list[str], message: str) -> str:
    """Stage *paths* (relative to *root*) and commit; returns the new commit."""
    r = _git(root, "add", "--", *paths)
    if r.returncode != 0:
        raise KBError(f"git add in {root}: {r.stderr.strip()}")
    r = _git(root, "commit", "-q", "-m", message, "--", *paths)
    if r.returncode != 0:
        raise KBError(f"git commit in {root} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return resolve(root, "HEAD") or ""


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
