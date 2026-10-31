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
                        derived_from=(), license=None, sets=()) -> dict:
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
    """Validate and write a brand-new item."""
    data.setdefault("summary", data.get("title", ""))
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


def add(kb: Store, kind: str, sources: list[str], fields: dict, *, mode: str = "auto",
        cas_name: str | None = None, url: str | None = None, fetch: bool = True,
        force: bool = False, via: str | None = None) -> Item:
    """A primary item whose originals are *sources* (paths or URLs)."""
    if not sources:
        raise KBError("nothing to add: give one or more files or URLs")
    if url and len(sources) != 1:
        raise KBError("--url names the canonical location of a single file")
    ids = fields.get("identifiers", {})
    if ids and not force:
        dup = find_by_identifier(kb, ids)
        if dup:
            raise KBError(f"already in this store as {', '.join(dup)} (same identifier); use --force to add anyway")
    data = deep_merge(_skeleton(kb, kind, via=via), fields)
    with kb.tx():
        renditions = []
        used: set[str] = set()
        for src in sources:
            is_url = "://" in src
            filename = _safe_name(Path(src.split("?", 1)[0].rstrip("/")).name)
            name = "original" if len(sources) == 1 else _safe_name(Path(filename).stem)
            while name in used:
                name += "_"
            used.add(name)
            r = {"name": name, "role": "original", "media_type": content.media_type(filename), "locations": []}
            if is_url:
                r["locations"].append(content.url_location(src, retrieved=fetch))
                if fetch:
                    path, sha, size = content.fetch(kb, src)
                    m = mode if mode != "auto" else ("db" if size <= kb.cfg.max_inline_bytes
                                                      else ("cas" if cas_name else "none"))
                    locs, sha, size = content.place(kb, path, filename, m, cas_name)
                    r.update(sha256=sha, size=size)
                    r["locations"] += locs
            else:
                path = Path(src).expanduser()
                if not path.is_file():
                    raise KBError(f"no such file: {src}")
                locs, sha, size = content.place(kb, path, filename, mode, cas_name)
                r.update(sha256=sha, size=size, locations=locs)
                if url:
                    r["locations"].append(content.url_location(url, retrieved=False))
            if not r["locations"]:
                raise KBError(f"{src}: store mode 'none' needs a URL (--url) to say where it lives")
            renditions.append(r)
        data["renditions"] = renditions + data.get("renditions", [])
        return _finish(kb, data)


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
