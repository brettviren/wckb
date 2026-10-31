"""Git hooks for exchange repositories (git repositories of bundle files).

Stores themselves are SQLite files, not git repositories; git carries
bundles.  ``kb git hooks install`` puts a marked block into the repository's
hook scripts (respecting ``core.hooksPath``), so hooks the user already has
keep working.  The block calls ``kb hook git-<name> --repo <toplevel>``.

    pre-commit   staged bundle files verify (format, checksum, content hashes);
                 existing bundles are never modified or deleted
    pre-push     the same for every commit being pushed
    post-merge   one line: how many bundles the repository holds, how to import them
"""

import os
import stat
import subprocess
from pathlib import Path

from .errors import KBError

HOOKS = ("pre-commit", "pre-push", "post-merge")
DEFAULT = ("pre-commit", "pre-push", "post-merge")
BEGIN = "# >>> kb >>> (managed by kb git hooks; edit outside this block)"
END = "# <<< kb <<<"
ZERO = "0" * 40


def _git(root: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise KBError(f"{root} is not in a git repository" if "not a git" in r.stderr else r.stderr.strip())
    return r.stdout.strip()


def hooks_dir(root: Path) -> Path:
    p = Path(_git(root, "rev-parse", "--git-path", "hooks"))
    return p if p.is_absolute() else (root / p).resolve()


def block(name: str) -> str:
    return f'{BEGIN}\nkb hook git-{name} --repo "$(git rev-parse --show-toplevel)" "$@" || exit $?\n{END}\n'


def toplevel(root: Path) -> Path:
    return Path(_git(root, "rev-parse", "--show-toplevel")).resolve()


def _strip(text: str) -> str:
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        tail = rest.split(END, 1)[1]
        return head + tail.lstrip("\n")
    return text


def install(root: Path, names=DEFAULT) -> list[str]:
    d = hooks_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    done = []
    for name in names:
        if name not in HOOKS:
            raise KBError(f"unknown hook {name}; kb provides {', '.join(HOOKS)}")
        p = d / name
        text = p.read_text() if p.exists() else "#!/bin/sh\n"
        first = text.splitlines()[0] if text else ""
        if first.startswith("#!") and not any(s in first for s in ("sh", "bash", "zsh", "dash")):
            raise KBError(f"{p} is not a shell script ({first}); call `kb hook git-{name}` from it yourself")
        text = _strip(text)
        if not text.startswith("#!"):
            text = "#!/bin/sh\n" + text
        line1, _, rest = text.partition("\n")
        p.write_text(line1 + "\n" + block(name) + rest)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        done.append(str(p))
    return done


def remove(root: Path, names=HOOKS) -> list[str]:
    d = hooks_dir(root)
    done = []
    for name in names:
        p = d / name
        if p.exists() and BEGIN in p.read_text():
            text = _strip(p.read_text())
            if text.strip() in ("#!/bin/sh", ""):
                p.unlink()
            else:
                p.write_text(text)
            done.append(str(p))
    return done


def status(root: Path) -> dict[str, str]:
    d = hooks_dir(root)
    out = {}
    for name in HOOKS:
        p = d / name
        if not p.exists():
            out[name] = "absent"
        elif BEGIN in p.read_text():
            out[name] = "installed" if os.access(p, os.X_OK) else "installed, not executable"
        else:
            out[name] = "present without kb"
    return out


# -- what the hooks run -------------------------------------------------------

def _bundle_changes(repo: Path, diff_args: list[str]) -> tuple[list[str], list[str]]:
    """(added bundle paths, modified-or-deleted bundle paths) for a git diff."""
    from .bundle import SUFFIX
    out = subprocess.run(["git", "-C", str(repo), "diff", "--name-status", *diff_args],
                         capture_output=True, text=True).stdout
    added, touched = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        status, path = parts[0], parts[-1]
        if not path.endswith(SUFFIX):
            continue
        (added if status.startswith("A") else touched).append(path if status.startswith("A") else parts[1])
    return added, touched


def _verify_blobs(repo: Path, spec_paths: list[tuple[str, str]]) -> list[str]:
    import json
    from .bundle import verify
    problems = []
    for spec, path in spec_paths:
        text = subprocess.run(["git", "-C", str(repo), "show", spec], capture_output=True, text=True).stdout
        try:
            verify(json.loads(text), path)
        except (KBError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
    return problems


def run_pre_commit(repo: Path) -> tuple[int, str]:
    added, touched = _bundle_changes(repo, ["--cached"])
    problems = [f"{p}: bundles are immutable; never edit or delete one, add a new bundle" for p in touched]
    problems += _verify_blobs(repo, [(f":{p}", p) for p in added])
    if not problems:
        return 0, ""
    return 1, "kb: commit refused:\n" + "\n".join(f"  {p}" for p in problems)


def run_pre_push(repo: Path, stdin_text: str) -> tuple[int, str]:
    from . import gitutil
    problems = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:
            continue
        local, remote = parts[1], parts[3]
        base = remote if remote != ZERO and gitutil.resolve(repo, remote) else None
        args = [base, local] if base else ["4b825dc642cb6eb9a060e54bf8d69288fbee4904", local]  # empty tree
        added, touched = _bundle_changes(repo, args)
        problems += [f"{p}: bundles are immutable" for p in touched]
        problems += _verify_blobs(repo, [(f"{local}:{p}", p) for p in added])
    if not problems:
        return 0, ""
    return 1, "kb: push refused:\n" + "\n".join(f"  {p}" for p in problems)


def run_post_merge(repo: Path) -> tuple[int, str]:
    from .bundle import SUFFIX
    n = len(list(repo.rglob(f"*{SUFFIX}")))
    return 0, (f"kb: {n} bundle(s) here; import new ones with kb -C STORE inbox import {repo} [--accept]") if n else ""


def init_exchange(path: Path) -> list[str]:
    """A new exchange repository: git, a README, the hooks."""
    path.mkdir(parents=True, exist_ok=True)
    made = []
    if not (path / ".git").exists():
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        made.append(str(path / ".git"))
    readme = path / "README.md"
    if not readme.exists():
        readme.write_text("# Knowledge exchange\n\nBundles of knowledge items proposed to a kb store, one file each "
                          "under `bundles/`. Bundles are immutable: review the rendered summary in each pull request, "
                          "merge to accept, then import into the store with `kb inbox import <this repo> --accept`.\n")
        made.append(str(readme))
    (path / "bundles").mkdir(exist_ok=True)
    (path / "bundles" / ".gitkeep").touch()
    made += install(path)
    return made
