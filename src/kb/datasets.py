"""DataLad datasets as data sources.

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
    cfg = Path(path) / ".datalad" / "config"
    if not cfg.is_file():
        return None
    r = subprocess.run(["git", "config", "-f", str(cfg), "datalad.dataset.id"], capture_output=True, text=True)
    return r.stdout.strip() or None


def dataset_root(path: Path) -> Path | None:
    """The innermost dataset holding *path* (a file or directory)."""
    p = Path(path).resolve()
    for d in [p] + list(p.parents):
        if (d / ".datalad" / "config").is_file():
            return d
    return None


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

def sources_for(kb: Store, uuid: str) -> list[Item]:
    return [r for r in kb.items.values() if r.kind == "source" and r.data.get("source_type") == SOURCE_TYPE
            and r.data.get("source_id") == uuid]


def source_item(kb: Store, ref: str) -> Item:
    rec = kb.get(ref)
    if rec.kind != "source" or rec.data.get("source_type") != SOURCE_TYPE:
        raise KBError(f"{rec.id} is not a DataLad source (kb source list shows the sources)")
    return rec


def describe(kb: Store, target: str, title: str | None = None, summary: str | None = None) -> Item:
    """The source item for the dataset at TARGET: a local dataset directory, or a URL (cloned to learn
    its identity).  An existing item for the same dataset gains the new URLs and paths."""
    p = Path(target).expanduser()
    if p.exists():
        root = p.resolve()
        uuid = dataset_id(root)
        if uuid is None:
            inner = dataset_root(root)
            raise KBError(f"{root} is not a DataLad dataset" + (f" (it is inside {inner})" if inner else ""))
        urls, paths = remote_urls(root), [{"host": kb.cfg.host, "path": str(root)}]
    else:
        if not _is_remote(target):
            raise KBError(f"{target}: no such directory, and not a URL")
        root = _clone_from(kb, [target], None, title or _slug(Path(target.rstrip("/")).name.removesuffix(".git")))
        uuid = dataset_id(root)
        urls, paths = [target] + [u for u in remote_urls(root) if u != target], []
    remember(uuid, root)
    have = sources_for(kb, uuid)
    if have:
        return add_places(kb, have[0], urls, paths)
    from . import authoring
    name = title or root.name
    return authoring.new(kb, "source", {
        "title": name, "summary": summary or f"The DataLad dataset {name}.",
        "source_type": SOURCE_TYPE, "source_id": uuid, "urls": urls, "paths": paths})


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
    uuid = dataset_id(root)
    have = sources_for(kb, uuid) if uuid else []
    return have[0] if have else describe(kb, str(root))


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
    f.write_text("# Local clones of DataLad datasets, by dataset ID. Written by kb; kb source link edits it.\n"
                 + tomli_w.dumps(reg))


def _candidates(kb: Store, uuid: str) -> list[tuple[Path, str]]:
    out = []
    explicit = kb.cfg.raw.get("datasets", {}).get(uuid, {}).get("path")
    if explicit:
        out.append((Path(os.path.expandvars(explicit)).expanduser(), "settings"))
    if clones().get(uuid):
        out.append((Path(clones()[uuid]), "registry"))
    for rec in sources_for(kb, uuid):
        out += [(Path(p["path"]), "item") for p in rec.data.get("paths", []) if p.get("host") == kb.cfg.host]
    base = source_dir(kb.cfg.raw, SOURCE_TYPE)
    if base.is_dir():
        out += [(d, "cache") for d in sorted(base.glob(f"*-{uuid[:8]}"))]
    return out


def locate(kb: Store, src: Item | str, clone: bool = False) -> Path | None:
    """The local clone of a dataset (a source item, or a dataset ID); with CLONE, make one if needed."""
    uuid = src if isinstance(src, str) else src.data["source_id"]
    for path, how in _candidates(kb, uuid):
        if path.is_dir() and dataset_id(path) == uuid:
            if how != "registry" and how != "settings":
                remember(uuid, path)
            return path
    if not clone:
        return None
    return _clone(kb, uuid)


def suggested_dir(kb: Store, rec: Item) -> Path:
    return source_dir(kb.cfg.raw, SOURCE_TYPE) / f"{_slug(rec.data.get('title', ''))}-{rec.data['source_id'][:8]}"


def _clone(kb: Store, uuid: str, dest: Path | None = None) -> Path:
    items = sources_for(kb, uuid)
    if not items:
        raise KBError(f"no source item for DataLad dataset {uuid} in this store")
    rec = items[0]
    if dest is None:
        if source_settings(kb.cfg.raw, SOURCE_TYPE).get("new", "clone") == "ask":
            dest = ASK(rec, suggested_dir(kb, rec)) if ASK else None
            if dest is None:
                raise KBError(f"DataLad dataset {rec.data.get('title')} ({rec.id}) is not on this machine; "
                              f"clone it with kb source clone {rec.id} [--to DIR], or tell kb where it is with "
                              f"kb source link {rec.id} PATH")
        else:
            dest = suggested_dir(kb, rec)
    origins = [p["path"] for r in items for p in r.data.get("paths", [])
               if p.get("host") == kb.cfg.host and Path(p["path"]).is_dir()]
    origins += [u for r in items for u in r.data.get("urls", [])]
    root = _clone_from(kb, list(dict.fromkeys(origins)), dest, rec.data.get("title", ""))
    got = dataset_id(root)
    if got != uuid:
        raise KBError(f"cloned {root}, but it is dataset {got}, not {uuid} ({rec.id} lists a wrong URL?)")
    remember(uuid, root)
    return root


def _clone_from(kb: Store, origins: list[str], dest: Path | None, name: str) -> Path:
    need()
    if not origins:
        raise KBError("no URL or local path to clone the dataset from (kb source add URL adds one)")
    tmp = dest is None
    if tmp:     # the dataset ID is not known before cloning: clone, then name it
        base = source_dir(kb.cfg.raw, SOURCE_TYPE)
        base.mkdir(parents=True, exist_ok=True)
        dest = base / f".cloning-{os.getpid()}"
    dest = Path(dest).expanduser()
    if dest.exists() and any(dest.iterdir()):
        raise KBError(f"{dest} exists and is not empty")
    import datalad.api as dl
    errors = []
    for o in origins:
        try:
            dl.clone(source=o, path=str(dest), result_renderer="disabled")
            break
        except Exception as exc:          # datalad raises its own types; report and try the next origin
            errors.append(f"{o}: {str(exc).splitlines()[0] if str(exc) else type(exc).__name__}")
    else:
        raise KBError("could not clone the dataset: " + "; ".join(errors))
    if tmp:
        final = dest.parent / f"{_slug(name)}-{(dataset_id(dest) or 'unknown')[:8]}"
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
    return _clone(kb, rec.data["source_id"], dest), True


def link(kb: Store, ref: str, path: Path) -> Path:
    """Tell kb that PATH is a clone of source REF (checked), for every store of this user."""
    rec = source_item(kb, ref)
    path = Path(path).expanduser().resolve()
    got = dataset_id(path)
    if got != rec.data["source_id"]:
        raise KBError(f"{path} is " + (f"dataset {got}" if got else "not a DataLad dataset")
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
    p = source_dir(kb.cfg.raw, SOURCE_TYPE) / "blobs" / tf.blob[:2] / tf.blob / Path(tf.path).name
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


def report(kb: Store, rec: Item) -> dict:
    """A source item and its state on this machine: where its clone is, which items use it."""
    root = locate(kb, rec)
    used = [r[0] for r in kb.conn.execute("SELECT DISTINCT src FROM refs WHERE dst = ? AND rel = 'source'", (rec.id,))]
    same = [r.id for r in sources_for(kb, rec.data["source_id"]) if r.id != rec.id]
    return {"id": rec.id, "title": rec.data.get("title"), "type": rec.data.get("source_type"),
            "source_id": rec.data.get("source_id"), "urls": rec.data.get("urls", []),
            "paths": rec.data.get("paths", []), "clone": str(root) if root else None,
            "used_by": used, "same_as": same}


def report_all(kb: Store) -> list[dict]:
    return [report(kb, r) for r in kb.items.values() if r.kind == "source"]
