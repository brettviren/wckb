"""DataLad datasets (and plain git repositories) as data sources.

Everything here applies to two kinds of repository source: ``datalad`` (git +
git-annex, content fetched on demand) and ``git`` (a plain git repository:
its files are in git, so a clone has them all).  A git source's identity is
its first commit; it needs no DataLad.

A DataLad dataset (git + git-annex) is described in a store by a *source
item* (kind ``source``, ``source_type = "datalad"``): its dataset ID (a UUID,
the dataset's global identity), the URLs it can be cloned from, and local
file trees that hold it on particular hosts.  Source items travel with the
items that need them (a knowledge set always carries them), so a receiving
store knows where to get the bytes.

Where the dataset is on *this* machine is not part of the item.  ``locate``
finds a local clone by dataset ID, so every source item for the same
dataset, in every store of this user, shares one clone:

1. an explicit mapping: ``datasets.<uuid>.path`` in settings, the user's
   config.toml or ``.kb/local.toml``;
2. kb's own registry of clones it made or was told about
   (``datasets.toml`` beside the user's config.toml);
3. a path the source items list for this host;
4. a clone in the source directory (``sources.datalad.dir``, by default
   ``~/.cache/wckb/datalad``).

When content is needed and there is no clone, kb clones it into the source
directory, or, with ``sources.datalad.new = "ask"``, asks where (or stops
with instructions when nobody can be asked).

Support needs the DataLad Python package (``pip install 'wckb[datalad]'``)
and git-annex.  Reading what is already local (listing a clone, the content
of files present) uses git and git-annex directly.
"""

import importlib.util
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from .config import _read, source_dir, source_settings, user_config_path
from .errors import KBError
from .items import Item, Store

SOURCE_TYPE = "datalad"
REPO_TYPES = ("datalad", "git")
LABEL = {"datalad": "DataLad dataset", "git": "git repository"}
INSTALL = "pip install 'wckb[datalad]' (or uv tool install --editable '.[datalad]'), and git-annex"

# Set by the CLI when a person can be asked where a new clone goes: (source item, suggestion) -> path or None.
ASK: Callable[[Item, Path], Path | None] | None = None


def available() -> str | None:
    """None when DataLad can be used here, else why not."""
    if importlib.util.find_spec("datalad") is None:
        return "the DataLad Python package is not installed"
    if shutil.which("git-annex") is None:
        return "git-annex is not on PATH"
    return None


def need() -> None:
    why = available()
    if why:
        raise KBError(f"DataLad support: {why}; install with {INSTALL}")


def _git(root: Path, *args: str, check: bool = True, binary: bool = False, stdin: str | None = None):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=not binary, input=stdin)
    if check and r.returncode != 0:
        err = r.stderr.decode(errors="replace") if binary else r.stderr
        raise KBError(f"git {' '.join(args[:2])} in {root}: {err.strip()}")
    return r


# -- datasets on disk -------------------------------------------------------------------

def dataset_id(path: Path) -> str | None:
    """The DataLad dataset ID recorded in .datalad/config, if there is one."""
    cfg = Path(path) / ".datalad" / "config"
    if not cfg.is_file():
        return None
    r = subprocess.run(["git", "config", "-f", str(cfg), "datalad.dataset.id"], capture_output=True, text=True)
    return r.stdout.strip() or None


def _toplevel(path: Path) -> Path | None:
    p = Path(path)
    r = subprocess.run(["git", "-C", str(p if p.is_dir() else p.parent), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True)
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def evidence(root: Path) -> list[str]:
    """Signs that the git repository at ROOT is under DataLad (or git-annex) control."""
    out = []
    uid = dataset_id(root)
    if uid:
        out.append(f"dataset ID {uid} in .datalad/config")
    if _git(root, "config", "--get", "annex.uuid", check=False).stdout.strip():
        out.append("git-annex is initialized")
    attrs = Path(root) / ".gitattributes"
    if attrs.is_file() and "annex." in attrs.read_text(errors="replace"):
        out.append("annex settings in .gitattributes")
    if _git(root, "log", "-n1", "--format=%h", "--grep=^\\[DATALAD", check=False).stdout.strip():
        out.append("DataLad commits in its history")
    mods = Path(root) / ".gitmodules"
    if mods.is_file():
        n = mods.read_text(errors="replace").count("datalad-id")
        if n:
            out.append(f"{n} subdataset(s) with DataLad IDs in .gitmodules")
    return out


def _root_commit(root: Path) -> str | None:
    roots = _git(root, "rev-list", "--max-parents=0", "HEAD", check=False).stdout.split()
    return sorted(roots)[0] if roots else None


def identity(root: Path, kind: str = SOURCE_TYPE) -> str | None:
    """A repository's global identity.  datalad: its DataLad dataset ID, or, for a dataset created without
    one, its first git commit ("git:<sha>"), which every clone shares; None if ROOT is not a dataset.
    git: its first commit."""
    root = Path(root)
    if not (root / ".git").exists():
        return None
    if kind == "git":
        rc = _root_commit(root)
        return f"git:{rc}" if rc else None
    uid = dataset_id(root)
    if uid:
        return uid
    if not evidence(root):
        return None
    rc = _root_commit(root)
    return f"git:{rc}" if rc else None


def short(ident: str) -> str:
    """Eight characters of an identity, for directory names."""
    return ident.split(":")[-1][:8]


def dataset_root(path: Path) -> Path | None:
    """The innermost dataset holding *path* (a file or directory)."""
    top = _toplevel(Path(path).resolve())
    return top if top is not None and evidence(top) else None


def _is_remote(url: str) -> bool:
    return "://" in url or bool(re.match(r"^[\w.-]+@[\w.-]+:", url))


def remote_urls(root: Path) -> list[str]:
    out = []
    for line in _git(root, "remote", "-v", check=False).stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "(fetch)" and _is_remote(parts[1]) and parts[1] not in out:
            out.append(parts[1])
    return out


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "dataset"


# -- source items -------------------------------------------------------------------------

def sources_for(kb: Store, uuid: str, kind: str = SOURCE_TYPE) -> list[Item]:
    return [r for r in kb.items.values() if r.kind == "source" and r.data.get("source_type") == kind
            and r.data.get("source_id") == uuid]


def source_item(kb: Store, ref: str) -> Item:
    rec = kb.get(ref)
    if rec.kind != "source" or rec.data.get("source_type") not in REPO_TYPES:
        raise KBError(f"{rec.id} is not a DataLad or git source (kb source list shows the sources)")
    return rec


def _kind(rec: Item) -> str:
    return rec.data.get("source_type", SOURCE_TYPE)


def _ls_remote(url: str) -> tuple[bool, list[tuple[str, str]], str]:
    """(reachable, [(sha, ref)], error) for a git URL, without cloning; never prompts."""
    import os as _os
    try:
        r = subprocess.run(["git", "ls-remote", url], capture_output=True, text=True, timeout=60,
                           env=_os.environ | {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"})
    except subprocess.TimeoutExpired:
        return False, [], "no answer within 60 s"
    if r.returncode != 0:
        lines = r.stderr.strip().splitlines()
        return False, [], (lines[-1] if lines else f"git ls-remote failed ({r.returncode})")
    return True, [tuple(line.split("\t", 1)) for line in r.stdout.splitlines() if "\t" in line], ""


def recognize(target: str) -> tuple[bool, str]:
    """Is TARGET a DataLad dataset (a local directory) or something to clone one from (a URL)? And why."""
    p = Path(target).expanduser()
    if p.exists():
        if not p.is_dir():
            return False, "a file, not a directory"
        top = _toplevel(p)
        if top is None:
            return False, "not a git repository, so not a DataLad dataset"
        if top != p.resolve():
            inner = dataset_root(p)
            return False, (f"inside the DataLad dataset at {inner}; give that directory" if inner
                           else f"inside the git repository at {top}, which is not a DataLad dataset")
        ev = evidence(top)
        if not ev:
            return False, "a git repository, not a DataLad dataset (no .datalad/config, git-annex or DataLad commits)"
        ident = identity(top)
        if ident is None:
            return False, f"a DataLad dataset with no commits yet ({'; '.join(ev)}): datalad save first"
        what = "no dataset ID, so identified by its first commit" if ident.startswith("git:") else f"dataset ID {ident}"
        signs = [e for e in ev if not e.startswith("dataset ID")]
        return True, f"a DataLad dataset, {what}" + (f" ({'; '.join(signs)})" if signs else "")
    if target.startswith(("ria+", "datalad-annex::")):
        return True, "a DataLad store URL"
    if not _is_remote(target):
        return False, "no such path, and not a URL"
    ok, refs, err = _ls_remote(target)
    if not ok:
        return False, f"not a reachable git repository ({err})"
    if any(ref == "refs/heads/git-annex" for _, ref in refs):
        return True, "a git repository with a git-annex branch (a DataLad dataset, or git-annex without DataLad)"
    return False, "a git repository without git-annex, not a DataLad dataset"


def recognize_git(target: str) -> tuple[bool, str]:
    """Is TARGET a plain git repository (no DataLad or git-annex): a local work tree or a git URL?"""
    p = Path(target).expanduser()
    if p.exists():
        if not p.is_dir():
            return False, "a file, not a directory"
        top = _toplevel(p)
        if top is None:
            return False, "not a git repository"
        if top != p.resolve():
            return False, f"inside the git repository at {top}; give that directory"
        if evidence(top):
            return False, "a DataLad dataset (or git-annex repository), not a plain git repository"
        if _root_commit(top) is None:
            return False, "a git repository with no commits yet"
        return True, "a git repository without DataLad or git-annex"
    if not _is_remote(target):
        return False, "no such path, and not a URL"
    ok, refs, err = _ls_remote(target)
    if not ok:
        return False, f"not a reachable git repository ({err})"
    if any(ref == "refs/heads/git-annex" for _, ref in refs):
        return False, "has a git-annex branch: a DataLad dataset, not a plain git repository"
    return True, "a git repository without git-annex"


def _same_history(root: Path, url: str) -> bool:
    """Does URL share history with the clone at ROOT (a ref of it is known here)?"""
    ok, refs, _ = _ls_remote(url)
    return ok and any(_git(root, "cat-file", "-e", sha + "^{commit}", check=False).returncode == 0
                      for sha, _ in refs)


def _warn(msg: str) -> None:
    import sys
    print(f"kb: warning: {msg}", file=sys.stderr)


NO_ID_WARNING = ("no .datalad/config: this dataset has no DataLad dataset ID (datalad create may have stopped "
                 "early, e.g. because a git ignore rule matched .gitattributes); kb identifies it by its first commit")


def describe(kb: Store, targets: list[str] | str, title: str | None = None, summary: str | None = None,
             check: bool = True, kind: str = SOURCE_TYPE, warn: Callable[[str], None] = _warn) -> Item:
    """The source item for one dataset given by TARGETS: local clones and/or URLs it can be cloned from.

    With a local clone, nothing is cloned; given URLs are checked to share its history (unless not
    CHECK).  With URLs only, the first is cloned to learn the dataset's identity.  An existing item
    for the same dataset gains the new URLs and paths."""
    targets = [targets] if isinstance(targets, str) else list(targets)
    local = [Path(t).expanduser().resolve() for t in targets if Path(t).expanduser().exists()]
    given = [t for t in targets if not Path(t).expanduser().exists()]
    for t in given:
        if not _is_remote(t):
            raise KBError(f"{t}: no such directory, and not a URL")
    label = LABEL[kind]
    if local:
        ids = {}
        for root in local:
            ok, why = (recognize if kind == "datalad" else recognize_git)(str(root))
            uuid = identity(root, kind) if ok else None
            if uuid is None:
                raise KBError(f"{root}: {why}")
            if kind == "datalad" and dataset_id(root) is None:
                warn(f"{root}: {NO_ID_WARNING}")
            ids[root] = uuid
        if len(set(ids.values())) > 1:
            raise KBError(f"these are different {label}s: " + ", ".join(f"{r} ({u})" for r, u in ids.items()))
        uuid, root = ids[local[0]], local[0]
        known = [u for r in local for u in remote_urls(r)]
        if check:
            for u in given:
                if u not in known and not any(_same_history(r, u) for r in local):
                    raise KBError(f"{u} does not share history with {root} (or is unreachable): not the same {label}? "
                                  "(--no-check skips this)")
        urls = list(dict.fromkeys(given + known))
        paths = [{"host": kb.cfg.host, "path": str(r)} for r in local]
    else:
        root = _clone_from(kb, given, None, title or _slug(Path(given[0].rstrip("/")).name.removesuffix(".git")),
                           kind)
        uuid = identity(root, kind)
        if uuid is None:
            raise KBError(f"cloned {given[0]} to {root}, but it is not a {label} (or has no commits)")
        if kind == "datalad" and dataset_id(root) is None:
            warn(f"{given[0]}: {NO_ID_WARNING}")
        urls, paths = list(dict.fromkeys(given + remote_urls(root))), []
    remember(uuid, root)
    have = sources_for(kb, uuid, kind)
    if have:
        return add_places(kb, have[0], urls, paths)
    from . import authoring
    name = title or root.name
    return authoring.new(kb, "source", {
        "title": name, "summary": summary or f"The {label} {name}.",
        "source_type": kind, "source_id": uuid, "urls": urls, "paths": paths})


def add_places(kb: Store, rec: Item, urls: list[str], paths: list[dict]) -> Item:
    import copy
    data = copy.deepcopy(rec.data)
    for k, new in (("urls", urls), ("paths", paths)):
        for x in new:
            if x not in data.setdefault(k, []):
                data[k].append(x)
    if data == rec.data:
        return rec
    return kb.save(Item(data, kb), "source", {"urls": urls, "paths": paths})


def ensure_source(kb: Store, root: Path) -> Item:
    """The source item for the dataset at *root*, made if this store has none."""
    uuid = identity(root)
    have = sources_for(kb, uuid) if uuid else []
    return have[0] if have else describe(kb, [str(root)])


# -- local clones -------------------------------------------------------------------------

def _registry_path() -> Path:
    return user_config_path().parent / "datasets.toml"


def clones() -> dict[str, str]:
    """kb's registry of local clones (dataset ID -> path)."""
    return {k: v.get("path") for k, v in _read(_registry_path()).get("datasets", {}).items()}


def remember(uuid: str, path: Path) -> None:
    reg = _read(_registry_path())
    entry = reg.setdefault("datasets", {}).setdefault(uuid, {})
    if entry.get("path") == str(path):
        return
    entry["path"] = str(path)
    f = _registry_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# Local clones of DataLad datasets and git repositories, by identity. Written by kb "
                 "(kb source clone, kb source link).\n"
                 + tomli_w.dumps(reg))


def _candidates(kb: Store, uuid: str, kind: str) -> list[tuple[Path, str]]:
    out = []
    explicit = kb.cfg.raw.get("datasets", {}).get(uuid, {}).get("path")
    if explicit:
        out.append((Path(os.path.expandvars(explicit)).expanduser(), "settings"))
    if clones().get(uuid):
        out.append((Path(clones()[uuid]), "registry"))
    for rec in sources_for(kb, uuid, kind):
        out += [(Path(p["path"]), "item") for p in rec.data.get("paths", []) if p.get("host") == kb.cfg.host]
    base = source_dir(kb.cfg.raw, kind)
    if base.is_dir():
        out += [(d, "cache") for d in sorted(base.glob(f"*-{short(uuid)}"))]
    return out


def locate(kb: Store, src: Item | str, clone: bool = False) -> Path | None:
    """The local clone of a dataset (a source item, or a dataset ID); with CLONE, make one if needed."""
    uuid = src if isinstance(src, str) else src.data["source_id"]
    kind = SOURCE_TYPE if isinstance(src, str) else _kind(src)
    for path, how in _candidates(kb, uuid, kind):
        if path.is_dir() and identity(path, kind) == uuid:
            if how != "registry" and how != "settings":
                remember(uuid, path)
            return path
    if not clone:
        return None
    return _clone(kb, uuid, kind)


def suggested_dir(kb: Store, rec: Item) -> Path:
    return source_dir(kb.cfg.raw, _kind(rec)) / f"{_slug(rec.data.get('title', ''))}-{short(rec.data['source_id'])}"


def _clone(kb: Store, uuid: str, kind: str = SOURCE_TYPE, dest: Path | None = None) -> Path:
    items = sources_for(kb, uuid, kind)
    if not items:
        raise KBError(f"no source item for {LABEL[kind]} {uuid} in this store")
    rec = items[0]
    if dest is None:
        if source_settings(kb.cfg.raw, kind).get("new", "clone") == "ask":
            dest = ASK(rec, suggested_dir(kb, rec)) if ASK else None
            if dest is None:
                raise KBError(f"{LABEL[kind]} {rec.data.get('title')} ({rec.id}) is not on this machine; "
                              f"clone it with kb source clone {rec.id} [--to DIR], or tell kb where it is with "
                              f"kb source link {rec.id} PATH")
        else:
            dest = suggested_dir(kb, rec)
    origins = [p["path"] for r in items for p in r.data.get("paths", [])
               if p.get("host") == kb.cfg.host and Path(p["path"]).is_dir()]
    origins += [u for r in items for u in r.data.get("urls", [])]
    root = _clone_from(kb, list(dict.fromkeys(origins)), dest, rec.data.get("title", ""), kind)
    got = identity(root, kind)
    if got != uuid:
        raise KBError(f"cloned {root}, but it is {got}, not {uuid} ({rec.id} lists a wrong URL?)")
    remember(uuid, root)
    return root


def _clone_from(kb: Store, origins: list[str], dest: Path | None, name: str, kind: str = SOURCE_TYPE) -> Path:
    if kind == "datalad":
        need()
    if not origins:
        raise KBError(f"no URL or local path to clone the {LABEL[kind]} from (kb source add URL adds one)")
    tmp = dest is None
    if tmp:     # the identity is not known before cloning: clone, then name it
        base = source_dir(kb.cfg.raw, kind)
        base.mkdir(parents=True, exist_ok=True)
        dest = base / f".cloning-{os.getpid()}"
    dest = Path(dest).expanduser()
    if dest.exists() and any(dest.iterdir()):
        raise KBError(f"{dest} exists and is not empty")
    errors = []
    for o in origins:
        try:
            if kind == "datalad":
                import datalad.api as dl
                dl.clone(source=o, path=str(dest), result_renderer="disabled")
            else:
                r = subprocess.run(["git", "clone", "-q", o, str(dest)], capture_output=True, text=True,
                                   env=os.environ | {"GIT_TERMINAL_PROMPT": "0"})
                if r.returncode != 0:
                    raise KBError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "git clone failed")
            break
        except Exception as exc:          # datalad raises its own types; report and try the next origin
            errors.append(f"{o}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
            shutil.rmtree(dest, ignore_errors=True)
    else:
        raise KBError(f"could not clone the {LABEL[kind]}: " + "; ".join(errors))
    if tmp:
        final = dest.parent / f"{_slug(name)}-{short(identity(dest, kind) or 'unknown')}"
        if final.exists():
            shutil.rmtree(dest, ignore_errors=True)
            return final
        dest.rename(final)
        dest = final
    return dest.resolve()


def clone(kb: Store, ref: str, dest: Path | None = None) -> tuple[Path, bool]:
    """A local clone of source REF: (path, made now?)."""
    rec = source_item(kb, ref)
    have = locate(kb, rec)
    if have is not None and dest is None:
        return have, False
    return _clone(kb, rec.data["source_id"], _kind(rec), dest), True


def link(kb: Store, ref: str, path: Path) -> Path:
    """Tell kb that PATH is a clone of source REF (checked), for every store of this user."""
    rec = source_item(kb, ref)
    path = Path(path).expanduser().resolve()
    got = identity(path, _kind(rec))
    if got != rec.data["source_id"]:
        raise KBError(f"{path} is " + (got if got else f"not a {LABEL[_kind(rec)]}")
                      + f", not {rec.data['source_id']} ({rec.id})")
    remember(got, path)
    return path


# -- content ------------------------------------------------------------------------------

@dataclass
class TreeFile:
    path: str
    blob: str
    key: str | None = None          # git-annex key, for annexed files

    @property
    def sha256(self) -> str | None:
        if self.key and self.key.startswith("SHA256"):
            return self.key.split("--", 1)[1].split(".", 1)[0]
        return None

    @property
    def size(self) -> int | None:
        m = re.match(r"^[A-Z0-9]+-s(\d+)", self.key or "")
        return int(m[1]) if m else None


def ls(root: Path, rev: str = "HEAD", path: str = "") -> list[TreeFile]:
    """Files under PATH at REV, with annex keys read from git (no content is fetched)."""
    args = ["ls-tree", "-r", "-z", "--full-tree", rev] + ([path.rstrip("/")] if path else [])
    out = _git(root, *args).stdout
    rows = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, rel = entry.split("\t", 1)
        mode, kind, blob = meta.split()
        if kind != "blob" or any(part.startswith(".") for part in rel.split("/")):
            continue
        rows.append((mode, blob, rel))
    links = [b for m, b, _ in rows if m == "120000"]
    targets = _cat_batch(root, links) if links else {}
    files = []
    for mode, blob, rel in rows:
        key = None
        if mode == "120000":
            t = targets.get(blob, "")
            if "annex/objects/" in t:
                key = t.rsplit("/", 1)[-1]
        files.append(TreeFile(rel, blob, key))
    return files


def _cat_batch(root: Path, blobs: list[str]) -> dict[str, str]:
    r = _git(root, "cat-file", "--batch", stdin="\n".join(blobs) + "\n")
    out, lines, i = {}, r.stdout.split("\n"), 0
    while i < len(lines):
        head = lines[i].split()
        if len(head) == 3:
            out[head[0]] = lines[i + 1]
            i += 2
        else:
            i += 1
    return out


def content(kb: Store, root: Path, tf: TreeFile, fetch: bool = True) -> Path | None:
    """A local file with TF's bytes: the annexed object (fetched if allowed), or the git blob."""
    if tf.key:
        return key_content(root, tf.key, fetch)
    p = source_dir(kb.cfg.raw, "git") / "blobs" / tf.blob[:2] / tf.blob / Path(tf.path).name
    if not p.is_file():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".partial")
        tmp.write_bytes(_git(root, "cat-file", "blob", tf.blob, binary=True).stdout)
        tmp.replace(p)
    return p


def key_content(root: Path, key: str, fetch: bool = True) -> Path | None:
    def where():
        r = _git(root, "annex", "contentlocation", key, check=False)
        return root / r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None
    p = where()
    if p is None and fetch:
        need()
        _git(root, "annex", "get", "--key", key, check=False)
        p = where()
    return p


def get(root: Path, relpath: str) -> Path | None:
    """The file at RELPATH in the working tree, its content fetched (datalad get) if missing."""
    p = root / relpath
    if p.exists():
        return p
    if not os.path.lexists(p):
        return None
    need()
    import datalad.api as dl
    try:
        dl.get(path=str(p), dataset=str(root), result_renderer="disabled", on_failure="ignore")
    except Exception:            # reported by the caller as "not available"
        return None
    return p if p.exists() else None


def tracked(root: Path, relpath: str) -> TreeFile | None:
    """RELPATH as committed at HEAD, if the working tree has it unchanged."""
    if _git(root, "status", "--porcelain", "--", relpath, check=False).stdout.strip():
        return None
    hit = [f for f in ls(root, "HEAD", relpath) if f.path == relpath]
    return hit[0] if hit else None


def tags(root: Path, refresh: bool = False) -> list[str]:
    if refresh and _git(root, "remote", check=False).stdout.strip():
        _git(root, "fetch", "--all", "--tags", "--quiet", check=False)
    return [t for t in _git(root, "tag", "--list", "--sort=creatordate").stdout.split() if t]

