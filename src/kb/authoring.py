"""Making items: new, add, supersede, retract.

These functions write the plumbing (ID, timestamps, hashes, locations) so
that people and LLMs only supply meaning.  Every item is validated before
it is written, in one transaction with its content; nothing invalid reaches
the store through them.
"""

import json
import re
from pathlib import Path

from . import schema, content
from .errors import KBError
from .ids import new_id, parse_ref
from .items import Store, Item, now

CONTENT_NAME = {"topic": "page"}  # content rendition name; "note" otherwise

#: What a qualified date (field ``dates``) can say, as in schemas/v1/item.json.
DATE_QUALIFIERS = {
    "created": "when it was made (written, drawn, produced)",
    "published": "when it was made public (released, posted, presented)",
    "modified": "when it last changed",
    "accessed": "when it was read or downloaded for this item",
    "captured": "when a photo or recording was taken",
}


# -- turning options into fields -------------------------------------------

def parse_runs(specs) -> list[list[int]]:
    """'1234', '1200-1300' or '1200:1300' -> [[first, last], ...]."""
    out = []
    for spec in specs:
        for part in str(spec).split(","):
            m = re.fullmatch(r"\s*(\d+)\s*(?:[-:]\s*(\d+))?\s*", part)
            if not m:
                raise KBError(f"bad run range {part!r}: use N or FIRST-LAST")
            first, last = int(m[1]), int(m[2] or m[1])
            if last < first:
                raise KBError(f"bad run range {part!r}: last before first")
            out.append([first, last])
    return out


def parse_set(items) -> dict:
    """``key=value`` pairs; value is JSON if it parses, else a string; dotted keys nest."""
    out: dict = {}
    for item in items:
        if "=" not in item:
            raise KBError(f"--set wants key=value, got {item!r}")
        key, value = item.split("=", 1)
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            pass
        d = out
        *parents, last = key.split(".")
        for p in parents:
            d = d.setdefault(p, {})
        d[last] = value
    return out


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def fields_from_options(*, title=None, summary=None, subject=(), tags=(), runs=(), start=None,
                        end=None, status=None, access=None, by=None, how=None, when=None,
                        creators=(), date=None, identifiers=None, evidence=(), related=(),
                        derived_from=(), license=None, sets=(), dated=(), links=()) -> dict:
    f: dict = {}
    if title: f["title"] = title
    if summary: f["summary"] = summary
    if subject: f["subject"] = list(dict.fromkeys(subject))
    if tags: f["tags"] = list(dict.fromkeys(tags))
    valid = {}
    if runs: valid["runs"] = parse_runs(runs)
    if start or end:
        valid["time"] = {k: v for k, v in (("start", start), ("end", end)) if v}
    if valid: f["valid"] = valid
    if status: f["status"] = status
    if access: f["access"] = access
    if by or how or when:
        a = {"who": by or "unknown"}
        if how: a["how"] = how
        if when: a["when"] = when
        f["asserted_by"] = [a]
    if creators: f["creators"] = list(creators)
    if date: f["date"] = date
    if identifiers: f["identifiers"] = dict(identifiers)
    if evidence: f["evidence"] = list(evidence)
    if related: f["related"] = list(related)
    if derived_from: f["derived_from"] = list(derived_from)
    if license: f["license"] = license
    for q in dated:
        k, eq, v = q.partition("=")
        if not eq or k not in DATE_QUALIFIERS:
            raise KBError(f"--dated wants QUALIFIER=DATE, QUALIFIER one of {', '.join(DATE_QUALIFIERS)}: {q!r}")
        f.setdefault("dates", {})[k] = v
    for x in links:
        from .graph import RELATIONS
        rel, eq, ref = x.partition("=")
        if not eq or rel not in RELATIONS:
            raise KBError(f"--link wants RELATION=ID, RELATION one of {', '.join(RELATIONS)}: {x!r}")
        f.setdefault("links", []).append({"rel": rel, "ref": ref})
    return deep_merge(f, parse_set(sets))


# -- helpers ---------------------------------------------------------------

def _skeleton(kb: Store, kind: str, slug: str | None = None, via: str | None = None) -> dict:
    data = {
        "schema_version": schema.MAJOR,
        "id": new_id(kind, slug),
        "kind": kind,
        "created": {"at": now(), "by": kb.cfg.user},
        "access": kb.cfg.access,
    }
    if via:
        data["created"]["via"] = via
    from .config import coauthors
    if coauthors():
        data["created"]["with"] = coauthors()
    if kind == "observation":
        data["status"] = "unverified"
    return data


def _content_rendition(kb: Store, kind: str, text: str, content_file: tuple[str, str] | None = None) -> dict:
    name, filename = content_file or (CONTENT_NAME.get(kind, "note"), f"{CONTENT_NAME.get(kind, 'note')}.md")
    data = (text if text.endswith("\n") else text + "\n").encode()
    locs, sha, size = content.place_bytes(kb, data, filename)
    return {"name": name, "role": "content", "media_type": content.media_type(filename),
            "sha256": sha, "size": size, "locations": locs}


def _known(kb: Store, ref: str) -> bool:
    """In this store, or in a store it can reach (references are store-independent)."""
    if kb.exists(ref):
        return True
    from .federation import locate
    from .ids import parse_ref
    return locate(kb, parse_ref(ref)[0]) is not None


def _finish(kb: Store, data: dict, allow_dangling: bool = False) -> Item:
    """Validate and write a brand-new item; its inputs are pinned as they are now (a view's by its build)."""
    data.setdefault("summary", data.get("title", ""))
    if data.get("derived_from") and data.get("kind") != "view":
        from .graph import pin
        data["derived_from"] = pin(kb, data["derived_from"])
    errors = schema.validate(data)
    if not allow_dangling:
        errors += [f"{field}: no item {ref} here or in any reachable store (--allow-dangling if intended)"
                   for field, ref in _refs(data) if not _known(kb, ref)]
    if errors:
        raise KBError("item not written:\n  " + "\n  ".join(errors))
    return kb.write_new(data)


def _refs(data: dict):
    for field in ("supersedes", "retracts", "related", "members"):
        for ref in data.get(field, []):
            yield field, ref
    for x in data.get("links", []):
        yield "links", x["ref"]
    for d in data.get("derived_from", []):
        yield "derived_from", d["ref"] if isinstance(d, dict) else d
    for ev in data.get("evidence", []):
        if "://" not in ev:
            yield "evidence", ev


def new(kb: Store, kind: str, fields: dict, text: str | None = None, slug: str | None = None,
        via: str | None = None, content_file: tuple[str, str] | None = None,
        allow_dangling: bool = False) -> Item:
    """An item from *fields*, with *text* as its content rendition
    (named and filed as *content_file* = (name, filename) if given)."""
    data = deep_merge(_skeleton(kb, kind, slug, via), fields)
    if kind == "person" and data.get("name"):      # a person needs little more than a name
        if not data.get("title"):
            data["title"] = data["name"]
        if not data.get("summary"):
            data["summary"] = data["name"] + (f", {data['affiliation']}." if data.get("affiliation") else ".")
    for k in ("id", "kind", "schema_version"):
        if k in fields and fields[k] != data[k]:
            raise KBError(f"{k} is assigned by kb and cannot be given")
    with kb.tx():
        if text is not None:
            data.setdefault("renditions", []).insert(0, _content_rendition(kb, kind, text, content_file))
        return _finish(kb, data, allow_dangling)


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return name or "file"


def find_by_identifier(kb: Store, identifiers: dict) -> list[str]:
    hits = []
    for rec in kb.items.values():
        theirs = rec.data.get("identifiers", {})
        if any(theirs.get(k) == v for k, v in identifiers.items() if k != "url"):
            hits.append(rec.id)
    return hits


#: Fields found in files that only documents have.
_DOCUMENT_ONLY = ("doctype", "venue", "version", "event")


def add(kb: Store, kind: str, sources: list[str], fields: dict, *, mode: str = "auto",
        cas_name: str | None = None, url: str | None = None, fetch: bool = True,
        force: bool = False, via: str | None = None, target: str | Path | None = None,
        guess: bool = True) -> Item:
    """A primary item whose originals are *sources* (paths or URLs).

    TARGET: copy each file there (a directory, or the file's new path when there is one source).  If it
    lands in a source (a DataLad dataset, a git repository or filesystem tree described here), the
    source's type takes it in (datalad save, git commit) and the rendition names the source.
    GUESS: fill in metadata found in the files and at arXiv (kb.metadata); FIELDS always win."""
    from . import metadata
    if not sources:
        raise KBError("nothing to add: give one or more files or URLs")
    if url and len(sources) != 1:
        raise KBError("--url names the canonical location of a single file")
    if target and not fetch and any("://" in s for s in sources):
        raise KBError("--target copies the content, so URLs must be fetched (drop --no-fetch)")
    found = metadata.Found()
    if guess:
        for src in sources:
            found.take(metadata.for_source(src))
        found.take(metadata.for_identifiers(fields.get("identifiers", {})))
    sources = [found.instead.get(s, s) for s in sources]
    known = {**found.fields.get("identifiers", {}), **fields.get("identifiers", {})}
    if known and not force and find_by_identifier(kb, known):     # before anything is copied or committed
        raise KBError(f"already in this store as {', '.join(find_by_identifier(kb, known))} (same identifier); "
                      "use --force to add anyway")
    with kb.tx():
        # first what each source is (fetched, its type, what it says of itself), so a duplicate is refused
        # before anything is copied or committed; then where its content goes
        got = []
        used: set[str] = set()
        for src in sources:
            is_url = "://" in src
            filename = _safe_name(Path(src.split("?", 1)[0].rstrip("/")).name)
            if is_url and src in found.instead.values() and not filename.endswith(".pdf"):
                filename += ".pdf"
            name = "original" if len(sources) == 1 else _safe_name(Path(filename).stem)
            while name in used:
                name += "_"
            used.add(name)
            path, said, modified, failure = None, None, None, None
            if is_url and fetch:
                try:
                    f = content.fetch_info(kb, src)
                    path, said, modified = f.path, f.media_type, f.last_modified
                except content.FetchError as exc:
                    if exc.status in content.GONE:
                        raise KBError(f"{exc} (the URL is wrong, or the page is gone)") from None
                    failure = exc.record()        # a refusal or a passing failure: keep the URL, say what happened
                    _warn(f"{exc}: added without its content (recorded; kb rendition verify ID retries)")
            elif not is_url:
                path = Path(src).expanduser()
                if not path.is_file():
                    raise KBError(f"no such file: {src}")
            mtype = content.sniff(path, filename, said) if path is not None else content.media_type(filename)
            if guess and path is not None and not got:
                found.take(metadata.for_file(path, mtype, last_modified=modified))
            if is_url and path is not None and not got:     # a fact, not a guess: when kb read it
                found.put("dates.accessed", now()[:10], "kb's download")
            got.append((src, is_url, filename, name, path, mtype, failure))
        guessed = {k: v for k, v in found.fields.items() if kind == "document" or k not in _DOCUMENT_ONLY}
        ids = {**guessed.get("identifiers", {}), **fields.get("identifiers", {})}
        if ids and not force:
            dup = find_by_identifier(kb, ids)
            if dup:
                raise KBError(f"already in this store as {', '.join(dup)} (same identifier); use --force to add anyway")
        renditions = []
        for src, is_url, filename, name, path, mtype, failure in got:
            r = {"name": name, "role": "original", "media_type": mtype, "locations": []}
            if is_url:
                r["locations"].append(content.url_location(src, retrieved=path is not None)
                                      | ({"failed": failure} if failure else {}))
            if target is not None:
                if path is None:
                    raise KBError(f"{src}: nothing to copy to --target (fetching it failed)")
                dest = _copy(path, target, filename, several=len(sources) > 1)
                locs, sha, size = _place_copy(kb, dest, filename, mode, cas_name)
                r.update(sha256=sha, size=size)
                r["locations"] = locs + r["locations"]
            elif path is not None:
                m = mode
                if is_url and mode == "auto":     # a mirror of what the URL gave, wherever large content goes
                    size = path.stat().st_size
                    m = ("db" if size <= kb.cfg.max_inline_bytes else "cas" if cas_name or content.default_cas(kb)
                         else "content" if content.content_dataset_usable(kb) else "none")
                locs, sha, size = content.place(kb, path, filename, m, cas_name)
                r.update(sha256=sha, size=size)
                r["locations"] += locs
            if url and not is_url:
                r["locations"].append(content.url_location(url, retrieved=False))
            if not r["locations"]:
                raise KBError(f"{src}: store mode 'none' needs a URL (--url) to say where it lives")
            renditions.append(r)
        data = deep_merge(deep_merge(_skeleton(kb, kind, via=via), guessed), fields)
        if found.abstract and "abstract" not in used:
            renditions.append(_content_rendition(kb, kind, found.abstract, ("abstract", "abstract.md")))
        data["renditions"] = renditions + data.get("renditions", [])
        _report_guesses(found, fields)
        rec = _finish(kb, data)
        _report_places(kb, renditions)
        return rec


NOTE_TITLE_CHARS = 80
NOTE_SUMMARY_CHARS = 400


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def add_note(kb: Store, text: str, fields: dict, via: str | None = None) -> Item:
    """A brief text note: a document of doctype ``note``, creator the user, dated today, its text the
    ``note`` rendition.  Title and summary default to the note's first line and its text (clipped);
    FIELDS always win."""
    text = text.strip()
    if not text:
        raise KBError("the note is empty")
    first = text.splitlines()[0].strip()
    base = {"doctype": "note", "creators": [kb.cfg.user], "date": now()[:10],
            "title": _clip(first, NOTE_TITLE_CHARS),
            "summary": _clip(" ".join(text.split()), NOTE_SUMMARY_CHARS)}
    return new(kb, "document", deep_merge(base, fields), text, via=via)


def _warn(message: str) -> None:
    import logging
    from .logs import tell
    tell(logging.getLogger(__name__), logging.WARNING, message)


def _copy(path: Path, target: str | Path, filename: str, several: bool) -> Path:
    """Copy PATH to TARGET (a directory: one that exists, or a path ending in /; else the new file's path),
    unless the same bytes are there."""
    import os
    import shutil
    into = str(target).endswith(os.sep)
    target = Path(target).expanduser()
    dest = target / filename if several or into or target.is_dir() else target
    if dest.exists() or dest.is_symlink():
        if dest.resolve() == path.resolve() or (dest.is_file() and
                                                content.sha256_file(dest)[0] == content.sha256_file(path)[0]):
            return dest
        raise KBError(f"{dest} exists, with other content")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, dest)
    return dest


def _place_copy(kb: Store, dest: Path, filename: str, mode: str, cas_name: str | None):
    """Locations for a file kb copied to DEST: the source holding it (which takes it in), else as MODE says,
    noting the copy itself."""
    import logging
    from . import sources
    from .logs import tell
    held = sources.containing(kb, dest)
    sha, size = content.sha256_file(dest)
    if held:
        rec, root = held
        rel = (dest.parent.resolve() / dest.name).relative_to(root).as_posix()
        loc = sources.TYPES[rec.data["source_type"]].put(kb, rec, root, rel, f"Add {rel} (kb add)")
        extra = content.place(kb, dest, filename, mode, cas_name)[0] if mode not in ("auto", "datalad") else []
        return [loc] + extra, sha, size
    from .datasets import _toplevel
    top = _toplevel(dest)
    if top is not None:
        tell(logging.getLogger(__name__), logging.INFO,
             f"{dest} is in the git repository {top}, which is not a source here: copied only "
             f"(kb source add {top} makes it one, and kb add --target then commits into it)")
    locs, sha, size = content.place(kb, dest, filename, mode, cas_name)
    if not any(l["type"] == "local" for l in locs):
        locs.append({"type": "local", "host": kb.cfg.host, "path": str(dest.resolve())})
    return locs, sha, size


_PLACES = {"db": "in the store", "cas": "in a content store", "datalad": "in a DataLad dataset",
           "source": "in a source's tree", "local": "only on this machine", "url": "at its URL",
           "asset": "through an asset"}


def _report_places(kb: Store, renditions: list[dict]) -> None:
    """Say where each file's content went: a person (or an LLM) should not have to look it up."""
    import logging
    from .logs import tell
    mine = (kb.setting(content.CONTENT_SETTING) or {}).get("dataset")
    for r in renditions:
        places = [("the store's content dataset" if loc["type"] == "datalad" and loc.get("source") == mine
                   else _PLACES.get(loc["type"], loc["type"])) for loc in r["locations"]]
        where = " and ".join(dict.fromkeys(places))
        note = "" if "sha256" in r else " (not fetched: no hash yet)"
        tell(logging.getLogger(__name__), logging.INFO,
             f"#{r['name']} ({r['media_type']}): kept {where}{note}")


def _report_guesses(found, fields: dict) -> None:
    import logging
    from .logs import tell
    log = logging.getLogger(__name__)
    kept = {k: w for k, w in found.whence.items()
            if k.split(".")[0] not in fields or (k.startswith("identifiers.")
                                                 and k.split(".", 1)[1] not in fields.get("identifiers", {}))}
    by: dict[str, list[str]] = {}
    for k, w in kept.items():
        by.setdefault(w, []).append(k)
    if by:
        tell(log, logging.INFO, "found " + "; ".join(f"{', '.join(ks)} in {w}" for w, ks in by.items()))
    for note in found.notes:
        tell(log, logging.WARNING, note)


_NOT_INHERITED = {"id", "created", "renditions", "summary", "status", "supersedes", "retracts",
                  "evidence", "derived_from", "asserted_by", "schema_version", "origin"}


def supersede(kb: Store, old_ref: str, fields: dict, text: str | None = None,
              via: str | None = None) -> Item:
    """A corrected version of an item; the old one stops being current."""
    old = kb.get(old_ref)
    if not kb.is_current(old.id):
        raise KBError(f"{old.id} is already superseded by {', '.join(kb.superseded_by[old.id])}; "
                      "supersede the current version instead")
    inherited = {k: v for k, v in old.data.items() if k not in _NOT_INHERITED}
    fields = deep_merge(inherited, fields)
    fields["supersedes"] = [old.id]
    if "asserted_by" in old.data and "asserted_by" not in fields:
        # Whoever records a correction asserts it, unless told otherwise.
        fields["asserted_by"] = [{"who": kb.cfg.user, "when": now()[:10]}]
    if old.kind == "observation" and text is None:
        raise KBError("superseding an observation needs the corrected text (--text or --text-file)")
    return new(kb, old.kind, fields, text=text, via=via)


def retract(kb: Store, old_ref: str, reason: str, fields: dict, via: str | None = None) -> Item:
    """An observation withdrawing an item without replacing it."""
    old = kb.get(old_ref)
    base = {
        "title": f"Retraction: {old.data['title']}"[:200],
        "summary": f"Withdraws {old.id}. {reason}"[:400],
        "retracts": [old.id],
        "asserted_by": [{"who": kb.cfg.user, "when": now()[:10]}],
    }
    for k in ("subject", "valid", "access"):
        if k in old.data:
            base[k] = old.data[k]
    return new(kb, "observation", deep_merge(base, fields), text=reason, via=via)


def from_json(kb: Store, payload: dict, text: str | None = None, via: str | None = None) -> Item:
    """An item from a partial JSON object (kind required); kb fills the plumbing."""
    payload = dict(payload)
    kind = payload.pop("kind", None)
    if not kind:
        raise KBError("JSON input needs a 'kind'")
    text = payload.pop("text", text)
    return new(kb, kind, payload, text=text, via=via)
