"""Opening an item's file with the application the desktop associates with its type (``kb open``).

Which file: an item's original by default (for a search hit in extracted
text, the file the text came from; for a web page, its self-contained copy
``page`` when it has one and is not in a source's tree), its text with ``text`` (the original
itself when that is text, else the text extracted from it), or a rendition
named ``ID#NAME``.  Content not here is fetched or got first (a URL
downloaded, ``datalad get`` in a dataset).

A file in a source (a DataLad dataset, a git or filesystem tree) is opened
where it is in that tree, so a web page finds its scripts, styles and data
by their relative paths (an HTML artifact: decisions/0004); for a web page in
a DataLad dataset, its whole directory is got first.  Files kept by hash (a
content store, the download cache) have no name; the application is given a
link with the file's own name, so it can tell the type.  The application is ``open.command`` (a list; ``{path}`` is replaced,
else the path is appended), else ``xdg-open`` (Linux), ``open`` (macOS) or
the Windows association.
"""

import logging
import mimetypes
import os
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from . import content
from .errors import KBError
from .ids import parse_ref
from .items import Item, Store

log = logging.getLogger(__name__)


def choose(rec: Item, name: str | None = None, text: bool = False) -> dict:
    """The rendition to open: NAME, else the item's original (else its content, else its first);
    with TEXT, that rendition's text instead."""
    if name:
        r = rec.rendition(name)
    else:
        order = sorted(rec.renditions, key=lambda r: (r["role"] != "original", r["name"] != "original",
                                                      r["role"] != "content"))
        if not order:
            raise KBError(f"{rec.id} has no renditions: nothing to open")
        r = order[0]
        if r["role"] == "derived" and r.get("derived_from") and rec.has_rendition(r["derived_from"]):
            r = rec.rendition(r["derived_from"])
        if not text:
            r = _page_of(rec, r)
    if not text or content.is_text(r["media_type"]):
        return r
    derived = [d for d in rec.renditions if d["role"] == "derived" and d.get("derived_from") == r["name"]
               and content.is_text(d["media_type"])]
    if not derived:
        raise KBError(f"{rec.id}#{r['name']} has no text extracted from it (kb rendition extract {rec.id})")
    return next((d for d in derived if d["name"] in ("text", f"{r['name']}-text")), derived[0])


def _page_of(rec: Item, r: dict) -> dict:
    """The self-contained copy of web page R (a page transform: transforms.Monolith), when it has one and R is not
    in a source's tree, where the page finds what it loads by relative paths and is current."""
    if r["media_type"] not in content.MARKUP or any(loc["type"] in ("datalad", "source")
                                                    for loc in r.get("locations", [])):
        return r
    pages = [d for d in rec.renditions if d["role"] == "derived" and d.get("derived_from") == r["name"]
             and d["media_type"] in content.MARKUP]
    return pages[0] if pages else r


def filename(rec: Item, r: dict) -> str:
    """The file's own name: as the store, a URL or a dataset has it, else the rendition's name and type."""
    for loc in r.get("locations", []):
        if loc.get("filename"):
            return PurePosixPath(loc["filename"]).name
        if loc["type"] in ("datalad", "source") and loc.get("path"):
            return PurePosixPath(loc["path"]).name
        if loc["type"] == "url":
            name = PurePosixPath(unquote(urlsplit(loc["url"]).path)).name
            if PurePosixPath(name).suffix:
                return name
    ext = mimetypes.guess_extension(r["media_type"]) or ""
    return f"{rec.id}-{r['name']}{ext}"


def path(kb: Store, ref: str, text: bool = False, fetch: bool = True) -> tuple[Item, dict, Path]:
    """(item, rendition, a local file with its content and its own name), fetched or got if need be."""
    ident, name = parse_ref(ref)
    rec = kb.get(ident)
    r = choose(rec, name, text)
    p = in_place(kb, r, fetch) or content.resolve(kb, rec, r, fetch_urls=fetch)
    if p is None:
        raise KBError(f"{rec.id}#{r['name']} is not reachable from here"
                      + ("" if fetch else " without fetching"))
    want = filename(rec, r)
    if p.name != want:      # kept by hash: give the application the file's own name
        link = kb.cfg.cache_dir / "open" / (r.get("sha256") or rec.id) / want
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(p.resolve())
        p = link
    return rec, r, p


def in_place(kb: Store, r: dict, fetch: bool) -> Path | None:
    """The rendition's file where it is in a source's tree (with its neighbours), if it is in one here."""
    from .sources import TYPES
    for loc in r.get("locations", []):
        if loc["type"] not in ("datalad", "source") or loc.get("source") not in kb.items:
            continue
        src = kb.items[loc["source"]]
        t = TYPES.get(src.data.get("source_type"))
        root = t.locate(kb, src) if t else None
        if root is None and fetch and loc["type"] == "datalad":
            from . import datasets
            root = datasets.locate(kb, src, clone=True)
        if root is None:
            continue
        p = Path(root) / loc["path"]
        if loc["type"] == "datalad" and fetch and r.get("media_type") in ("text/html", "application/xhtml+xml"):
            _get(Path(root), p.parent)             # the page and what it loads from beside it
        elif loc["type"] == "datalad" and fetch and not p.exists():
            from . import datasets
            datasets.get(Path(root), loc["path"])
        if p.exists():
            sha = r.get("sha256")
            if sha and content.sha256_file(p)[0] != sha:
                continue                            # the tree has other bytes there now
            return p
    return None


def _get(root: Path, directory: Path) -> None:
    """datalad get a directory of a dataset (all of it), quietly; what can't be got is left."""
    from . import datasets
    if datasets.available() is not None:
        return
    import datalad.api as dl
    try:
        dl.get(path=str(directory), dataset=str(root), recursive=True, result_renderer="disabled",
               on_failure="ignore")
    except Exception as exc:                        # DataLad's own errors: open what is there
        log.warning("datalad get %s: %s", directory, exc)


def command(kb: Store, file: Path) -> list[str] | None:
    """The command that opens FILE (None on Windows, where the association is used directly)."""
    conf = (kb.cfg.raw.get("open") or {}).get("command")
    if conf:
        argv = [str(a) for a in ([conf] if isinstance(conf, str) else conf)]
        return ([a.replace("{path}", str(file)) for a in argv] if any("{path}" in a for a in argv)
                else argv + [str(file)])
    if sys.platform == "darwin":
        return ["open", str(file)]
    if sys.platform.startswith("win"):
        return None
    if shutil.which("xdg-open"):
        return ["xdg-open", str(file)]
    raise KBError("no way to open files here: install xdg-utils (xdg-open), or set open.command in your config")


def launch(kb: Store, file: Path) -> None:
    """Open FILE with its application, in the background (kb does not wait for it)."""
    argv = command(kb, file)
    if argv is None:
        os.startfile(file)            # type: ignore[attr-defined]  # Windows only
        return
    log.debug("open: %s", " ".join(argv))
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError as exc:
        raise KBError(f"cannot run {argv[0]}: {exc}") from None
