"""Bundles, the inbox, and review.

A **bundle** is one knowledge set and the items it denotes, serialized into a
single self-checking JSON file, together with the small content (in-database
renditions) they need.  It is how knowledge travels between people, machines
and review queues: by email, a shared directory, or as a file in a git
"exchange" repository reviewed by pull request.  Bundles are immutable and
named by their own hash, so carrying them in git never conflicts.

A store's **inbox** holds received bundles.  Each may collect **reviews**,
from people and from LLMs (verdict plus reasoning), before a decision:
``accept`` applies it in one transaction, ``reject`` records why not.  Who may
accept is governed by the ``accept`` autonomy level.
"""

import base64
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KBError
from .records import KB, Record, canonical, now

FORMAT = 1
SUFFIX = ".kbundle.json"
VERDICTS = ("accept", "reject", "changes", "comment")


# -- making and reading -----------------------------------------------------------

@dataclass
class SenderInfo:
    id: str
    name: str
    scope: str
    user: str


class BundleSource:
    """A bundle seen as a sender store (what kb.transfer needs: records, blobs, cfg)."""

    def __init__(self, b: dict):
        self.bundle = b
        f = b["from"]
        self.cfg = SenderInfo(f["store"], f.get("name", f["store"]), f.get("scope", "other"),
                              b["created"].get("by", "unknown"))
        self.records = {}
        for it in [{"data": b["set"], "updated_at": b.get("set_updated_at")}] + b["items"]:
            rec = Record(it["data"], None, it.get("updated_at"))
            self.records[rec.id] = rec
        self._blobs = b.get("blobs", {})
        self.people = {i: Record(d) for i, d in b.get("people", {}).items()}

    def blob(self, sha: str) -> bytes | None:
        v = self._blobs.get(sha)
        return None if v is None else base64.b64decode(v)


def bundle_id(b: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in b.items() if k != "id"})).hexdigest()


def make(kb: KB, kset_ref: str, note: str | None = None, approved: str | None = None) -> dict:
    """A bundle of the set *kset_ref* (held in *kb*) and everything it denotes there."""
    from .transfer import expand
    ks = kb.get(kset_ref)
    if ks.kind != "kset":
        raise KBError(f"{ks.id} is a {ks.kind}; bundles are made from knowledge sets (kb kset new)")
    exp = expand(ks.data, lambda i: kb.records[i].data if i in kb.records else None)
    if exp.missing_members:
        raise KBError("set members not in this store: " + ", ".join(i for _, i in exp.missing_members))
    items, blobs = [], {}
    for ident in exp.items:
        if ident == ks.id:
            continue
        rec = kb.records[ident]
        items.append({"data": rec.data, "updated_at": rec.updated_at})
        for r in rec.renditions:
            if any(l["type"] == "db" for l in r.get("locations", [])) and r.get("sha256"):
                data = kb.blob(r["sha256"])
                if data is not None:
                    blobs[r["sha256"]] = base64.b64encode(data).decode()
    for r in ks.renditions:
        if r.get("sha256") and kb.blob(r["sha256"]) is not None:
            blobs[r["sha256"]] = base64.b64encode(kb.blob(r["sha256"])).decode()
    people = {}
    for it in [{"data": ks.data}] + items:
        for who in _agents(it["data"]) + [kb.cfg.user]:
            if who.startswith("per-") and who in kb.records and who not in people:
                people[who] = kb.records[who].data
    b = {"kb_bundle": FORMAT, "created": {"at": now(), "by": kb.cfg.user},
         "from": {"store": kb.cfg.id, "name": kb.cfg.name, "scope": kb.cfg.scope},
         "set": ks.data, "set_updated_at": ks.updated_at, "items": items, "blobs": blobs,
         "missing": [{"from": f, "id": i} for f, i, _ in exp.missing], "people": people}
    if note:
        b["note"] = note
    if approved:
        b["approved"] = approved
    b["id"] = bundle_id(b)
    return b


def _agents(d: dict) -> list[str]:
    """Person refs an item mentions (for rendering names; people travel as context, not as items)."""
    out = [d.get("created", {}).get("by", "")] + list(d.get("creators", []))
    out += [a.get("who", "") for a in d.get("asserted_by", [])]
    return [x for x in out if isinstance(x, str)]


def write(b: dict, path: Path) -> Path:
    if path.is_dir():
        path = path / f"{b['set']['id']}-{b['id'][:12]}{SUFFIX}"
    path.write_text(json.dumps(b, indent=1, ensure_ascii=False) + "\n")
    return path


def read(path: Path) -> dict:
    try:
        b = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KBError(f"{path}: not a readable bundle: {exc}") from None
    return verify(b, str(path))


def verify(b: dict, where: str = "bundle") -> dict:
    if not isinstance(b, dict) or b.get("kb_bundle") != FORMAT:
        raise KBError(f"{where}: not a kb bundle (format {FORMAT})")
    if b.get("id") != bundle_id(b):
        raise KBError(f"{where}: checksum mismatch: the bundle was altered or truncated")
    for sha, data in b.get("blobs", {}).items():
        if hashlib.sha256(base64.b64decode(data)).hexdigest() != sha:
            raise KBError(f"{where}: embedded content {sha[:12]}… does not match its hash")
    return b


# -- checking against a store -------------------------------------------------------

def check(kb: KB, b: dict) -> dict:
    """Everything a reviewer needs to know mechanically: validity and what applying would do."""
    from .checks import check_data
    from .transfer import plan
    src = BundleSource(b)
    problems = []
    for rec in src.records.values():
        problems += [p.as_dict() for p in check_data(kb, rec.data) if p.level == "error" and p.code != "missing"]
    try:
        p = plan(src, kb, b["set"], prefer="ours")
        out = {"plan": p.summary(), "notes": {i.id: i.notes for i in p.items if i.notes},
               "dangling": p.dangling, "relabel_needed": []}
    except KBError as exc:
        out = {"plan": None, "error": str(exc)}
    out["problems"] = problems
    out["ok"] = not problems and out.get("plan") is not None and not out["plan"]["conflict"]
    return out


def apply(kb: KB, b: dict, approved: str = "human", access: str | None = None, prefer: str = "ours") -> dict:
    from .transfer import transfer
    src = BundleSource(b)
    extra = []
    if b["set"]["id"] not in kb.records:
        extra = []   # the set travels as an item of its own transfer (see plan: sender holds it)
    return transfer(src, kb, b["set"], prefer=prefer, access=access, approved=approved,
                    edge=b["from"].get("name", ""), direction="bundle", extra=extra, log_source=False)


# -- the inbox ------------------------------------------------------------------------

@dataclass
class Entry:
    bundle: str
    set_id: str
    sender: str
    sender_name: str
    received_at: str
    status: str
    decided_at: str | None = None
    decided_by: str | None = None
    note: str | None = None
    reviews: list = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


def receive(kb: KB, b: dict, note: str | None = None) -> Entry:
    """Put a bundle into the store's inbox (once; receiving it again is a no-op)."""
    verify(b)
    with kb.tx():
        if kb.conn.execute("SELECT 1 FROM inbox WHERE bundle = ?", (b["id"],)).fetchone() is None:
            kb.conn.execute("INSERT INTO inbox VALUES (?,?,?,?,?,?,?,?,?,?)", (
                b["id"], b["set"]["id"], b["from"]["store"], b["from"].get("name"), now(), "pending",
                json.dumps(b).encode(), None, None, note))
            kb.log_event("inbox-receive", b["set"]["id"], {"bundle": b["id"], "from": b["from"]})
    return get(kb, b["id"])


def _entry(kb: KB, row) -> Entry:
    e = Entry(row["bundle"], row["set_id"], row["sender"], row["sender_name"], row["received_at"],
              row["status"], row["decided_at"], row["decided_by"], row["note"])
    e.reviews = [dict(r) for r in kb.conn.execute("SELECT * FROM reviews WHERE bundle = ? ORDER BY seq",
                                                  (row["bundle"],))]
    return e


def _find(kb: KB, ref: str):
    rows = kb.conn.execute("SELECT * FROM inbox WHERE bundle LIKE ? OR set_id = ?", (ref + "%", ref)).fetchall()
    if not rows:
        raise KBError(f"no inbox entry {ref!r} (kb inbox list)")
    if len(rows) > 1:
        raise KBError(f"{ref!r} matches {len(rows)} inbox entries; give more of the bundle id")
    return rows[0]


def get(kb: KB, ref: str) -> Entry:
    return _entry(kb, _find(kb, ref))


def bundle_of(kb: KB, ref: str) -> dict:
    return json.loads(bytes(_find(kb, ref)["data"]))


def entries(kb: KB, status: str | None = "pending") -> list[Entry]:
    q, args = "SELECT * FROM inbox", []
    if status and status != "all":
        q += " WHERE status = ?"
        args.append(status)
    return [_entry(kb, r) for r in kb.conn.execute(q + " ORDER BY received_at", args)]


def review(kb: KB, ref: str, verdict: str, text: str, reviewer: str | None = None, kind: str | None = None) -> Entry:
    """Record a review: by a person, or by an LLM (whose verdict is advice, never a decision)."""
    if verdict not in VERDICTS:
        raise KBError(f"verdict must be one of {', '.join(VERDICTS)}")
    row = _find(kb, ref)
    kind = kind or ("llm" if kb.cfg.is_agent else "human")
    with kb.tx():
        kb.conn.execute("INSERT INTO reviews (bundle, at, reviewer, reviewer_kind, verdict, text) VALUES (?,?,?,?,?,?)",
                        (row["bundle"], now(), reviewer or (("claude" if kind == "llm" else kb.cfg.user)), kind,
                         verdict, text))
        kb.log_event("review", row["set_id"], {"bundle": row["bundle"], "verdict": verdict, "kind": kind})
    return get(kb, row["bundle"])


def _guard(kb: KB, approved: str | None) -> str:
    if not kb.cfg.is_agent:
        return approved or "human"
    level = kb.cfg.autonomy_for("accept")
    if level == "never":
        raise KBError("autonomy: accepting bundles into this store is reserved for the human; record your "
                      "assessment with kb review note, and leave the decision to them")
    if level == "ask" and not approved:
        raise KBError("autonomy: accepting needs the human's approval; ask, then pass --approved \"<their words>\"")
    return f"human: {approved}" if approved else "autonomy:auto"


def accept(kb: KB, ref: str, approved: str | None = None, note: str | None = None, access: str | None = None,
           prefer: str = "ours") -> dict:
    row = _find(kb, ref)
    if row["status"] != "pending":
        raise KBError(f"inbox entry {row['bundle'][:12]} is already {row['status']}")
    who = _guard(kb, approved)
    b = json.loads(bytes(row["data"]))
    report = apply(kb, b, approved=who, access=access, prefer=prefer)
    with kb.tx():
        kb.conn.execute("UPDATE inbox SET status='accepted', decided_at=?, decided_by=?, note=? WHERE bundle=?",
                        (now(), who, note, row["bundle"]))
        kb.log_event("inbox-accept", row["set_id"], {"bundle": row["bundle"]}, approved=who)
    return report


def reject(kb: KB, ref: str, reason: str, approved: str | None = None) -> Entry:
    row = _find(kb, ref)
    if row["status"] != "pending":
        raise KBError(f"inbox entry {row['bundle'][:12]} is already {row['status']}")
    who = _guard(kb, approved) if kb.cfg.is_agent else (approved or "human")
    with kb.tx():
        kb.conn.execute("UPDATE inbox SET status='rejected', decided_at=?, decided_by=?, note=? WHERE bundle=?",
                        (now(), who, reason, row["bundle"]))
        kb.log_event("inbox-reject", row["set_id"], {"bundle": row["bundle"], "reason": reason}, approved=who)
    return get(kb, row["bundle"])


def import_dir(kb: KB, directory: Path, accept_all: bool = False, approved: str | None = None) -> list[dict]:
    """Receive every bundle file under *directory* (e.g. a merged exchange repository)."""
    out = []
    for p in sorted(directory.rglob(f"*{SUFFIX}")):
        b = read(p)
        known = kb.conn.execute("SELECT status FROM inbox WHERE bundle = ?", (b["id"],)).fetchone()
        if known is not None:
            out.append({"bundle": b["id"][:12], "file": str(p), "status": known["status"]})
            continue
        receive(kb, b, note=f"imported from {p}")
        status = "pending"
        if accept_all:
            accept(kb, b["id"], approved=approved, note="accepted on import (reviewed upstream)")
            status = "accepted"
        out.append({"bundle": b["id"][:12], "file": str(p), "status": status})
    return out
