"""``kb check``: the store's consistency gate.

Append-only is enforced by the database itself (triggers, see ``kb.db``);
this scan catches everything else that can be wrong: items that fail their
schema, kinds that disagree with IDs, content whose bytes are missing or do
not match their hash, columns tampered with outside kb, access leaks,
verified claims without real evidence.  Unresolved references are warnings:
references are store-independent (``kb obtain`` fetches them).
"""

import hashlib
import json
from dataclasses import dataclass

from . import schema
from .config import access_rank
from .ids import KIND, is_ref, parse_ref
from .records import KB, Record, core_digest, digest, fixed, is_frozen

LEVELS = ("public", "collaboration", "group", "private")


@dataclass
class Problem:
    level: str      # error | warning
    code: str
    where: str
    message: str
    hint: str = ""

    def __str__(self):
        s = f"{self.level}: {self.where}: [{self.code}] {self.message}"
        return s + (f"\n    hint: {self.hint}" if self.hint else "")

    def as_dict(self):
        return self.__dict__.copy()


def _err(code, where, msg, hint=""):
    return Problem("error", code, where, msg, hint)


def _warn(code, where, msg, hint=""):
    return Problem("warning", code, where, msg, hint)


def check_data(kb: KB, data: dict) -> list[Problem]:
    """Checks that need only this item and the store (used for incoming items too)."""
    rid = data.get("id", "?")
    probs = [_err("schema", rid, m, "kb schema " + str(data.get("kind", "KIND"))) for m in schema.validate(data)]
    if not isinstance(data.get("id"), str):
        return probs
    prefix = data["id"].split("-", 1)[0]
    if KIND.get(prefix) not in (None, data.get("kind")):
        probs.append(_err("id", rid, f"ID prefix {prefix} is for {KIND[prefix]}, not {data.get('kind')}"))
    rec = Record(data, kb)
    probs += _check_content(kb, rec)
    probs += _check_access(kb, rec)
    probs += _check_refs(kb, rec)
    return probs


def _check_content(kb: KB, rec: Record, deep: bool = False) -> list[Problem]:
    probs = []
    for r in rec.data.get("renditions", []):
        if not isinstance(r, dict):
            continue
        for loc in r.get("locations", []):
            if loc.get("type") != "db":
                continue
            sha = r.get("sha256")
            if not sha or not kb.has_blob(sha):
                probs.append(_err("missing", rec.id, f"rendition {r.get('name')}: content is not in the store",
                                  "re-add the file (kb content / kb rendition add)"))
            elif deep and hashlib.sha256(kb.blob(sha)).hexdigest() != sha:
                probs.append(_err("hash", rec.id, f"rendition {r.get('name')}: stored bytes do not match their hash"))
            if r.get("size") is not None and r["size"] > kb.cfg.max_inline_bytes:
                probs.append(_err("size", rec.id, f"rendition {r.get('name')} is {r['size']} bytes, over the "
                                                  f"{kb.cfg.max_inline_bytes} byte limit for the database",
                                  "keep large content in a content store or at a URL"))
    return probs


def _check_access(kb: KB, rec: Record) -> list[Problem]:
    level = rec.data.get("access")
    if level not in LEVELS:
        return []
    probs = []
    if access_rank(level) > access_rank(kb.cfg.access):
        probs.append(_err("access", rec.id, f"access {level} is more restricted than this store ({kb.cfg.access})",
                          "this item does not belong in this store"))
    for ref in rec.inputs():
        try:
            dep = kb.records.get(parse_ref(ref)[0])
        except Exception:  # noqa: BLE001
            continue
        if dep is not None and dep.data.get("access") in LEVELS and access_rank(dep.data["access"]) > access_rank(level):
            probs.append(_err("access", rec.id, f"is {level} but its input {dep.id} is {dep.data['access']}",
                              "a derived item may not be more open than any input"))
    return probs


def _check_refs(kb: KB, rec: Record) -> list[Problem]:
    probs = []
    d = rec.data
    fields = [(f, r) for f in ("supersedes", "retracts", "related", "members") for r in d.get(f, [])]
    fields += [("derived_from", x["ref"] if isinstance(x, dict) else x) for x in d.get("derived_from", [])]
    fields += [("evidence", e) for e in d.get("evidence", []) if isinstance(e, str) and "://" not in e]
    for field, ref in fields:
        if not isinstance(ref, str) or not is_ref(ref):
            continue
        if not kb.exists(ref):
            probs.append(_warn("dangling", rec.id, f"{field}: {ref} is not in this store",
                               "kb obtain " + ref + " (or fix the reference)"))
        elif parse_ref(ref)[0] == rec.id:
            probs.append(_err("ref", rec.id, f"{field}: refers to itself"))
    for ref in d.get("supersedes", []):
        if is_ref(ref) and parse_ref(ref)[0] in kb.records and kb.records[parse_ref(ref)[0]].kind != rec.kind:
            probs.append(_err("ref", rec.id, f"supersedes {ref}, a {kb.records[parse_ref(ref)[0]].kind}",
                              "an item supersedes one of its own kind; use retracts otherwise"))
    if d.get("status") == "verified":
        evidence = d.get("evidence", [])
        if not evidence:
            probs.append(_err("evidence", rec.id, "status is verified but there is no evidence",
                              "add evidence (a ref or URL) or set status unverified"))
        else:
            strong = False
            for e in evidence:
                if "://" in e or not is_ref(e):
                    strong = True
                    continue
                ident, name = parse_ref(e)
                dep = kb.records.get(ident)
                if dep is None:
                    strong = True   # can't judge what isn't here; dangling is reported separately
                    continue
                fids = [r.get("fidelity") for r in dep.renditions if name is None or r["name"] == name]
                if name is None or any(f != "llm" for f in fids):
                    strong = True
            if not strong:
                probs.append(_err("evidence", rec.id, "verified only by LLM-made renditions",
                                  "a model's reading is not evidence: cite an original or extracted rendition"))
    return probs


def check(kb: KB, ids: list[str] | None = None, deep: bool = False) -> list[Problem]:
    """Scan the store (or the items *ids*)."""
    probs: list[Problem] = []
    rows = kb.conn.execute("SELECT id, kind, json, digest, core, fixed, frozen FROM items").fetchall()
    for row in rows:
        if ids is not None and row["id"] not in ids:
            continue
        try:
            data = json.loads(row["json"])
        except json.JSONDecodeError as exc:
            probs.append(_err("json", row["id"], f"stored JSON is unreadable: {exc}"))
            continue
        if data.get("id") != row["id"] or data.get("kind") != row["kind"]:
            probs.append(_err("tamper", row["id"], "id/kind columns disagree with the item"))
        if row["core"] != core_digest(data) or json.loads(row["fixed"]) != fixed(data) \
                or bool(row["frozen"]) != is_frozen(data) or row["digest"] != digest(data):
            probs.append(_err("tamper", row["id"], "guard columns disagree with the item: it was changed outside kb",
                              "restore from a backup or the sender's copy"))
        probs += check_data(kb, data)
        if deep:
            probs += [p for p in _check_content(kb, Record(data, kb), deep=True) if p.code == "hash"]
    return probs

