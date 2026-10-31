"""Exchange repositories: git as transport and review for bundles.

An exchange repository is an ordinary git repository holding bundle files
(``bundles/YYYY/<set>-<bundle>.kbundle.json``).  Because bundles are immutable
and uniquely named, branches and pull requests over it never conflict.  A
sender proposes a bundle by pull request (``kb push --land pr``); reviewers
read the rendered summary in the PR description (``kb bundle show
--format md``); once merged, the receiving store imports it
(``kb inbox import EXCHANGE_DIR --accept``).
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import bundle as bundles
from . import gitutil
from .errors import KBError


def _git(root: Path, *args) -> str:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise KBError(f"git {' '.join(args[:2])} in {root}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def bundle_path(b: dict) -> str:
    return f"bundles/{b['created']['at'][:4]}/{b['set']['id']}-{b['id'][:12]}{bundles.SUFFIX}"


def propose(kb, nb, b: dict, message: str | None = None, against=None) -> dict:
    """Commit the bundle on a new branch of the neighbor's exchange clone, push it, open a pull request."""
    from . import forge
    from .render import bundle_text, bundle_view
    repo = nb.exchange
    if repo is None or not (repo / ".git").exists():
        raise KBError(f"neighbor {nb.name} has no exchange repository clone here "
                      "(kb neighbor add ... --exchange PATH, a git clone of its bundle repository)")
    branch = f"kb/{b['set']['id']}-{b['id'][:8]}"
    base = gitutil.current_branch(repo)
    wt = Path(tempfile.mkdtemp(prefix="kb-ex-"))
    wt.rmdir()
    _git(repo, "worktree", "add", "-q", "-b", branch, str(wt), "HEAD")
    out = {"branch": branch}
    try:
        rel = bundle_path(b)
        dest = wt / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        bundles.write(b, dest)
        title = message or f"Knowledge from {b['from'].get('name')}: {b['set'].get('title', b['set']['id'])}"
        body = bundle_text(bundle_view(b, against), md=True)   # against the receiver, when visible
        out["commit"] = gitutil.commit(wt, [rel], f"{title}\n\nbundle {b['id']}")
        _git(wt, "push", "-q", "-u", "origin", branch)
        r = forge.repo_for(kb.cfg.raw, repo)
        out["pr"] = forge.client(r.forge).pr_create(r.owner, r.name, branch, base, title, body)
    finally:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
        shutil.rmtree(wt, ignore_errors=True)
    return out
