"""Knowledge items and the store that holds them.

An item is a JSON object validated by JSON Schema, exactly as before; it now
lives in the ``items`` table of the store's SQLite file (see ``kb.db``).
``Store`` is the store: it loads items, writes them (validating, guarding,
keeping derived tables in step and logging an event), and holds the blobs
that small content lives in.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from functools import cached_property
from pathlib import Path

from . import db as dbm
from .errors import KBError
from .ids import PRIMARY_KINDS, parse_ref

log = logging.getLogger(__name__)

# Fields of a frozen item (source, released policy) that never change: what it asserts.
IMMUTABLE = ("id", "kind", "created", "asserted_by", "creators", "date", "valid", "doctype",
             "system", "query", "retrieved", "parameters", "repository", "commit",
             "members", "depth", "nested", "follow", "asset_type", "match",
             "source_type", "source_id")
POLICY_FROZEN = ("name", "version", "policy_type")
FIXED_ROLES = ("original", "content")

_ORDER = [
    "schema_version", "id", "kind", "title", "summary", "status", "access",
    "subject", "tags", "valid", "created", "asserted_by", "creators", "date", "dates",
    "doctype", "event", "venue", "version", "system", "query", "retrieved",
    "parameters", "repository", "commit", "language", "format", "variables",
    "produced_by", "name", "policy_type", "stage", "applies_to", "order", "based_on", "affiliation",
    "email", "handles", "build", "audience", "members", "depth", "nested", "follow", "purpose",
    "asset_type", "match", "versions", "bindings",
    "identifiers", "license", "supersedes", "retracts", "derived_from", "evidence",
    "related", "links", "origin", "renditions", "x",
]
_DIGEST_EXCLUDE = {"summary", "tags", "related", "links", "dates", "evidence", "x", "origin"}
_DIGEST_ROLES = {"original", "content", "metadata", "source"}


def now(precise: bool = False) -> str:
    t = datetime.now(timezone.utc)
    if not precise:
        t = t.replace(microsecond=0)
    return t.isoformat().replace("+00:00", "Z")


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def ordered(data: dict) -> dict:
    out = {k: data[k] for k in _ORDER if k in data}
    out.update({k: v for k, v in data.items() if k not in out})
    return out


def dumps(data: dict) -> str:
    return json.dumps(ordered(data), indent=2, ensure_ascii=False) + "\n"


def digest(data: dict) -> str:
    """SHA-256 over the parts of an item that define what it means."""
    core = {k: v for k, v in data.items() if k not in _DIGEST_EXCLUDE}
    core["renditions"] = sorted(
        ({"name": r["name"], "sha256": r.get("sha256")}
         for r in data.get("renditions", []) if r.get("role") in _DIGEST_ROLES),
        key=lambda r: r["name"])
    return hashlib.sha256(canonical(core)).hexdigest()


def is_frozen(data: dict) -> bool:
    return data.get("kind") in PRIMARY_KINDS or data.get("kind") in ("asset", "source") or (
        data.get("kind") == "policy" and data.get("stage") in ("released", "deprecated"))


def source_refs(data: dict) -> list[str]:
    """Source items (src-...) an item needs to get its content: from rendition locations and asset bindings."""
    out = [loc["source"] for r in data.get("renditions", []) if isinstance(r, dict)
           for loc in r.get("locations", []) if isinstance(loc, dict) and loc.get("source")]
    out += [b["locator"]["source"] for b in data.get("bindings", [])
            if isinstance(b, dict) and isinstance(b.get("locator"), dict) and b["locator"].get("source")]
    return list(dict.fromkeys(out))


def core(data: dict) -> dict:
    """What a frozen item asserts: never changes once written (hashes are in ``fixed``)."""
    c = {k: data.get(k) for k in IMMUTABLE + POLICY_FROZEN}
    if data.get("kind") not in ("asset", "source"):   # assets gain versions (digests guarded via fixed)
        c["_fixed"] = sorted(fixed(data))
    return c


def fixed(data: dict) -> dict[str, str | None]:
    """Hashes that never change once recorded: of original and content renditions, and, for an
    asset, of each registered version (keyed "version:LABEL")."""
    out = {r["name"]: r.get("sha256") for r in data.get("renditions", []) if r.get("role") in FIXED_ROLES}
    for v in data.get("versions", []) if data.get("kind") == "asset" else []:
        out[f"version:{v['label']}"] = v.get("digest")
    return out


def fixed_compatible(a: dict, b: dict) -> bool:
    fa, fb = fixed(a), fixed(b)
    if a.get("kind") == "asset":     # either side may know more versions; shared ones must agree
        return all(fa[k] is None or fb[k] is None or fa[k] == fb[k] for k in fa.keys() & fb.keys())
    return fa.keys() == fb.keys() and all(fa[k] is None or fb[k] is None or fa[k] == fb[k] for k in fa)


def core_digest(data: dict) -> str:
    return hashlib.sha256(canonical(core(data))).hexdigest()


class Item:
    """One knowledge item."""

    def __init__(self, data: dict, kb: "Store | None" = None, updated_at: str | None = None):
        self.data = data
        self.kb = kb
        self.updated_at = updated_at

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def kind(self) -> str:
        return self.data["kind"]

    @property
    def is_primary(self) -> bool:
        return self.kind in PRIMARY_KINDS

    @property
    def frozen(self) -> bool:
        return is_frozen(self.data)

    @property
    def renditions(self) -> list[dict]:
        return self.data.setdefault("renditions", [])

    def rendition(self, name: str) -> dict:
        for r in self.data.get("renditions", []):
            if r["name"] == name:
                return r
        raise KBError(f"{self.id} has no rendition {name!r}")

    def has_rendition(self, name: str) -> bool:
        return any(r["name"] == name for r in self.data.get("renditions", []))

    def inputs(self) -> list[str]:
        """Refs in derived_from, whether stamped or not."""
        return [d["ref"] if isinstance(d, dict) else d for d in self.data.get("derived_from", [])]

    def digest(self) -> str:
        return digest(self.data)

    def content(self, name: str) -> bytes | None:
        """Bytes of a rendition held in this store's database, else None."""
        r = self.rendition(name)
        if self.kb is None or not any(l["type"] == "db" for l in r.get("locations", [])):
            return None
        return self.kb.blob(r.get("sha256", ""))

    def text(self, name: str) -> str | None:
        b = self.content(name)
        return None if b is None else b.decode("utf-8", errors="replace")

    def __repr__(self):
        return f"Item({self.id})"


def blob_shas(data: dict) -> list[str]:
    """Hashes of content an item keeps in the store's database: its db renditions,
    and the files of asset versions kept in the store."""
    out = [r["sha256"] for r in data.get("renditions", [])
           if r.get("sha256") and any(l["type"] == "db" for l in r.get("locations", []))]
    if data.get("kind") == "asset":
        out += [f["sha256"] for v in data.get("versions", []) for f in v.get("files", [])]
    return list(dict.fromkeys(out))


#: The most text of one rendition that is indexed.
INDEX_CHARS = 2_000_000


def rendition_texts(kb: "Store", rec: Item, fetch: bool = False) -> list[tuple[str, str]]:
    """Searchable text, by rendition: each textual rendition (an original, a note, text extracted from a
    PDF ...) whose content is here, wherever it is (the database, a content store, a dataset); with FETCH,
    also what must be fetched or got first.  View sources are left out."""
    from .content import MARKUP, is_text
    out = []
    for r in rec.renditions:
        if r["role"] == "source" or not is_text(r["media_type"]) or r["media_type"] in MARKUP:
            continue                # markup is searched through the text extracted from it
        t = None
        if any(l["type"] == "db" for l in r.get("locations", [])):
            t = rec.text(r["name"])
        if t is None and r.get("locations"):
            from . import locations
            try:
                p = locations.resolve(kb, rec, r, fetch=fetch)
            except KBError:
                p = None
            if p is not None:
                with open(p, encoding="utf-8", errors="replace") as f:
                    t = f.read(INDEX_CHARS)
        if t:
            out.append((r["name"], t[:INDEX_CHARS]))
    return out


def text_of(kb: "Store", rec: Item) -> str:
    """Searchable text: the item's textual renditions, all together."""
    return "\n\n".join(t for _, t in rendition_texts(kb, rec))


def index_rows(kb: "Store", rec: Item, fetch: bool = False) -> list[tuple]:
    """The fts rows of an item: its head (rendition '', chunk 0) and each chunk of each text rendition, every
    row with the item's title, summary and subjects (so words of both match together).  Text of several
    pages (separated by form feeds, as extracted from PDFs) is chunked page by page, and each row has its
    page (else None)."""
    from .embed import chunks, head_text, text_hash as h
    d = rec.data
    title, summary = d.get("title", ""), d.get("summary", "")
    # subjects and tags, words apart, so that search finds an item by them too
    subjects = " ".join([s.replace("/", " ") for s in d.get("subject", [])]
                        + [t.replace("-", " ") + " " + t for t in d.get("tags", [])])
    head = head_text(title, summary)
    rows = [(rec.id, "", 0, None, h(head), title, summary, subjects, "")]
    for name, text in rendition_texts(kb, rec, fetch):
        pages = text.split("\f")
        n = 0
        for p, page in enumerate(pages, 1):
            for c in chunks(page):
                n += 1
                rows.append((rec.id, name, n, p if len(pages) > 1 else None, h(c), title, summary, subjects, c))
    return rows


class Store:
    """A store: its settings, items, blobs and history in one SQLite file."""

    def __init__(self, root: Path, conn, cfg, readonly: bool):
        self.root = root
        self.conn = conn
        self.cfg = cfg
        self.readonly = readonly
        self.tx = dbm.Tx(conn)

    @classmethod
    def open(cls, root: Path | None = None, readonly: bool = False) -> "Store":
        from . import config as config_mod
        from .schema import check_revision
        root = (root.resolve() if root else config_mod.find_root())
        path = root / dbm.DB_NAME
        if not path.exists():
            raise KBError(f"{root} is not a kb store (no {dbm.DB_NAME}); kb init makes one")
        conn = dbm.connect(path, readonly=readonly)
        log.debug("opened the store %s%s", root, " read-only" if readonly else "")
        settings = {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}
        upgraded = settings.pop(dbm.REINDEX, None)
        cfg = config_mod.from_settings(root, settings)
        check_revision(cfg.schema)
        kb = cls(root, conn, cfg, readonly)
        if upgraded and not readonly:
            log.info("%s: the database was upgraded: rebuilding its query tables", root)
            kb.reindex()
            conn.execute("DELETE FROM settings WHERE key = ?", (dbm.REINDEX,))
        return kb

    @classmethod
    def create(cls, root: Path, settings: dict) -> "Store":
        root.mkdir(parents=True, exist_ok=True)
        path = root / dbm.DB_NAME
        if path.exists():
            raise KBError(f"{root} already holds a store")
        conn = dbm.connect(path, create=True)
        with dbm.Tx(conn)():
            for k, v in settings.items():
                conn.execute("INSERT INTO settings VALUES (?, ?)", (k, json.dumps(v)))
        conn.close()
        return cls.open(root)

    def close(self) -> None:
        self.conn.close()

    # -- settings ---------------------------------------------------------------

    def settings(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.conn.execute("SELECT key, value FROM settings")}

    def setting(self, key: str, default=None):
        r = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        self._writable()
        with self.tx():
            if value is None:
                self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
            self.log_event("settings", None, {"key": key})
        from . import config as config_mod
        self.cfg = config_mod.from_settings(self.root, self.settings(), keep=self.cfg)

    def _writable(self):
        if self.readonly:
            raise KBError(f"store {self.cfg.name} is open read-only here")

    # -- items ------------------------------------------------------------------

    @cached_property
    def items(self) -> dict[str, Item]:
        """All items of the store, by ID."""
        out = {}
        for r in self.conn.execute("SELECT json, updated_at FROM items"):
            data = json.loads(r["json"])
            out[data["id"]] = Item(data, self, r["updated_at"])
        return out

    def reload(self) -> None:
        for attr in ("items", "superseded_by"):
            self.__dict__.pop(attr, None)

    def get(self, ref: str) -> Item:
        ident, _ = parse_ref(ref)
        try:
            return self.items[ident]
        except KeyError:
            raise KBError(f"no item {ident}") from None

    def exists(self, ref: str) -> bool:
        try:
            ident, rendition = parse_ref(ref)
        except KBError:
            return False
        rec = self.items.get(ident)
        return rec is not None and (rendition is None or rec.has_rendition(rendition))

    @cached_property
    def superseded_by(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for rec in self.items.values():
            for ref in rec.data.get("supersedes", []) + rec.data.get("retracts", []):
                out.setdefault(parse_ref(ref)[0], []).append(rec.id)
        return out

    def is_current(self, ident: str) -> bool:
        return ident not in self.superseded_by

    def ref_digest(self, ref: str) -> str:
        ident, name = parse_ref(ref)
        rec = self.get(ident)
        d = rec.digest()
        if name:
            sha = rec.rendition(name).get("sha256", "")
            d = hashlib.sha256(f"{d}:{name}:{sha}".encode()).hexdigest()
        return d

    def save(self, rec: Item, action: str = "set", detail: dict | None = None,
             approved: str | None = None, actor: str | None = None, validate: bool = True) -> Item:
        """Write an item (insert or update), keep derived tables in step, log the event."""
        from .schema import validate as schema_validate
        self._writable()
        data = rec.data
        if validate:
            errors = schema_validate(data)
            if errors:
                raise KBError(f"{data.get('id')} not written:\n  " + "\n  ".join(errors))
        stamp = now(precise=True)
        row = (data["id"], data["kind"], json.dumps(ordered(data), ensure_ascii=False), digest(data),
               core_digest(data), json.dumps(fixed(data)), int(is_frozen(data)), data["created"]["at"], stamp,
               actor or self.cfg.user)
        try:
            with self.tx():
                exists = self.conn.execute("SELECT 1 FROM items WHERE id = ?", (data["id"],)).fetchone()
                if exists:
                    self.conn.execute("""UPDATE items SET kind=?, json=?, digest=?, core=?, fixed=?, frozen=?,
                                         created_at=?, updated_at=?, updated_by=? WHERE id=?""", row[1:] + row[:1])
                else:
                    self.conn.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?)", row)
                rec.kb, rec.updated_at = self, stamp
                self._index(rec)
                self.log_event(action if exists or action != "set" else "create", data["id"], detail, approved, actor)
        except Exception as exc:  # sqlite3 errors from the guards
            if "kb:" in str(exc):
                raise KBError(f"{data['id']}: {exc}") from None
            raise
        self.items[rec.id] = rec
        self.__dict__.pop("superseded_by", None)
        return rec

    def _index(self, rec: Item, fetch: bool = False) -> None:
        d, c, i = rec.data, self.conn, rec.id
        for t in ("subjects", "runs", "refs", "identifiers", "renditions"):
            c.execute(f"DELETE FROM {t} WHERE {'src' if t == 'refs' else 'id'} = ?", (i,))
        c.execute("DELETE FROM fts WHERE id = ?", (i,))
        c.executemany("INSERT INTO subjects VALUES (?,?)", [(i, s) for s in d.get("subject", [])])
        c.executemany("INSERT INTO runs VALUES (?,?,?)", [(i, a, b) for a, b in d.get("valid", {}).get("runs", [])])
        rel = [(i, parse_ref(r)[0], f) for f in ("supersedes", "retracts", "related", "members") for r in d.get(f, [])]
        rel += [(i, parse_ref(r)[0], "derived_from") for r in rec.inputs()]
        rel += [(i, parse_ref(e)[0], "evidence") for e in d.get("evidence", []) if "://" not in e]
        rel += [(i, s, "source") for s in source_refs(d)]
        rel += [(i, parse_ref(x["ref"])[0], x["rel"]) for x in d.get("links", [])]
        c.executemany("INSERT INTO refs VALUES (?,?,?)", rel)
        c.executemany("INSERT INTO identifiers VALUES (?,?,?)", [(i, k, v) for k, v in d.get("identifiers", {}).items()])
        c.executemany("INSERT INTO renditions VALUES (?,?,?,?,?,?,?,?)", [
            (i, r["name"], r["role"], r["media_type"], r.get("fidelity"), r.get("sha256"), r.get("size"),
             json.dumps(r.get("locations", []))) for r in d.get("renditions", [])])
        c.executemany("INSERT INTO fts VALUES (?,?,?,?,?,?,?,?,?)", index_rows(self, rec, fetch))

    def reindex(self, fetch: bool = False) -> int:
        """Rebuild the query tables from the items; with FETCH, text not on this machine is fetched (URLs,
        DataLad content) to be indexed too."""
        self._writable()
        with self.tx():
            for t in ("subjects", "runs", "refs", "identifiers", "renditions", "fts"):
                self.conn.execute(f"DELETE FROM {t}")
            for rec in self.items.values():
                self._index(rec, fetch)
        return len(self.items)

    def write_new(self, data: dict, approved: str | None = None) -> Item:
        if data["id"] in self.items:
            raise KBError(f"item {data['id']} already exists")
        return self.save(Item(data, self), "create", approved=approved)

    def resolve(self, rec: Item, rendition: dict, fetch: bool = False):
        """A local file with the rendition's content, if this store can reach it."""
        from .locations import resolve
        return resolve(self, rec, rendition, fetch=fetch)

    def snapshot(self, dest: Path) -> Path:
        """A consistent single-file copy of the store (for publishing read-only, or backup)."""
        dest = Path(dest)
        if dest.is_dir():
            dest = dest / dbm.DB_NAME
        tmp = dest.with_name(dest.name + ".partial")
        tmp.unlink(missing_ok=True)
        self.conn.execute("VACUUM INTO ?", (str(tmp),))
        tmp.replace(dest)
        return dest

    # -- blobs ------------------------------------------------------------------

    def blob(self, sha: str) -> bytes | None:
        r = self.conn.execute("SELECT data FROM blobs WHERE sha256 = ?", (sha,)).fetchone()
        return None if r is None else bytes(r["data"])

    def has_blob(self, sha: str) -> bool:
        return self.conn.execute("SELECT 1 FROM blobs WHERE sha256 = ?", (sha,)).fetchone() is not None

    def put_blob(self, data: bytes) -> tuple[str, int]:
        self._writable()
        sha = hashlib.sha256(data).hexdigest()
        with self.tx():
            self.conn.execute("INSERT OR IGNORE INTO blobs VALUES (?,?,?)", (sha, len(data), data))
        return sha, len(data)

    # -- events -----------------------------------------------------------------

    def log_event(self, action: str, item: str | None = None, detail: dict | None = None,
                  approved: str | None = None, actor: str | None = None) -> None:
        from .config import coauthors
        if coauthors():                  # every write says who took part, as Co-Authored-By does
            detail = {**(detail or {}), "with": coauthors()}
        self.conn.execute("INSERT INTO events (at, item, action, actor, approved, detail) VALUES (?,?,?,?,?,?)",
                          (now(precise=True), item, action, actor or self.cfg.user, approved,
                           json.dumps(detail) if detail is not None else None))

    def events(self, item: str | None = None, action: str | None = None, limit: int | None = None) -> list[dict]:
        q, args = "SELECT * FROM events", []
        where = []
        if item:
            where.append("item = ?")
            args.append(item)
        if action:
            where.append("action = ?")
            args.append(action)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY seq"
        rows = [dict(r) | {"detail": json.loads(r["detail"]) if r["detail"] else None}
                for r in self.conn.execute(q, args)]
        return rows[-limit:] if limit else rows
