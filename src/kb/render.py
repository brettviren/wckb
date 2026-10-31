"""Rendering items, knowledge sets and bundles for people (and LLMs).

Everything is first turned into a *view model*: plain dicts with
human-friendly values (names instead of IDs where known, dates, sizes,
links, excerpts of the actual material; no hashes).  Formatters turn view
models into terminal text, markdown (for pull requests and LLMs) or JSON
(for programs, and later for a TUI or web view).  See docs/rendering.md for
where this is heading.
"""

import json
import textwrap
from datetime import datetime

from .ids import parse_ref

EXCERPT_LINES = 12
EXCERPT_CHARS = 900

ROLE_LABEL = {"original": "original", "content": "text", "derived": "derived", "metadata": "metadata",
              "source": "source file"}
FIDELITY_LABEL = {"lossless": "converted", "extracted": "extracted text", "llm": "LLM-written"}
TYPE_LABEL = {
    "application/pdf": "PDF", "text/markdown": "markdown", "text/plain": "text", "text/csv": "CSV table",
    "text/tab-separated-values": "TSV table", "application/json": "JSON", "application/x-root": "ROOT file",
    "application/x-hdf5": "HDF5 file", "application/vnd.apache.parquet": "Parquet table",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "slides (PowerPoint)",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "Word document",
    "text/x-python": "Python script", "text/x-makefile": "Makefile", "text/html": "HTML", "text/x-org": "org",
}
ID_LINKS = {
    "doi": "https://doi.org/{}", "arxiv": "https://arxiv.org/abs/{}", "inspire": "https://inspirehep.net/literature/{}",
    "cds": "https://cds.cern.ch/item/{}",
}


# -- small humanizers -------------------------------------------------------------------

def when(text: str | None) -> str:
    if not text:
        return ""
    try:
        t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text            # reduced dates like 2026 or 2026-10 read fine as they are
    return t.strftime("%d %b %Y") + ("" if len(text) <= 10 else t.strftime(" %H:%M UTC"))


def size(n: int | None) -> str:
    if n is None:
        return ""
    for unit in ("bytes", "kB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return ""


def runs(valid: dict | None) -> str:
    if not valid:
        return ""
    parts = []
    if valid.get("runs"):
        rr = [str(a) if a == b else f"{a}–{b}" for a, b in valid["runs"]]
        parts.append(("run " if len(rr) == 1 and "–" not in rr[0] else "runs ") + ", ".join(rr))
    t = valid.get("time", {})
    if t:
        parts.append(f"{when(t.get('start')) or '…'} to {when(t.get('end')) or 'now'}")
    return "; ".join(parts)


class Lookup:
    """Finds items for rendering: in a bundle first, then in a store."""

    def __init__(self, *holders):
        self.holders = [h for h in holders if h is not None]

    def get(self, ident: str):
        for h in self.holders:
            rec = h.items.get(ident)
            if rec is not None:
                return rec
        return None

    def name(self, agent: str) -> str:
        """A person's name for a per-… ref, else the text as given."""
        rec = None
        if agent.startswith("per-"):
            rec = self.get(agent)
            for h in self.holders:
                if rec is None and hasattr(h, "people"):
                    rec = h.people.get(agent)
        return rec.data.get("name", agent) if rec is not None else agent

    def title(self, ref: str) -> str:
        rec = self.get(parse_ref(ref)[0])
        return f"{rec.data.get('title', '')}" if rec is not None else "(not available here)"

    def text(self, rec, rendition: dict) -> str | None:
        for h in self.holders:
            if hasattr(h, "blob") and rendition.get("sha256"):
                b = h.blob(rendition["sha256"])
                if b is not None:
                    return b.decode("utf-8", errors="replace")
        return None


# -- view models ----------------------------------------------------------------------

def _excerpt(text: str) -> str:
    lines = [l.rstrip() for l in text.strip().splitlines()]
    out = "\n".join(lines[:EXCERPT_LINES])
    if len(out) > EXCERPT_CHARS:
        out = out[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …"
    elif len(lines) > EXCERPT_LINES:
        out += "\n…"
    return out


def _rendition_view(r: dict, store_kb=None) -> dict:
    from . import locations
    label = ROLE_LABEL.get(r["role"], r["role"])
    if r["role"] == "derived":
        label = FIDELITY_LABEL.get(r.get("fidelity"), "derived")
        tool = r.get("transform", {}).get("name")
        if tool and tool not in ("manual", "llm"):
            label += f" ({tool})"
    where = []
    for loc in r.get("locations", []):
        if loc["type"] == "db":
            where.append("held in the store")
        elif loc["type"] == "url":
            where.append("online" + (f", needs {loc['auth']}" if loc.get("auth") else ""))
        elif store_kb is not None:
            where.append(locations.for_location(loc).describe(store_kb, loc))
        else:
            where.append({"cas": f"content store '{loc.get('name')}'", "local": f"only on {loc.get('host')}",
                          "annex": f"DataLad dataset {loc.get('dataset')}"}.get(loc["type"], loc["type"]))
    return {"name": r["name"], "label": label, "type": TYPE_LABEL.get(r["media_type"], r["media_type"]),
            "size": size(r.get("size")), "where": where, "links": locations.links(r)}


def _links(d: dict, settings: dict | None = None) -> list[tuple[str, str]]:
    out = []
    for k, v in (d.get("identifiers") or {}).items():
        if k == "url":
            out.append(("web", v))
        elif k in ID_LINKS:
            out.append((k, ID_LINKS[k].format(v)))
        elif k == "indico" and settings and settings.get("links", {}).get("indico"):
            out.append(("indico", settings["links"]["indico"].rstrip("/") + "/" + v))
    ev = d.get("event") or {}
    if ev.get("url"):
        out.append(("event", ev["url"]))
    if d.get("repository"):
        out.append(("repository", d["repository"]))
    return out


def _facts(d: dict, look: Lookup) -> list[tuple[str, str]]:
    kind, f = d["kind"], []
    if d.get("asserted_by"):
        for a in d["asserted_by"]:
            f.append(("asserted by", ", ".join(x for x in (look.name(a["who"]), a.get("how"), when(a.get("when"))) if x)))
    if d.get("creators"):
        f.append(("by", ", ".join(look.name(c) for c in d["creators"])))
    if d.get("date"):
        f.append(("dated", when(d["date"])))
    if d.get("valid"):
        f.append(("applies to", runs(d["valid"])))
    if kind == "document":
        f.append(("type", d.get("doctype", "")))
        if d.get("event"):
            f.append(("event", ", ".join(x for x in (d["event"].get("title"), when(d["event"].get("date"))) if x)))
        if d.get("venue"):
            f.append(("venue", d["venue"]))
    if kind in ("config", "snapshot") and d.get("system"):
        f.append(("system", d["system"]))
    if kind == "config" and d.get("parameters"):
        for k, v in list(d["parameters"].items())[:12]:
            f.append((f"  {k}", json.dumps(v) if not isinstance(v, str) else v))
    if kind == "snapshot":
        f.append(("query", d.get("query", "")))
        f.append(("retrieved", when(d.get("retrieved"))))
    if kind == "dataset":
        if d.get("format"):
            f.append(("format", d["format"]))
        for v in (d.get("variables") or [])[:12]:
            f.append((f"  {v['name']}", " ".join(x for x in (v.get("description"), f"[{v['unit']}]" if v.get("unit") else "") if x)))
    if kind == "software":
        f.append(("version", d.get("version") or (d.get("commit") or "")[:10]))
    if kind == "view":
        b = d.get("build", {})
        f.append(("built with", " ".join(x for x in (b.get("tool"), b.get("target"), ",".join(b.get("formats", []))) if x)))
        if d.get("audience"):
            f.append(("for", d["audience"]))
    if kind == "policy":
        f.append(("policy", f"{d.get('name')} version {d.get('version')} ({d.get('policy_type')}, {d.get('stage')})"))
    if kind == "person":
        f.append(("name", " · ".join(x for x in (d.get("name"), d.get("affiliation")) if x)))
    if kind == "kset":
        f.append(("selects", f"{len(d.get('members', []))} item(s), {d.get('depth')}"
                  + (f", nested sets: {d['nested']}" if d.get("nested") else "")))
    if kind == "asset":
        vs = d.get("versions", [])
        f.append(("asset", f"{'one file' if d.get('asset_type') == 'file' else 'a file set'}, "
                           f"{len(vs)} version(s)" + (f", latest {vs[-1]['label']}" if vs else "")))
        for v in vs[-5:]:
            total = sum(x.get("size") or 0 for x in v.get("files", []))
            f.append((f"  {v['label']}", ", ".join(x for x in (
                f"{len(v.get('files', []))} file(s)", size(total), when(v.get("registered")),
                "seen in " + ", ".join(sorted({s["binding"] for s in v.get("sightings", [])})) if v.get("sightings") else "",
                v.get("note", "")) if x)))
        for b in d.get("bindings", []):
            f.append((f"  source {b['name']}", _binding_text(b)))
    if d.get("subject"):
        f.append(("subjects", ", ".join(d["subject"])))
    if d.get("license"):
        f.append(("license", d["license"]))
    o = d.get("origin")
    if o:
        f.append(("came from", f"{o.get('name', o.get('store'))} ({o.get('scope', '')}), {when(o.get('pushed_at'))}"
                  + (f", approved by {o['approved']}" if o.get("approved") else "")))
    f.append(("recorded", f"{when(d['created']['at'])} by {look.name(d['created']['by'])}"))
    return [(k, v) for k, v in f if v]


def _binding_text(b: dict) -> str:
    loc = b.get("locator", {})
    if b["type"] == "fs":
        where = f"directory {loc.get('root')}:{loc.get('path', '')}"
    elif b["type"] == "url":
        where = loc.get("url") or f"{len(loc.get('urls', {}))} URLs"
    elif b["type"] == "kb":
        where = "kept in the store"
    else:
        where = f"{b['type']} " + ", ".join(f"{k}={v}" for k, v in loc.items())
    v = b.get("versioning", "none")
    return where + ("" if v == "none" else f" (versions: {b.get('pattern') or v})")


def _relations(d: dict, look: Lookup) -> list[tuple[str, str, str]]:
    out = []
    for field_, label in (("supersedes", "corrects"), ("retracts", "withdraws"), ("evidence", "evidence"),
                          ("derived_from", "built from"), ("related", "related")):
        for x in d.get(field_, []):
            ref = x["ref"] if isinstance(x, dict) else x
            if "://" in ref:
                out.append((label, ref, ref))
            else:
                out.append((label, parse_ref(ref)[0], look.title(ref)))
    return out


def item_view(rec, look: Lookup, store_kb=None, settings: dict | None = None) -> dict:
    d = rec.data
    badges = [b for b in (d.get("status"), d.get("access"), d.get("stage")) if b]
    rends = [_rendition_view(r, store_kb) for r in d.get("renditions", []) if r["name"] != "manifest"]
    excerpt = None
    for role in ("content", "derived", "original"):
        for r in d.get("renditions", []):
            if r["role"] == role and (r["media_type"].startswith("text/") or r["media_type"] == "application/json"):
                t = look.text(rec, r)
                if t:
                    excerpt = {"from": _rendition_view(r)["label"], "text": _excerpt(t)}
                    break
        if excerpt:
            break
    links = _links(d, settings)
    for r in rends:
        links += [(r["label"], u) for u in r["links"] if (r["label"], u) not in links]
    return {"id": rec.id, "kind": d["kind"], "title": d.get("title", ""), "summary": d.get("summary", ""),
            "badges": badges, "facts": _facts(d, look), "links": links, "renditions": rends,
            "excerpt": excerpt, "relations": _relations(d, look)}


def tree_view(kset: dict, look: Lookup, max_depth: int = 4) -> dict:
    """The set as a tree: members, nested sets' members (per their depth), and missing leaves."""
    def node(ident: str, depth: int, seen: frozenset) -> dict:
        rec = look.get(ident)
        if rec is None:
            return {"id": ident, "title": "(not available here)", "kind": "?", "missing": True, "children": []}
        n = {"id": ident, "title": rec.data.get("title", ""), "kind": rec.kind, "missing": False, "children": []}
        if rec.kind == "kset" and ident not in seen:
            if depth >= max_depth:
                n["more"] = len(rec.data.get("members", []))
            else:
                n["depth"] = rec.data.get("depth")
                n["children"] = [node(parse_ref(m)[0], depth + 1, seen | {ident}) for m in rec.data.get("members", [])]
        return n
    root = node(kset["id"], 0, frozenset()) if look.get(kset["id"]) else {
        "id": kset["id"], "title": kset.get("title", ""), "kind": "kset", "missing": False,
        "children": [node(parse_ref(m)[0], 1, frozenset({kset["id"]})) for m in kset.get("members", [])]}
    root["depth"] = kset.get("depth")
    return root


def _field_changes(old: dict, new: dict) -> list[str]:
    out = []
    for k in sorted(set(old) | set(new)):
        if k in ("renditions", "origin", "derived_from") or old.get(k) == new.get(k):
            continue
        a, b = old.get(k), new.get(k)
        fmt = lambda v: "(none)" if v is None else (", ".join(map(str, v)) if isinstance(v, list) else str(v))  # noqa: E731
        out.append(f"{k}: {fmt(a)} → {fmt(b)}")
    return out


def bundle_view(b: dict, store_kb=None, max_depth: int = 4) -> dict:
    from .bundle import BundleSender, check
    src = BundleSender(b)
    look = Lookup(src, store_kb)
    view = {"bundle": b["id"][:12], "from": b["from"], "created": {"at": when(b["created"]["at"]),
                                                                       "by": look.name(b["created"]["by"])},
            "note": b.get("note"), "approved": b.get("approved"),
            "set": item_view(src.items[b["set"]["id"]], look, settings=store_kb.cfg.raw if store_kb else None),
            "tree": tree_view(b["set"], look, max_depth), "missing": b.get("missing", [])}
    if store_kb is not None:
        chk = check(store_kb, b)
        view["check"] = chk
        plan = chk.get("plan") or {}
        groups = []
        for action, label in (("conflict", "Conflicts"), ("new", "New"), ("update", "Changed"), ("same", "Already here")):
            ids = plan.get(action, [])
            if not ids:
                continue
            cards = []
            for i in ids:
                rec = src.items[i]
                v = item_view(rec, look, store_kb, store_kb.cfg.raw)
                if action == "update" and i in store_kb.items:
                    v["changes"] = _field_changes(store_kb.items[i].data, rec.data)
                if i in chk.get("notes", {}):
                    v["notes"] = chk["notes"][i]
                cards.append(v)
            groups.append({"action": action, "label": label, "items": cards})
        view["groups"] = groups
    else:
        view["groups"] = [{"action": "all", "label": "Items",
                           "items": [item_view(r, look) for i, r in src.items.items() if i != b["set"]["id"]]}]
    return view


# -- formatters ----------------------------------------------------------------------

def _tree_lines(n: dict, prefix: str = "", last: bool = True, top: bool = True) -> list[str]:
    mark = "" if top else ("└─ " if last else "├─ ")
    extra = " (missing here)" if n.get("missing") else (f" [{n['depth']}]" if n.get("depth") else "")
    if n.get("more"):
        extra += f" … {n['more']} more"
    lines = [f"{prefix}{mark}{n['title'] or n['id']}  ({n['kind']} {n['id']}){extra}"]
    kids = n.get("children", [])
    for i, c in enumerate(kids):
        lines += _tree_lines(c, prefix + ("" if top else ("   " if last else "│  ")), i == len(kids) - 1, False)
    return lines


def item_text(v: dict, md: bool = False, width: int = 88) -> str:
    h = "### " if md else ""
    out = [f"{h}{v['title']}", f"{v['kind']} {v['id']}" + (f"  [{', '.join(v['badges'])}]" if v["badges"] else "")]
    if v["summary"]:
        out.append(textwrap.fill(v["summary"], width) if not md else v["summary"])
    if v.get("changes"):
        out.append("Changes: " + "; ".join(v["changes"]))
    for n in v.get("notes", []):
        out.append(f"Note: {n}")
    if v["facts"]:
        out.append("")
        for k, val in v["facts"]:
            out.append(f"- **{k.strip()}:** {val}" if md else f"  {k:<14} {val}")
    if v["relations"]:
        out.append("")
        for label, ident, title in v["relations"]:
            out.append(f"- {label}: {title} (`{ident}`)" if md else f"  {label:<14} {title} ({ident})")
    if v["renditions"]:
        out.append("")
        for r in v["renditions"]:
            line = f"{r['label']}: {r['type']}" + (f", {r['size']}" if r["size"] else "") + \
                   (f", {'; '.join(r['where'])}" if r["where"] else "")
            out.append(f"- {line}" if md else f"  · {line}")
    if v["links"]:
        out.append("")
        for label, url in v["links"]:
            out.append(f"- [{label}]({url})" if md else f"  ↗ {label}: {url}")
    if v["excerpt"]:
        out.append("")
        if md:
            out.append(f"Excerpt ({v['excerpt']['from']}):\n\n> " + v["excerpt"]["text"].replace("\n", "\n> "))
        else:
            out.append(f"  ┌ excerpt ({v['excerpt']['from']})")
            out += [f"  │ {l}" for l in v["excerpt"]["text"].splitlines()]
            out.append("  └")
    return "\n".join(out)


def bundle_text(view: dict, md: bool = False) -> str:
    f = view["from"]
    hd = "## " if md else ""
    out = [f"{'# ' if md else ''}Bundle {view['bundle']} from {f.get('name', f['store'])} ({f.get('scope', '')})",
           f"made {view['created']['at']} by {view['created']['by']}"
           + (f"; approved: {view['approved']}" if view.get("approved") else "")]
    if view.get("note"):
        out.append(f"Note: {view['note']}")
    out += ["", f"{hd}The set", item_text(view["set"], md)]
    chk = view.get("check")
    if chk:
        out += ["", f"{hd}What accepting it would do"]
        plan = chk.get("plan")
        if plan is None:
            out.append(f"Cannot apply: {chk.get('error')}")
        else:
            out.append(", ".join(f"{len(plan[a])} {lab}" for a, lab in
                                 (("new", "new"), ("update", "changed"), ("same", "already here"), ("conflict", "conflicting"))))
            if chk.get("dangling"):
                out.append(f"References it leaves unresolved here: {len(chk['dangling'])} (kb pull can fetch them later)")
        for p in chk.get("problems", []):
            out.append(f"Problem: {p['where']}: {p['message']}")
        out.append("Verdict of the checks: " + ("OK" if chk.get("ok") else "needs attention"))
    out += ["", f"{hd}Contents"] + (["```"] if md else [])
    out += _tree_lines(view["tree"])
    if md:
        out.append("```")
    for g in view["groups"]:
        if g["action"] == "same":
            out += ["", f"{hd}{g['label']} ({len(g['items'])})"]
            out += [f"- {i['title']} ({i['id']})" for i in g["items"]]
            continue
        out += ["", f"{hd}{g['label']} ({len(g['items'])})"]
        for i in g["items"]:
            if i["kind"] == "kset":       # sets are shown by the tree; one line here
                sel = dict(i["facts"]).get("selects", "")
                out += ["", f"{'- ' if md else ''}Set: {i['title']} ({i['id']}), {sel}"]
                continue
            out += ["", item_text(i, md)]
    return "\n".join(out).replace("\n\n\n", "\n\n") + "\n"


def to_json(view: dict) -> str:
    return json.dumps(view, indent=2, ensure_ascii=False)
