"""``kb init``: a new store (one SQLite file), its settings, policy and enactment."""

import secrets
import subprocess
from pathlib import Path

from .config import ACCESS_LEVELS, SCOPES, SUBDIR
from .db import DB_NAME
from .errors import KBError
from .schema import REVISION

DEFAULT_ACCESS = {"project": "private", "home": "private", "group": "group", "org": "collaboration",
                  "other": "group"}


def default_target(path: Path, scope: str) -> str:
    """A project store kept in ./kb beside the working files enacts policy one level up."""
    return ".." if scope == "project" and path.name == SUBDIR else "."


def _in_git_worktree(path: Path) -> bool:
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    return r.returncode == 0 and r.stdout.strip() == "true"


def init(path: Path, name: str | None = None, access: str | None = None, scope: str = "other",
         target: str | None = None, force: bool = False, enact: bool = True,
         up: Path | None = None) -> list[str]:
    from . import federation, policy
    from .items import Store
    if scope not in SCOPES:
        raise KBError(f"scope must be one of {', '.join(SCOPES)}")
    access = access or DEFAULT_ACCESS[scope]
    if access not in ACCESS_LEVELS:
        raise KBError(f"access must be one of {', '.join(ACCESS_LEVELS)}")
    path.mkdir(parents=True, exist_ok=True)
    path = path.resolve()
    if (path / DB_NAME).exists():
        raise KBError(f"{path} already holds a store ({DB_NAME})")
    name = name or (path.parent.name if path.name == SUBDIR else path.name)
    store_id = "kb-" + "".join(secrets.choice("0123456789abcdefghjkmnpqrstvwxyz") for _ in range(10))
    settings = {
        "kb": {"id": store_id, "name": name, "scope": scope, "schema": REVISION, "access": access},
        "limits": {"max_inline_bytes": 1024 * 1024},
        "lint": {"unverified_days": 90},
    } | policy.default_settings(scope, target or default_target(path, scope))
    Store.create(path, settings)
    made = [str(path / DB_NAME)]
    if _in_git_worktree(path):
        # A store is not versioned by the repository around it; knowledge travels by bundles.
        (path / ".gitignore").write_text("# a kb store: not versioned here (share it with kb bundles)\n*\n")
        made.append(str(path / ".gitignore"))
    kb = Store.open(path)
    if up is not None:
        other = Store.open(Path(up).expanduser().resolve(), readonly=True)
        federation.add(kb, other.cfg.name, "up", path=str(other.root))
        made.append(f".kb/local.toml (neighbor {other.cfg.name}, up)")
    if enact:
        r = policy.enact(Store.open(path))
        made += [c["path"] for c in r["changes"]]
    return made
