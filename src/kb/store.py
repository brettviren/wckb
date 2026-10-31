"""Content of renditions: hashing, media types, placing new content, fetching.

Where bytes live is the business of data sources (``kb.sources``); this
module decides where *new* content goes and offers the common helpers.
"""

import hashlib
import mimetypes
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from . import sources
from .errors import KBError
from .records import now

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


blob_path = sources.blob_path
cache_path = sources.cache_path


def store_root(kb, name: str) -> Path:
    if name not in kb.cfg.stores:
        raise KBError(f"no content store {name!r} in this store's settings (kb config set stores ...)")
    root = kb.cfg.stores[name]
    if root is None:
        raise KBError(f"content store {name!r} has no path on this machine; set it in .kb/local.toml")
    return root


def put_cas(kb, name: str, path: Path, sha: str) -> Path:
    dest = blob_path(store_root(kb, name), sha)
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".partial")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)
    return dest


def default_store(kb) -> str | None:
    usable = [n for n, p in kb.cfg.stores.items() if p is not None]
    if "default" in usable:
        return "default"
    return usable[0] if len(usable) == 1 else None


def fetch(kb, url: str, timeout: float = 60) -> tuple[Path, str, int]:
    """Download *url* into the cache; return (path, sha256, size)."""
    kb.cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp, \
                tempfile.NamedTemporaryFile(dir=kb.cfg.cache_dir, delete=False) as tmp:
            shutil.copyfileobj(resp, tmp)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise KBError(f"cannot fetch {url}: {exc}") from exc
    tmp_path = Path(tmp.name)
    sha, size = sha256_file(tmp_path)
    dest = cache_path(kb, sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.replace(dest)
    return dest, sha, size


def resolve(kb, rec, rendition: dict, fetch_urls: bool = True, verify: bool = False) -> Path | None:
    return sources.resolve(kb, rec, rendition, fetch=fetch_urls)


STORE_MODES = ("auto", "db", "cas", "local", "none")


def place(kb, src: Path, filename: str, mode: str = "auto", store: str | None = None) -> tuple[list[dict], str, int]:
    """Put *src* where *mode* says; return (locations, sha256, size).

    ``auto``: small files in the store's database, large ones in the
    content-addressed store if there is one, else recorded as local to this
    host.  ``none`` records only the hash (the content lives at a URL).
    """
    if mode not in STORE_MODES:
        raise KBError(f"store mode must be one of {', '.join(STORE_MODES)}")
    sha, size = sha256_file(src)
    if mode == "auto":
        if size <= kb.cfg.max_inline_bytes:
            mode = "db"
        elif store or default_store(kb):
            mode = "cas"
        else:
            mode = "local"
    if mode == "db":
        if size > kb.cfg.max_inline_bytes:
            raise KBError(f"{src} is {size} bytes, over the {kb.cfg.max_inline_bytes} byte limit for the database; "
                          "use --store cas or a URL")
        kb.put_blob(src.read_bytes())
        return [{"type": "db", "filename": filename}], sha, size
    if mode == "cas":
        name = store or default_store(kb)
        if not name:
            raise KBError("no content-addressed store configured (kb config set stores '{\"default\": {\"path\": ...}}')")
        put_cas(kb, name, src, sha)
        return [{"type": "cas", "store": name}], sha, size
    if mode == "local":
        return [{"type": "local", "host": kb.cfg.host, "path": str(src.resolve())}], sha, size
    return [], sha, size


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
    """Re-fetch URL originals and compare hashes; with *record*, fill in missing hashes."""
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
