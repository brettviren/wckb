"""Knowledge transfer between stores.

A *knowledge set* (a ``kset`` item) names what to transfer.  It is expanded
(shallow: the members; deep: the members and everything they reference,
recursively) into a closed set of items, which are applied to the receiving
store in **one transaction**.  The sender is a store, or a *bundle*: the
same items serialized into one file (see ``kb.bundle``), which is how
knowledge travels between people, machines and review queues.  Both sides
log the transfer as events (``transfer-out``, ``transfer-in``).

The same item can arrive along several paths, possibly changed on one of
them.  An item's *core* (what a primary item asserts; see
``items.IMMUTABLE``) must be identical wherever it travels: a different
core is a conflict, and the whole transfer is refused.  Its mutable fields
(status, summary, subject, tags, evidence, related, ...) are reconciled:
lists are united; for single values the receiver keeps its own unless the
item comes again from the store it first came from, or the edge prefers
"theirs".  Every arrival is logged, so where things came from stays visible.
"""

import copy
import hashlib
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from . import content
from .config import access_rank
from .errors import KBError
from .ids import parse_ref
from .items import Store, Item, blob_shas, canonical, core, fixed_compatible, is_frozen, now
REF_FIELDS = ("members", "supersedes", "retracts", "derived_from", "evidence", "related")
MUTABLE_LISTS = ("subject", "tags", "evidence", "related")
MUTABLE_SCALARS = ("status", "summary", "license")
MUTABLE = MUTABLE_SCALARS + MUTABLE_LISTS + ("identifiers",)


# -- knowledge sets -------------------------------------------------------------

def refs_of(data: dict, fields) -> list[str]:
    out = []
    for f in fields:
        for r in data.get(f, []):
            r = r["ref"] if isinstance(r, dict) else r
            if isinstance(r, str) and "://" not in r:
                out.append(parse_ref(r)[0])
    return out


@dataclass
class Expansion:
    items: list[str]                                   # in discovery order, the set itself first
    missing: list[tuple[str, str, bool]] = field(default_factory=list)   # (from, id, explicit member?)

    @property
    def missing_members(self):
        return [(f, i) for f, i, explicit in self.missing if explicit]


def expand(kset: dict, lookup: Callable[[str], dict | None]) -> Expansion:
    """Resolve a knowledge set into the items it denotes.

    shallow: the set and its members.  deep: also everything referenced from
    them (the fields in ``follow``), recursively.  A member, or a reference
    reached deeply, that is itself a set contributes its members, at its own
    depth (``nested = "follow"``, the default) or at the depth in force where
    it was reached (``nested = "inherit"``).  Missing items are reported,
    not fatal: references are store-independent.
    """
    nested = kset.get("nested", "follow")
    follow = [f for f in kset.get("follow", REF_FIELDS) if f != "members"]
    rank = {"shallow": 0, "deep": 1}
    best: dict[str, str] = {}
    order: list[str] = []
    missing: list[tuple[str, str, bool]] = []
    seen_missing: set[str] = set()
    queue = deque([(kset["id"], kset["depth"], None, True)])
    while queue:
        ident, mode, frm, explicit = queue.popleft()
        if ident in best and rank[best[ident]] >= rank[mode]:
            continue
        data = kset if ident == kset["id"] else lookup(ident)
        if data is None:
            if ident not in seen_missing:
                seen_missing.add(ident)
                missing.append((frm or "", ident, explicit))
            continue
        if ident not in best:
            order.append(ident)
        best[ident] = mode
        if data.get("kind") == "kset":
            own = data.get("depth", "shallow")
            child = own if (ident == kset["id"] or nested == "follow") else mode
            for m in refs_of(data, ["members"]):
                queue.append((m, child, ident, True))
        if mode == "deep":
            for r in refs_of(data, follow):
                queue.append((r, "deep", ident, False))
    return Expansion(order, missing)


# -- classification ---------------------------------------------------------------

def _union(a: list, b: list) -> list:
    out = list(a)
    for x in b:
        if x not in out:
            out.append(x)
    return out


def _strip(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "origin"}


@dataclass
class PlanEntry:
    id: str
    action: str                   # new | update | same | conflict
    data: dict
    src: Item
    notes: list[str] = field(default_factory=list)


def _core_differs(a: dict, b: dict) -> list[str]:
    ca, cb = core(a), core(b)
    diff = sorted(k for k in ca if k != "_fixed" and ca[k] != cb.get(k))
    if ca.get("_fixed") != cb.get("_fixed") or not fixed_compatible(a, b):
        diff.append("content")
    return diff


def classify(incoming: dict, src: Item, existing: Item | None, sender_id: str, prefer: str) -> PlanEntry:
    """What arriving *incoming* means for a store holding *existing*.

    prefer: ours | theirs | newer (by the stores' last-update times; for twins).
    """
    if existing is None:
        return PlanEntry(src.id, "new", incoming, src)
    mine = existing.data
    same_path = mine.get("origin", {}).get("store") == sender_id
    if prefer == "newer":
        theirs = (src.updated_at or "") > (existing.updated_at or "")
    else:
        theirs = same_path or prefer == "theirs"
    if is_frozen(mine) or is_frozen(incoming):
        diff = _core_differs(mine, incoming)
        if diff:
            return PlanEntry(src.id, "conflict", incoming, src,
                        [f"what the item asserts differs ({', '.join(diff)}): two different items share this ID, "
                         "or a source was changed outside kb somewhere"])
        merged = copy.deepcopy(mine)
        notes = []
        for k in MUTABLE_LISTS:
            u = _union(mine.get(k, []), incoming.get(k, []))
            if u:
                merged[k] = u
        for k in MUTABLE_SCALARS + ("stage",):
            a, b = mine.get(k), incoming.get(k)
            if a != b and b is not None:
                if theirs or a is None:
                    merged[k] = b
                else:
                    notes.append(f"kept our {k} ({a!r}); this path says {b!r}")
        if incoming.get("identifiers"):
            merged["identifiers"] = (mine.get("identifiers", {}) | incoming["identifiers"]) if theirs \
                else (incoming["identifiers"] | mine.get("identifiers", {}))
        have = {r["name"]: r for r in mine.get("renditions", [])}
        for r in incoming.get("renditions", []):
            if r["name"] not in have:
                merged.setdefault("renditions", []).append(r)
            elif have[r["name"]].get("sha256") is None and r.get("sha256"):
                have[r["name"]].update(sha256=r["sha256"], size=r.get("size"))
        if mine.get("kind") == "asset":
            _merge_asset(merged, incoming)
        if theirs and "origin" in incoming:
            merged["origin"] = incoming["origin"]
        if canonical(_strip(merged)) == canonical(_strip(mine)):
            return PlanEntry(src.id, "same", merged, src, notes)
        return PlanEntry(src.id, "update", merged, src, notes)
    # Editable items (topics, views, drafts, persons): replaced whole, or not at all.
    if canonical(_strip(mine)) == canonical(_strip(incoming)):
        return PlanEntry(src.id, "same", mine, src)
    if theirs:
        return PlanEntry(src.id, "update", incoming, src)
    return PlanEntry(src.id, "conflict", incoming, src,
                ["an editable item that diverged along different paths; keep ours, or transfer with prefer=theirs"])


def _merge_asset(merged: dict, incoming: dict) -> None:
    """Versions are united by label (shared labels agree on digest, checked as core); bindings by name."""
    have = {v["label"]: v for v in merged.setdefault("versions", [])}
    for v in incoming.get("versions", []):
        mine = have.get(v["label"])
        if mine is None:
            merged["versions"].append(copy.deepcopy(v))
            continue
        if not mine.get("digest") and v.get("digest"):
            mine.update(digest=v["digest"], files=v.get("files", []))
        for e in v.get("sightings", []):
            if e not in mine.setdefault("sightings", []):
                mine["sightings"].append(e)
    names = {b["name"] for b in merged.setdefault("bindings", [])}
    merged["bindings"] += [copy.deepcopy(b) for b in incoming.get("bindings", []) if b["name"] not in names]


# -- the transfer -----------------------------------------------------------------

def state(rec: Item) -> str:
    """Digest plus mutable fields: what a repeated transfer would change."""
    return hashlib.sha256(canonical([rec.digest(), {k: rec.data.get(k) for k in MUTABLE}])).hexdigest()


def ledger(kb: Store) -> list[dict]:
    """Transfers out of and into *kb*, oldest first."""
    out = []
    for e in kb.events():
        if e["action"] in ("transfer-out", "transfer-in"):
            out.append(e["detail"] | {"dir": "out" if e["action"] == "transfer-out" else "in", "at": e["at"],
                                      "by": e["actor"], "approved": e["approved"], "set": e["item"]})
    return out


def _origin(sender, rec: Item, kset_id: str) -> dict:
    prev = rec.data.get("origin", {})
    path = list(prev.get("path") or ([prev["store"]] if prev.get("store") else []))
    o = {"store": sender.cfg.id, "name": sender.cfg.name, "scope": sender.cfg.scope,
         "path": path + [sender.cfg.id], "set": kset_id, "pushed_at": now(), "pushed_by": sender.cfg.user}
    return o


@dataclass
class Plan:
    items: list[PlanEntry]
    expansion: Expansion
    dangling: list[str]
    relabeled: list[str] = field(default_factory=list)

    @property
    def conflicts(self):
        return [i for i in self.items if i.action == "conflict"]

    @property
    def changes(self):
        return [i for i in self.items if i.action in ("new", "update")]

    def summary(self) -> dict:
        return {a: [i.id for i in self.items if i.action == a] for a in ("new", "update", "same", "conflict")}


def plan(sender, dst: Store, kset: dict, *, prefer: str = "ours", access: str | None = None,
         relabel: str | None = None) -> Plan:
    """What applying *kset* from *sender* (a store or a bundle) to *dst* would do."""
    lookup = lambda i: sender.items[i].data if i in sender.items else None  # noqa: E731
    exp = expand(kset, lookup)
    if exp.missing_members:
        raise KBError("set members not held by the sender: " + ", ".join(f"{i} (from {f})" for f, i in exp.missing_members)
                      + "; fetch them first (kb pull ID ...) or leave them out")
    items, relabeled = [], []
    for ident in exp.items:
        if ident not in sender.items:
            continue  # the set itself, when the receiver made it (pulls)
        rec = sender.items[ident]
        data = copy.deepcopy(rec.data)
        lvl = data.get("access", "group")
        if access_rank(lvl) > access_rank(dst.cfg.access):
            new = access or relabel
            if new is None or access_rank(new) > access_rank(dst.cfg.access):
                raise KBError(f"{ident} is {lvl}, more restricted than {dst.cfg.name} ({dst.cfg.access}); "
                              "relabel deliberately with --access LEVEL, or leave it out")
            data["access"] = new
            relabeled.append(ident)
        if sender.cfg.id != dst.cfg.id:      # twins are one store: provenance stays as it was
            data["origin"] = _origin(sender, rec, kset["id"])
        items.append(classify(data, rec, dst.items.get(ident), sender.cfg.id, prefer))
    sent = {i.id for i in items} | {kset["id"]}
    dangling = []
    for it in items:
        for r in refs_of(it.data, REF_FIELDS):
            if r not in sent and r not in dst.items and r not in dangling:
                dangling.append(r)
    return Plan(items, exp, dangling, relabeled)


def _copy_content(sender, rec: Item, dst: Store, data: dict) -> list[str]:
    """Bring the bytes of the item's renditions into *dst* where the receiver keeps them."""
    warnings = []
    for sha in blob_shas(data):
        if not dst.has_blob(sha):
            b = sender.blob(sha)
            if b is not None:
                dst.put_blob(b)
            elif data.get("kind") != "asset":     # asset files may legitimately live only in a content store
                warnings.append(f"{rec.id}: content {sha[:12]}… not available from the sender")
    for r in data.get("renditions", []):
        sha = r.get("sha256")
        for loc in r.get("locations", []):
            if loc["type"] == "cas" and sha:
                name = loc["name"] if dst.cfg.cas.get(loc["name"]) else content.default_cas(dst)
                if name is not None and content.blob_path(dst.cfg.cas[name], sha).exists():
                    loc["name"] = name
                    continue
                path = sender.resolve(rec, r) if hasattr(sender, "resolve") else None
                if path is None or name is None:
                    warnings.append(f"{rec.id}#{r['name']}: large content stays at the sender's store "
                                    f"({loc['name']}); the receiver can't reach it yet")
                    continue
                content.put_cas(dst, name, path, sha)
                loc["name"] = name
            elif loc["type"] == "local":
                warnings.append(f"{rec.id}#{r['name']} is local-only; the copy points at {loc['host']}")
    return warnings


def apply(sender, dst: Store, p: Plan, kset: dict, *, approved: str, edge: str, direction: str,
          extra: list[Item] | None = None) -> dict:
    """Write the plan into *dst* in one transaction (rolled back if any item fails its checks)."""
    from .checks import check_data
    warnings: list[str] = []
    try:
        _apply_tx(sender, dst, p, kset, approved, edge, direction, extra, warnings)
    except BaseException:
        dst.reload()       # the cache may hold items the rollback removed
        raise
    dst.reload()
    return {"warnings": warnings}


def _apply_tx(sender, dst, p, kset, approved, edge, direction, extra, warnings):
    from .checks import check_data
    with dst.tx():
        for rec in extra or []:              # e.g. the set made for a pull, written with its transfer
            dst.save(rec, "create")
        for it in p.changes:
            warnings += _copy_content(sender, it.src, dst, it.data)
        dst.reload()
        for it in p.changes:
            dst.save(Item(it.data, dst), "transfer-in", {"set": kset["id"], "from": sender.cfg.id,
                                                            "action": it.action}, approved=approved)
        errors = [x for it in p.changes for x in check_data(dst, it.data) if x.level == "error"]
        if errors:
            raise KBError(f"{dst.cfg.name} rejects the transfer:\n  " + "\n  ".join(map(str, errors)))
        dst.log_event("transfer-in", kset["id"], {
            "from": sender.cfg.id, "from_name": sender.cfg.name, "edge": edge, "direction": direction,
            "items": [{"id": i.id, "action": i.action, "digest": i.src.digest()} for i in p.items
                      if i.action != "conflict"]}, approved=approved)


def transfer(sender, dst: Store, kset: dict, *, prefer: str = "ours", access: str | None = None,
             relabel: str | None = None, approved: str = "human", edge: str = "", direction: str = "push",
             dry_run: bool = False, extra: list[Item] | None = None, log_source: bool = True) -> dict:
    """Apply *kset* from *sender* to *dst*, atomically; log it on both sides."""
    p = plan(sender, dst, kset, prefer=prefer, access=access, relabel=relabel)
    report = {"set": kset["id"], "from": sender.cfg.name, "to": dst.cfg.name, "direction": direction,
              "approved": approved, "dry_run": dry_run, "items": p.summary(),
              "notes": {i.id: i.notes for i in p.items if i.notes}, "dangling": p.dangling,
              "relabeled": p.relabeled, "warnings": []}
    if p.conflicts:
        report["error"] = "conflicts"
        if dry_run:
            return report
        raise KBError("transfer refused, conflicts:\n  " + "\n  ".join(f"{i.id}: {'; '.join(i.notes)}" for i in p.conflicts))
    if dry_run:
        return report
    if not p.changes and not extra:
        report["result"] = "nothing to transfer"
        return report
    report.update(apply(sender, dst, p, kset, approved=approved, edge=edge, direction=direction, extra=extra))
    if log_source and isinstance(sender, Store) and not sender.readonly:
        with sender.tx():
            sender.log_event("transfer-out", kset["id"], {
                "to": edge, "store": dst.cfg.id, "to_name": dst.cfg.name, "direction": direction,
                "items": [{"id": i.id, "action": i.action, "digest": i.src.digest(), "state": state(i.src)}
                          for i in p.items if i.action != "conflict"]}, approved=approved)
    return report
