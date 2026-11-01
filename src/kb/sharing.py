"""Sources shared by the users of one computer.

A *shared source* is a source directory that a Unix group shares.  It takes
one of two roles:

- **owner**: the directory itself, its permissions open to the group:
  everything gets the group and the owner's permissions, directories pass
  the group on (setgid), and a git repository gets
  ``core.sharedRepository=group``.  Its owner (a person, or an account such as
  ``datalad``) makes it, or makes an existing one shared.
- **clone**: a user's DataLad clone of an owner's dataset, made with
  ``--reckless ephemeral``: its ``.git/annex`` is a link to the owner's, so a
  file got in any clone is stored once, in the owner's annex.  The clone
  needs ``core.sharedRepository`` too, or what is got there is readable by its
  getter alone; and git must be told to trust the owner's repository
  (``safe.directory``).

Whatever is got into a shared annex, the group can read.  The Indico remote
(git-annex-remote-indico) refuses there the files of protected events unless
``indico.shareprotected`` is set.

Kept in step with ``git indico share`` and ``git indico clone``, which do the
same for Indico datasets without kb.
"""

import grp
import logging
import os
import stat
import subprocess
from pathlib import Path

from .errors import KBError

log = logging.getLogger(__name__)

#: ``core.sharedRepository`` values meaning the owner alone.
_PRIVATE = ("", "0", "false", "no", "off", "umask")


def _git(path: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise KBError(f"git {' '.join(args[:2])} in {path}: {(r.stderr or r.stdout).strip()}")
    return r.stdout


def shared_value(value: str | None) -> str | None:
    """What ``core.sharedRepository`` says: ``group``, ``all``, an octal mode, or None for private."""
    v = (value or "").strip().lower()
    if v in _PRIVATE:
        return None
    if v in ("1", "true", "yes", "on", "group"):
        return "group"
    if v in ("2", "all", "world", "everybody"):
        return "all"
    try:
        return v if int(v, 8) & 0o077 else None
    except ValueError:
        return None


def is_repo(path: Path) -> bool:
    return (Path(path) / ".git").exists()


def shared_config(path: Path) -> str | None:
    return shared_value(_git(path, "config", "--get", "core.sharedRepository", check=False)) if is_repo(path) else None


def origin_of(path: Path) -> Path | None:
    """The owner's dataset whose annex this one uses (a shared clone), or None."""
    annex = Path(path) / ".git" / "annex"
    return annex.resolve().parent.parent if annex.is_symlink() else None


def role(path: Path) -> str | None:
    """``clone`` (a shared clone), ``owner`` (shared for a group), or None (not shared)."""
    if origin_of(path):
        return "clone"
    if shared_config(path):
        return "owner"
    st = Path(path).stat()
    return "owner" if not is_repo(path) and st.st_mode & stat.S_ISGID and st.st_mode & 0o070 == 0o070 else None


def group_of(path: Path, group: str | None = None) -> tuple[str, int]:
    """The group named, or that of PATH (or of its nearest existing parent); you must be in it."""
    if group:
        try:
            g = grp.getgrnam(group)
        except KeyError as exc:
            raise KBError(f"no group {group!r}") from exc
    else:
        where = Path(path)
        while not where.exists():
            where = where.parent
        g = grp.getgrgid(where.stat().st_gid)
    if g.gr_gid not in os.getgroups() and g.gr_gid != os.getgid():
        raise KBError(f"you are not in the group {g.gr_name}: give --group, or ask to be added (then log in again)")
    return g.gr_name, g.gr_gid


def trust(path: Path) -> list[str]:
    """Let git use a repository of another user (``safe.directory``)."""
    path = Path(path).resolve()
    if path.stat().st_uid == os.getuid() or not is_repo(path):
        return []
    listed = subprocess.run(["git", "config", "--global", "--get-all", "safe.directory"],
                            capture_output=True, text=True).stdout.split("\n")
    if str(path) in listed or "*" in listed:
        return []
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(path)], check=True)
    return [f"added {path} to safe.directory in your git config (it belongs to another user)"]


def _walk(path: Path):
    for top, _, files in os.walk(path):
        for p in [Path(top), *(Path(top) / f for f in files)]:
            st = os.lstat(p)
            if not stat.S_ISLNK(st.st_mode):
                yield p, st


def _permit(path: Path, gid: int) -> tuple[int, int]:
    """Give the group what the owner has on everything of ours below PATH; directories pass the group on.
    (changed, left alone because another user owns them)."""
    changed = others = 0
    for p, st in _walk(path):
        mode = stat.S_IMODE(st.st_mode)
        want = mode | ((mode & 0o700) >> 3) | (stat.S_ISGID if stat.S_ISDIR(st.st_mode) else 0)
        if st.st_gid == gid and want == mode:
            continue
        if st.st_uid != os.getuid():
            others += 1
            continue
        if st.st_gid != gid:
            os.chown(p, -1, gid, follow_symlinks=False)
        p.chmod(want)
        changed += 1
    return changed, others


def _forbid(path: Path) -> tuple[int, int]:
    """Take the group's write permission and setgid off everything of ours below PATH."""
    changed = others = 0
    for p, st in _walk(path):
        mode = stat.S_IMODE(st.st_mode)
        want = mode & ~(stat.S_IWGRP | stat.S_IWOTH | stat.S_ISGID)
        if want == mode:
            continue
        if st.st_uid != os.getuid():
            others += 1
            continue
        p.chmod(want)
        changed += 1
    return changed, others


def share(path: Path, group: str | None = None) -> list[str]:
    """Make PATH (a repository or a plain directory) shared by GROUP, as its owner.  Run again, it repairs."""
    path = Path(path).expanduser().resolve()
    name, gid = group_of(path, group)
    done = trust(path)
    if is_repo(path):
        if _git(path, "rev-parse", "--is-bare-repository").strip() == "true":
            raise KBError(f"{path}: a bare repository; share a repository with a work tree")
        if shared_config(path) != "group":
            _git(path, "config", "core.sharedRepository", "group")
            done.append("set core.sharedRepository=group")
    changed, others = _permit(path, gid)
    if changed:
        done.append(f"gave the group {name} {changed} files and directories")
    if others:
        done.append(f"left {others} files and directories of other users as they are (their owners can run this too)")
    log.info("shared %s with the group %s", path, name)
    return done


def unshare(path: Path) -> list[str]:
    """Stop sharing PATH, as its owner: the group may read what it could, but no longer add or change."""
    path = Path(path).expanduser().resolve()
    done = []
    if is_repo(path) and shared_config(path):
        _git(path, "config", "--unset", "core.sharedRepository")
        done.append("unset core.sharedRepository")
    changed, others = _forbid(path)
    if changed:
        done.append(f"took the group's write permission off {changed} files and directories")
    if others:
        done.append(f"{others} files and directories belong to other users; they keep their permissions")
    log.info("unshared %s", path)
    return done


def create(path: Path, group: str | None = None, shared: bool = False, text2git: bool = True) -> list[str]:
    """A new DataLad dataset at PATH (text in git, as text2git, unless not TEXT2GIT: then all annexed),
    shared by GROUP if SHARED."""
    from . import datasets
    datasets.need()
    path = Path(path).expanduser().absolute()
    if path.exists() and any(path.iterdir()):
        raise KBError(f"{path} exists and is not empty")
    done = []
    if shared:
        _, gid = group_of(path, group)
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, -1, gid)
        path.chmod(0o2775)
        _git(path, "init", "-q", "--shared=group")
    import datalad.api as dl
    dl.create(path=str(path), force=shared, cfg_proc=["text2git"] if text2git else None,
              result_renderer="disabled")
    done.append(f"made a DataLad dataset at {path}")
    if shared:
        done += share(path, group)
    return done


def clone(source: Path, dest: Path) -> list[str]:
    """A shared clone at DEST of the owner's dataset SOURCE (a local, shared DataLad dataset)."""
    from . import datasets
    datasets.need()
    source = Path(source).expanduser().resolve()
    dest = Path(dest).expanduser().absolute()
    if not is_repo(source):
        raise KBError(f"{source}: not a dataset on this computer, so it cannot be shared from")
    done = trust(source)
    value = shared_config(source)
    if not value:
        raise KBError(f"{source} is not shared; its owner can share it: kb source share {source}")
    group_of(source, grp.getgrgid(source.stat().st_gid).gr_name)
    if dest.exists() and any(dest.iterdir()):
        raise KBError(f"{dest} exists and is not empty")
    import datalad.api as dl
    dl.clone(source=str(source), path=str(dest), reckless="ephemeral", git_clone_opts=[
        "--config", f"core.sharedRepository={value}"], result_renderer="disabled")
    _git(dest, "config", "core.sharedRepository", value)   # what is got here takes its permissions from this
    done.append(f"cloned {source} to {dest}, sharing its annex, with core.sharedRepository={value}")
    log.info("shared clone %s of %s", dest, source)
    return done


def adopt(path: Path) -> list[str]:
    """Set up an existing shared clone (made by hand) as :func:`clone` would have."""
    origin = origin_of(path)
    done = trust(origin)
    value = shared_config(origin) or "group"
    if shared_config(path) != value:
        _git(path, "config", "core.sharedRepository", value)
        done.append(f"set core.sharedRepository={value}")
    return done


def unclone(path: Path) -> list[str]:
    """Make a shared clone a clone of its own: its own annex (empty), no longer the owner's."""
    path = Path(path).expanduser().resolve()
    origin = origin_of(path)
    annex = path / ".git" / "annex"
    present = _git(path, "annex", "find", "--in", "here", check=False).split("\n")
    annex.unlink()
    annex.mkdir()
    for key in ("core.sharedRepository", "annex.uuid", "datalad.clone.reckless", "annex.private"):
        _git(path, "config", "--unset", key, check=False)
    _git(path, "annex", "init", "-q")
    n = len([p for p in present if p])
    log.info("unshared the clone %s of %s", path, origin)
    return [f"{path} has its own annex now, no longer {origin}'s"
            + (f"; {n} files must be got again (datalad get), from the origin or elsewhere" if n else "")]

