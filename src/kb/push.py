"""Moving knowledge along edges: candidates, push, pull, obtain.

Every copy is a knowledge set (a ``kset`` item) expanded and applied in one
transaction by ``kb.transfer``.  ``push`` sends from this store and lands
per the edge: ``apply`` (straight into the receiver), ``inbox`` (a bundle
waiting for review there) or ``pr`` (a bundle file proposed by pull request
in the receiver's exchange repository).  ``pull`` receives into this store,
``obtain`` fetches referenced items from whichever reachable store has them.
"""

import json
from dataclasses import dataclass, field

from .config import access_rank
from .errors import KBError
from .federation import Neighbor, neighbor, neighbors, open_store
from .ids import parse_ref
from .items import Store, Item

PROMOTE_TAG, HOLD_TAG = "promote", "no-promote"


def ledger(kb: Store) -> list[dict]:
    from .transfer import ledger as read
    return read(kb)


def last_pushes(kb: Store, target: str) -> dict[str, dict]:
    """Item ID -> latest entry of transfers out to neighbor *target*."""
    out = {}
    for e in ledger(kb):
        if e.get("dir") == "out" and e.get("to") == target:
            for it in e.get("items", []):
                out[it["id"]] = it
    return out


def _state(rec: Item) -> str:
    from .transfer import state
    return state(rec)


@dataclass
class Candidate:
    id: str
    kind: str
    title: str
    state: str                     # new | changed | pushed | blocked
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def as_dict(self):
        return self.__dict__.copy()


def _blockers(kb: Store, rec: Item, target_access: str | None, nb: Neighbor) -> list[str]:
    out = []
    d = rec.data
    if target_access and access_rank(d.get("access", "group")) > access_rank(target_access):
        if not nb.relabel or access_rank(nb.relabel) > access_rank(target_access):
            out.append(f"access {d.get('access')} is more restricted than {nb.name} ({target_access}); "
                       f"push with --access to relabel, if that is right")
    if d.get("status") == "draft" or d.get("stage") == "draft":
        out.append("still a draft")
    for r in rec.renditions:
        locs = r.get("locations", [])
        if locs and all(l["type"] == "local" for l in locs):
            out.append(f"rendition {r['name']} is local-only (kb rendition locate)")
    return out


def candidates(kb: Store, target: str, include_all: bool = False) -> list[Candidate]:
    """Items worth pushing to *target*, best first, with the reasons."""
    nb = neighbor(kb, target)
    t_access = t_id = None
    if nb.available:
        tk = open_store(nb.location)
        t_access, t_id = tk.cfg.access, tk.cfg.id
    pushed = last_pushes(kb, target)
    corrected = {parse_ref(r)[0] for rec in kb.items.values()
                 for r in rec.data.get("supersedes", []) + rec.data.get("retracts", [])}
    out = []
    for rec in kb.items.values():
        d = rec.data
        if not kb.is_current(rec.id):
            continue
        if rec.kind == "kset":
            continue  # transfer artifacts travel with their transfers
        if t_id and t_id in (d.get("origin", {}).get("path") or [d.get("origin", {}).get("store")]):
            continue  # came from there
        tags = set(d.get("tags", []))
        if HOLD_TAG in tags or f"{HOLD_TAG}:{target}" in tags:
            continue
        c = Candidate(rec.id, rec.kind, d.get("title", ""), "new")
        prev = pushed.get(rec.id)
        if prev:
            if prev.get("state") == _state(rec):
                if not include_all:
                    continue
                c.state = "pushed"
            else:
                c.state = "changed"
                c.reasons.append("changed since pushed")
                c.score += 3
        if PROMOTE_TAG in tags or f"{PROMOTE_TAG}:{target}" in tags:
            c.score += 5
            c.reasons.append("marked for promotion")
        for ref in d.get("supersedes", []) + d.get("retracts", []):
            if parse_ref(ref)[0] in pushed:
                c.score += 4
                c.reasons.append(f"corrects {parse_ref(ref)[0]}, which was pushed")
        s = d.get("status")
        if s == "verified":
            c.score += 2
            c.reasons.append("verified")
        elif s == "disputed":
            c.score -= 2
            c.reasons.append("disputed")
        if rec.kind in ("topic", "observation", "config", "dataset", "snapshot"):
            c.score += 1
        if rec.kind == "view" or rec.kind == "person":
            c.score -= 1
        if d.get("subject"):
            c.score += 1
        else:
            c.reasons.append("no subject")
        if d.get("summary", "").strip() == d.get("title", "").strip():
            c.score -= 1
            c.reasons.append("weak summary")
        if rec.id in corrected:
            continue
        c.blockers = _blockers(kb, rec, t_access, nb)
        if c.blockers:
            c.state = "blocked"
        out.append(c)
    order = {"changed": 0, "new": 1, "blocked": 2, "pushed": 3}
    return sorted(out, key=lambda c: (order[c.state], -c.score, c.id))


def authorize(kb: Store, target: str, approved: str | None) -> str:
    """Who approved this push, or an error saying what is needed."""
    if not kb.cfg.is_agent:
        return approved or "human"
    level = kb.cfg.autonomy_for("push", target)
    if level == "never":
        raise KBError(f"autonomy: pushing to {target} is reserved for the human. "
                      f"Show them the candidates and the command; they run it themselves.")
    if level == "ask":
        if not approved:
            raise KBError(f"autonomy: pushing to {target} needs the human's approval in this session. "
                          f"Ask them, then pass --approved \"<their words>\".")
        return f"human: {approved}"
    return "autonomy:auto" + (f" ({approved})" if approved else "")


def edge_back(dst: Store, src: Store) -> Neighbor | None:
    """The receiving store's own edge to the sender, if it declares one."""
    for n in neighbors(dst):
        if n.path is not None and n.path.resolve() == src.root.resolve():
            return n
    return None


def make_kset(kb: Store, members: list[str], depth: str = "shallow", nested: str = "follow",
              title: str | None = None, summary: str | None = None, purpose: str = "transfer",
              allow_dangling: bool = False, save: bool = True) -> Item:
    """A knowledge set in *kb* (or, with save=False, just the item, for writing with a transfer)."""
    from . import authoring
    if not members:
        raise KBError("a knowledge set needs at least one member")
    levels = [kb.items[m].data.get("access", kb.cfg.access) for m in members if m in kb.items]
    access = max(levels, key=access_rank) if levels else kb.cfg.access   # the list reveals no more than its members
    fields = {"title": title or f"{purpose.capitalize()} of {len(members)} item(s)", "access": access,
              "summary": summary or f"{depth.capitalize()} selection of {len(members)} item(s) for {purpose}.",
              "members": list(dict.fromkeys(members)), "depth": depth, "nested": nested, "purpose": purpose,
              "subject": ["kb/transfer"]}
    if save:
        return authoring.new(kb, "kset", fields, allow_dangling=allow_dangling)
    data = authoring.deep_merge(authoring._skeleton(kb, "kset"), fields)
    from .schema import validate
    errors = validate(data)
    if errors:
        raise KBError("knowledge set not valid:\n  " + "\n  ".join(errors))
    return Item(data, kb)


def changed_items(src: Store, dst: Store, prefer: str = "theirs") -> list[str]:
    """Current items of *src* that *dst* lacks or holds differently ("all" transfers)."""
    from .transfer import classify
    out = []
    for rec in src.items.values():
        if not src.is_current(rec.id) or rec.kind == "kset":
            continue
        it = classify(rec.data, rec, dst.items.get(rec.id), src.cfg.id, prefer)
        if it.action != "same":
            out.append(rec.id)
    return out


def _selection(kb: Store, refs: list[str], depth: str, nested: str, title: str | None) -> Item:
    """An existing set (a single ks- ref held here) or a new one over *refs*."""
    if len(refs) == 1 and refs[0].startswith("ks-") and refs[0] in kb.items:
        return kb.get(refs[0])
    ids = [kb.get(parse_ref(r)[0]).id for r in refs]
    return make_kset(kb, ids, depth, nested, title)


def push(kb: Store, target: str, refs: list[str], *, deep: bool = False, nested: str = "follow",
         all_items: bool = False, access: str | None = None, mode: str | None = None,
         approved: str | None = None, dry_run: bool = False, message: str | None = None,
         title: str | None = None, with_deps: bool = False) -> dict:
    """Send knowledge from this store to neighbor *target*."""
    from . import bundle as bundles
    from .transfer import plan, transfer
    nb = neighbor(kb, target)
    if nb.relation == "twin":
        raise KBError(f"{target} is a twin: use kb sync")
    land = mode or nb.mode
    form = "all" if all_items else ("select" if refs else nb.push)
    if form == "none":
        raise KBError(f"pushing to {target} is disabled on this edge (push = none)")
    who = authorize(kb, target, approved)
    if land == "pr":
        tk = open_store(nb.location) if nb.available else None
    else:
        if not nb.writable:
            raise KBError(f"neighbor {target} has no store on this machine to write to (needs a path)")
        tk = open_store(nb.location, readonly=False)
    depth = "deep" if (deep or with_deps) else "shallow"
    if form == "all":
        if tk is None:
            raise KBError(f"'all' needs to see {target}'s store to know what it lacks")
        ids = changed_items(kb, tk)
        if not ids:
            return {"to": target, "result": "nothing new or changed"}
        if dry_run:
            return {"to": target, "dry_run": True, "would_send": ids}
        ks = make_kset(kb, ids, "shallow", title=title or f"All new or changed items for {target}")
    else:
        if not refs:
            raise KBError(f"name the items (or a knowledge set) to push to {target}; its edge is 'select'")
        if dry_run:
            members = [kb.get(parse_ref(r)[0]).id for r in refs]
            fake = kb.items[members[0]].data if len(members) == 1 and members[0].startswith("ks-") else \
                {"id": "ks-dryrun000", "kind": "kset", "members": members, "depth": depth, "nested": nested}
            if tk is None:
                return {"to": target, "dry_run": True, "note": "receiver not visible from here; it will review"}
            p = plan(kb, tk, fake, prefer="ours", access=access, relabel=nb.relabel)
            return {"to": target, "dry_run": True, "items": p.summary(), "dangling": p.dangling,
                    "notes": {i.id: i.notes for i in p.items if i.notes}, "relabeled": p.relabeled}
        ks = _selection(kb, refs, depth, nested, title)
    if land == "apply":
        back = edge_back(tk, kb)
        return transfer(kb, tk, ks.data, prefer=back.prefer if back else "ours", access=access,
                        relabel=nb.relabel, approved=who, edge=target, direction="push") | {"land": "apply"}
    b = bundles.make(kb, ks.id, note=message, approved=who)
    if land == "inbox":
        e = bundles.receive(tk, b)
        _log_out(kb, target, tk.cfg.id if tk else None, ks, b, who, "inbox")
        return {"to": target, "land": "inbox", "set": ks.id, "bundle": b["id"][:12], "status": e.status,
                "items": len(b["items"])}
    from .exchange import propose
    pr = propose(kb, nb, b, message, against=tk)
    _log_out(kb, target, tk.cfg.id if tk else None, ks, b, who, "pr", pr)
    return {"to": target, "land": "pr", "set": ks.id, "bundle": b["id"][:12]} | pr


def _log_out(kb, target, store_id, ks, b, who, land, extra=None):
    from .transfer import state
    with kb.tx():
        kb.log_event("transfer-out", ks.id, {
            "to": target, "store": store_id, "land": land, "bundle": b["id"],
            "items": [{"id": i["data"]["id"], "action": "sent", "state": state(Item(i["data"]))} for i in b["items"]]}
            | ({"pr": extra.get("pr", {}).get("url")} if extra else {}), approved=who)


def pull(kb: Store, source: str, refs: list[str], *, deep: bool = False, nested: str = "follow",
         all_items: bool = False, approved: str | None = None, dry_run: bool = False,
         land: str | None = None, message: str | None = None, src_kb: Store | None = None) -> dict:
    """Receive knowledge from neighbor *source* into this store (applied, or into the inbox for review)."""
    from . import bundle as bundles
    from .transfer import transfer
    nb = neighbor(kb, source)
    if nb.relation == "twin":
        raise KBError(f"{source} is a twin: use kb sync")
    if src_kb is None:
        if not nb.available:
            raise KBError(f"neighbor {source} is not available here (kb neighbor fetch {source}, or add a path)")
        src_kb = open_store(nb.location)
    form = "all" if all_items else ("select" if refs else nb.pull)
    if form == "none":
        raise KBError(f"pulling from {source} is disabled on this edge (pull = none)")
    who = approved or "human"
    if kb.cfg.is_agent:
        a = kb.cfg.autonomy.get("pull", "auto")
        level = a if isinstance(a, str) else a.get(source, a.get("default", "auto"))
        if level == "never":
            raise KBError(f"autonomy: pulling from {source} is reserved for the human")
        if level == "ask" and not approved:
            raise KBError(f"autonomy: pulling from {source} needs the human's approval; pass --approved")
        who = f"human: {approved}" if approved else "autonomy:auto"
    if form == "all":
        members, depth = changed_items(src_kb, kb, nb.prefer if nb.prefer != "newer" else "theirs"), "shallow"
        if not members:
            return {"from": source, "result": "nothing new or changed"}
    else:
        if not refs:
            raise KBError(f"name the items (or a set) to pull from {source}; its edge is 'select'")
        members, depth = [parse_ref(r)[0] for r in refs], ("deep" if deep else "shallow")
    land = land or ("inbox" if nb.accept == "review" else "apply")
    if len(members) == 1 and members[0].startswith("ks-") and members[0] in src_kb.items:
        ks_rec, extra = src_kb.get(members[0]), []       # the sender's own set travels with the items
    else:
        if dry_run:
            fake = {"id": "ks-dryrun000", "kind": "kset", "members": members, "depth": depth, "nested": nested}
            return transfer(src_kb, kb, fake, prefer=nb.prefer, approved=who, edge=source,
                            direction="pull", dry_run=True, log_source=False)
        ks_rec = make_kset(kb, members, depth, nested, title=f"Pull from {source}", save=False)
        extra = [ks_rec]
    if land == "inbox":
        if extra:
            # A bundle needs its set inside the sender's view: assemble it from src items plus our set.
            b = _bundle_from(src_kb, ks_rec, message, who)
        else:
            b = bundles.make(src_kb, ks_rec.id, note=message, approved=who)
        e = bundles.receive(kb, b, note=f"pulled from {source}")
        return {"from": source, "land": "inbox", "set": ks_rec.id, "bundle": b["id"][:12], "status": e.status}
    return transfer(src_kb, kb, ks_rec.data, prefer=nb.prefer, approved=who, edge=source,
                    direction="pull", dry_run=dry_run, extra=extra, log_source=False) | {"land": "apply"}


def _bundle_from(src: Store, ks_rec: Item, note, who) -> dict:
    """A bundle of items in *src* selected by a set made elsewhere."""
    import base64
    from . import bundle as bundles
    from .items import now
    from .transfer import expand
    exp = expand(ks_rec.data, lambda i: src.items[i].data if i in src.items else None)
    items, blobs = [], {}
    for ident in exp.items:
        if ident == ks_rec.id or ident not in src.items:
            continue
        rec = src.items[ident]
        items.append({"data": rec.data, "updated_at": rec.updated_at})
        from .items import blob_shas
        for sha in blob_shas(rec.data):
            data = src.blob(sha)
            if data is not None:
                blobs[sha] = base64.b64encode(data).decode()
    b = {"kb_bundle": bundles.FORMAT, "created": {"at": now(), "by": ks_rec.data["created"]["by"]},
         "from": {"store": src.cfg.id, "name": src.cfg.name, "scope": src.cfg.scope},
         "set": ks_rec.data, "set_updated_at": None, "items": items, "blobs": blobs,
         "missing": [{"from": f, "id": i} for f, i, _ in exp.missing]}
    if note:
        b["note"] = note
    b["approved"] = who
    b["id"] = bundles.bundle_id(b)
    return b


def obtain(kb: Store, refs: list[str] | None = None, *, deep: bool = False, dangling: bool = False,
           approved: str | None = None, dry_run: bool = False) -> list[dict]:
    """Fetch items referenced here (or named) from the nearest reachable store that has them."""
    from .federation import reachable
    from .lint import dangling as find_dangling
    wanted = [parse_ref(r)[0] for r in (refs or [])]
    if dangling:
        wanted += [parse_ref(r)[0] for _, r in find_dangling(kb)]
    wanted = [w for w in dict.fromkeys(wanted) if w not in kb.items]
    if not wanted:
        return []
    by_store: dict[str, tuple[Store, list[str]]] = {}
    left = list(wanted)
    for r in reachable(kb, 3, relations=("up", "peer", "down")):
        hits = [w for w in left if w in r.kb.items]
        if hits:
            by_store[r.label] = (r.kb, hits)
            left = [w for w in left if w not in hits]
    reports = []
    for label, (src, ids) in by_store.items():
        first_hop = label.split(">")[0]
        rep = pull(kb, first_hop, ids, deep=deep, approved=approved, dry_run=dry_run, src_kb=src, land="apply")
        rep["via"] = label
        reports.append(rep)
    if left:
        reports.append({"not_found": left})
    return reports
