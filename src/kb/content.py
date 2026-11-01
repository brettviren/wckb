"""Content of renditions: hashing, media types, placing new content, fetching.

Where bytes live is the business of data sources (``kb.sources``); this
module decides where *new* content goes and offers the common helpers.
"""

import hashlib
import mimetypes
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import locations
from .errors import KBError
from .items import now

_EXTRA_TYPES = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".org": "text/x-org", ".rst": "text/x-rst",
    ".tex": "text/x-tex", ".csv": "text/csv", ".tsv": "text/tab-separated-values",
    ".jsonl": "application/jsonl", ".json": "application/json", ".yaml": "application/yaml",
    ".yml": "application/yaml", ".toml": "application/toml", ".root": "application/x-root",
    ".h5": "application/x-hdf5", ".hdf5": "application/x-hdf5", ".parquet": "application/vnd.apache.parquet",
    ".ipynb": "application/x-ipynb+json", ".qmd": "text/markdown", ".py": "text/x-python",
    ".sh": "text/x-shellscript", ".smk": "text/x-python",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
}
_NAMED = {"Makefile": "text/x-makefile", "Snakefile": "text/x-python"}


def media_type(name: str) -> str:
    base = Path(name.split("?", 1)[0]).name
    if base in _NAMED:
        return _NAMED[base]
    suffix = Path(base).suffix.lower()
    if suffix in _EXTRA_TYPES:
        return _EXTRA_TYPES[suffix]
    guess, _ = mimetypes.guess_type(base)
    return guess or "application/octet-stream"


#: Leading bytes of kinds of file whose name may not say what they are.
_MAGIC = ((b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
          (b"GIF8", "image/gif"), (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff"))


def sniff(path: Path, name: str) -> str:
    """The media type of the file at PATH: by NAME, else (when that says nothing) by its first bytes."""
    mtype = media_type(name)
    if mtype != "application/octet-stream":
        return mtype
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return mtype
    return next((t for magic, t in _MAGIC if head.startswith(magic)), mtype)


def is_text(mtype: str) -> bool:
    return mtype.startswith("text/") or mtype in (
        "application/json", "application/jsonl", "application/yaml", "application/toml")


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


blob_path = locations.blob_path
cache_path = locations.cache_path


def cas_root(kb, name: str) -> Path:
    if name not in kb.cfg.cas:
        raise KBError(f"no content store {name!r} in this store's settings (kb config set cas.NAME '{"path": ...}')")
    root = kb.cfg.cas[name]
    if root is None:
        raise KBError(f"content store {name!r} has no path on this machine; set it in .kb/local.toml")
    return root


def put_cas(kb, name: str, path: Path, sha: str) -> Path:
    dest = blob_path(cas_root(kb, name), sha)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".partial")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)
    return dest


def default_cas(kb) -> str | None:
    usable = [n for n, p in kb.cfg.cas.items() if p is not None]
    if "default" in usable:
        return "default"
    return usable[0] if len(usable) == 1 else None


def fetch(kb, url: str, timeout: float = 60) -> tuple[Path, str, int]:
    """Download *url* into the url source directory (content-addressed); return (path, sha256, size)."""
    from .config import source_dir
    tmpdir = source_dir(kb.cfg.raw, "url")
    tmpdir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, \
                tempfile.NamedTemporaryFile(dir=tmpdir, delete=False) as tmp:
            shutil.copyfileobj(resp, tmp)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise KBError(f"cannot fetch {url}: {exc}") from exc
    tmp_path = Path(tmp.name)
    sha, size = sha256_file(tmp_path)
    dest = locations.url_cache_path(kb, sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.replace(dest)
    return dest, sha, size


def resolve(kb, rec, rendition: dict, fetch_urls: bool = True, verify: bool = False) -> Path | None:
    return locations.resolve(kb, rec, rendition, fetch=fetch_urls)


PLACE_MODES = ("auto", "db", "cas", "content", "local", "datalad", "none")

#: The store setting naming its content dataset (a datalad source item).
CONTENT_SETTING = "content"


def place(kb, src: Path, filename: str, mode: str = "auto", cas: str | None = None) -> tuple[list[dict], str, int]:
    """Put *src* where *mode* says; return (locations, sha256, size).

    ``auto``: a file committed in a DataLad dataset stays there (when DataLad
    support is installed); otherwise small files go in the store's database,
    large ones in the content-addressed store if there is one, else in the
    store's content dataset (made on first need, when git-annex is here),
    else are recorded as local to this host.  ``datalad`` insists on the
    dataset the file is in; ``content`` on the content dataset.  ``none``
    records only the hash (the content lives at a URL).
    """
    if mode not in PLACE_MODES:
        raise KBError(f"--place must be one of {', '.join(PLACE_MODES)}")
    sha, size = sha256_file(src)
    if mode in ("auto", "datalad"):
        loc = _datalad_location(kb, src, required=mode == "datalad")
        if loc is not None:
            return [loc], sha, size
    if mode == "auto":
        if size <= kb.cfg.max_inline_bytes:
            mode = "db"
        elif cas or default_cas(kb):
            mode = "cas"
        elif content_dataset_usable(kb):
            mode = "content"
        else:
            mode = "local"
    if mode == "content":
        return [put_content(kb, src, filename, sha)], sha, size
    if mode == "db":
        if size > kb.cfg.max_inline_bytes:
            raise KBError(f"{src} is {size} bytes, over the {kb.cfg.max_inline_bytes} byte limit for the database; "
                          "use --store cas or a URL")
        kb.put_blob(src.read_bytes())
        return [{"type": "db", "filename": filename}], sha, size
    if mode == "cas":
        name = cas or default_cas(kb)
        if not name:
            raise KBError("no content-addressed store configured (kb config set cas.default '{\"path\": ...}')")
        put_cas(kb, name, src, sha)
        return [{"type": "cas", "name": name, "filename": filename}], sha, size
    if mode == "local":
        return [{"type": "local", "host": kb.cfg.host, "path": str(src.resolve())}], sha, size
    return [], sha, size


# -- the content dataset ------------------------------------------------------------------------
# A store's home for large content that has no other: an ordinary DataLad dataset, described by a
# datalad source item that the setting content.dataset names.  By default it is content/ beside
# kb.sqlite (setting content.path says elsewhere), made on first need.  Files go in by sha256.

def content_dataset(kb, create: bool = False, path: Path | None = None, shared: bool = False,
                    group: str | None = None):
    """(source item, directory here) of the store's content dataset, or None if it has none here; with
    CREATE, made (at PATH, else content.path, else content/ beside kb.sqlite) or adopted if missing."""
    from . import datasets
    conf = kb.setting(CONTENT_SETTING) or {}
    ref = conf.get("dataset")
    if ref and ref in kb.items and path is None:
        root = datasets.locate(kb, kb.items[ref])
        if root is not None or not create:
            return (kb.items[ref], root) if root is not None else None
    if not create:
        return None
    datasets.need()
    where = Path(path or conf.get("path") or kb.root / "content").expanduser().absolute()
    if not (where / ".git").exists():
        from .sharing import create as make
        make(where, group, shared=shared, text2git=False)
    elif shared:
        from .sharing import share
        share(where, group)
    where = where.resolve()
    uuid = datasets.identity(where)
    have = datasets.sources_for(kb, uuid) if uuid else []
    rec = have[0] if have else datasets.describe(
        kb, [str(where)], title=f"{kb.cfg.name} content",
        summary=f"The content dataset of the store {kb.cfg.name}: large files kb keeps for it, by sha256.")
    new = {**conf, "dataset": rec.id, **({"path": str(where)} if path else {})}
    if new != conf:
        kb.set_setting(CONTENT_SETTING, new)
    return rec, where


def content_dataset_usable(kb) -> bool:
    """Can large content go to a content dataset here (there is one, or one can be made)?"""
    from . import datasets
    if kb.readonly:
        return False
    return content_dataset(kb) is not None or datasets.available() is None


def put_content(kb, src: Path, filename: str, sha: str) -> dict:
    """Copy SRC into the content dataset (made if needed) by its sha256, save it; its datalad location."""
    from .sources import TYPES
    rec, root = content_dataset(kb, create=True)
    rel = f"sha256/{sha[:2]}/{sha}/{filename}"
    dest = root / rel
    if not os.path.lexists(dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return TYPES["datalad"].put(kb, rec, root, rel, f"Add {filename} ({sha[:12]}) (kb)")


def _datalad_location(kb, src: Path, required: bool) -> dict | None:
    """A datalad location for SRC if it is committed, unchanged, in a DataLad dataset."""
    from . import datasets
    root = datasets.dataset_root(src.parent)
    if root is None:
        if required:
            raise KBError(f"{src} is not inside a DataLad dataset")
        return None
    if datasets.available() is not None:
        if required:
            datasets.need()
        return None
    rel = (src.parent.resolve() / src.name).relative_to(root).as_posix()   # annexed files are symlinks: keep the name
    tf = datasets.tracked(root, rel)
    if tf is None:
        if required:
            raise KBError(f"{rel} is not committed (or has changed) in the dataset at {root}: datalad save it first")
        return None
    return dataset_location(kb, root, rel, tf)


def dataset_location(kb, root: Path, rel: str, tf=None) -> dict:
    """The datalad location of the committed file REL of the dataset at ROOT (its source item made if needed)."""
    from . import datasets, indico
    tf = tf or datasets.tracked(root, rel)
    if tf is None:
        raise KBError(f"{rel} is not committed (or has changed) in the dataset at {root}")
    srcitem = datasets.ensure_source(kb, root, "indico" if indico.sites(root) else "datalad")
    loc = {"type": "datalad", "source": srcitem.id, "path": rel}
    if tf.key:
        loc["key"] = tf.key
    return loc


def place_bytes(kb, data: bytes, filename: str) -> tuple[list[dict], str, int]:
    """Small content straight into the database."""
    if len(data) > kb.cfg.max_inline_bytes:
        with tempfile.NamedTemporaryFile(delete=False) as t:
            t.write(data)
        try:
            return place(kb, Path(t.name), filename, "auto")
        finally:
            Path(t.name).unlink(missing_ok=True)
    sha, size = kb.put_blob(data)
    return [{"type": "db", "filename": filename}], sha, size


def url_location(url: str, retrieved: bool) -> dict:
    loc = {"type": "url", "url": url}
    if retrieved:
        loc["retrieved"] = now()
    return loc


def verify(kb, recs, record: bool = False) -> list[dict]:
    """Re-fetch URL originals and compare hashes; with *item*, fill in missing hashes."""
    report = []
    for rec in recs:
        dirty = False
        for r in rec.renditions:
            for loc in r.get("locations", []):
                if loc["type"] != "url":
                    continue
                item = {"ref": f"{rec.id}#{r['name']}", "url": loc["url"]}
                try:
                    _, sha, size = fetch(kb, loc["url"])
                except KBError as exc:
                    report.append(item | {"result": "unreachable", "detail": str(exc)})
                    continue
                if r.get("sha256") is None:
                    if record:
                        r["sha256"], r["size"] = sha, size
                        loc["retrieved"] = now()
                        dirty = True
                        report.append(item | {"result": "recorded", "sha256": sha})
                    else:
                        report.append(item | {"result": "unhashed", "sha256": sha})
                elif sha == r["sha256"]:
                    report.append(item | {"result": "ok"})
                else:
                    report.append(item | {"result": "changed", "sha256": sha,
                                          "detail": f"recorded {r['sha256'][:12]}…, now {sha[:12]}…; "
                                                    f"add the new version with kb add --supersedes {rec.id}"})
        if dirty:
            kb.save(rec, "verify", {"recorded": True})
    return report
