"""The little git that kb needs: base-ref contents and a description of HEAD."""

import subprocess
from pathlib import Path


def _git(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def is_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--git-dir").returncode == 0


def resolve(root: Path, ref: str) -> str | None:
    r = _git(root, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return r.stdout.strip() if r.returncode == 0 else None


def default_base(root: Path) -> str | None:
    """origin/main or origin/master if present, else HEAD; None without commits."""
    for ref in ("origin/main", "origin/master", "HEAD"):
        if resolve(root, ref):
            return ref
    return None


def ls_records(root: Path, ref: str) -> list[str]:
    """record.json paths at *ref*, relative to *root* (which may be a
    subdirectory of the repository, as for a project's own store)."""
    r = _git(root, "ls-tree", "-r", "--name-only", ref, "--", "records/")
    return [p for p in r.stdout.splitlines() if p.endswith("/record.json")]


def show(root: Path, ref: str, path: str) -> bytes | None:
    """Content of *path* (relative to *root*) at *ref*."""
    r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:./{path}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def describe(root: Path) -> dict:
    head = resolve(root, "HEAD")
    if not head:
        return {}
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip())
    return {"commit": head, "dirty": dirty}


def changed_paths(root: Path, base: str) -> list[str]:
    """Paths changed in the working tree relative to *base*, untracked included."""
    a = _git(root, "diff", "--name-only", base).stdout.splitlines()
    b = _git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return sorted(set(a + b))


def commit(root: Path, paths: list[str], message: str) -> str:
    """Stage *paths* (relative to *root*) and commit; returns the new commit."""
    from .errors import KBError
    r = _git(root, "add", "--", *paths)
    if r.returncode != 0:
        raise KBError(f"git add in {root}: {r.stderr.strip()}")
    r = _git(root, "commit", "-q", "-m", message, "--", *paths)
    if r.returncode != 0:
        raise KBError(f"git commit in {root} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return resolve(root, "HEAD") or ""


def is_clean(root: Path) -> bool:
    return not _git(root, "status", "--porcelain").stdout.strip()


def switch_new(root: Path, branch: str) -> None:
    r = _git(root, "switch", "-c", branch)
    if r.returncode != 0:
        r = _git(root, "switch", branch)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip())


def clone_or_pull(url: str, dest: Path) -> str:
    if (dest / ".git").exists():
        r = _git(dest, "pull", "-q", "--ff-only")
        action = "updated"
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["git", "clone", "-q", "--filter=blob:none", url, str(dest)],
                           capture_output=True, text=True)
        action = "cloned"
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return action


def dirty(root: Path, paths: list[str]) -> list[str]:
    """Paths (relative to *root*) with uncommitted changes, untracked files included."""
    r = _git(root, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    return [line[3:] for line in r.stdout.splitlines() if line.strip()]


def toplevel(root: Path) -> Path:
    return Path(_git(root, "rev-parse", "--show-toplevel").stdout.strip())


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def is_ignored(root: Path, path: str) -> bool:
    return _git(root, "check-ignore", "-q", "--", path).returncode == 0
