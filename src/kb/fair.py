"""FAIR exports: RO-Crate packages and Frictionless data package descriptors."""

import csv
import json
import re
import shutil
from pathlib import Path

from . import content
from .errors import KBError
from .items import Store, Item

ROCRATE_CONTEXT = "https://w3id.org/ro/crate/1.1/context"


def _entity_type(rec: Item) -> str:
    return {"document": "CreativeWork", "dataset": "Dataset", "software": "SoftwareSourceCode",
            "person": "Person", "observation": "Claim", "topic": "Article", "view": "CreativeWork",
            "config": "PropertyValue", "snapshot": "Dataset"}.get(rec.kind, "CreativeWork")


def rocrate(kb: Store, ids: list[str], outdir: Path, copy: bool = True) -> Path:
    """Write an RO-Crate 1.1 for *ids* (and the persons they name) into *outdir*."""
    outdir.mkdir(parents=True, exist_ok=True)
    graph = [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork",
         "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"}, "about": {"@id": "./"}},
    ]
    root = {"@id": "./", "@type": "Dataset", "name": f"{kb.cfg.name}: export of {len(ids)} item(s)",
            "hasPart": []}
    graph.append(root)
    for ident in ids:
        rec = kb.get(ident)
        d = rec.data
        ent = {"@id": f"#{rec.id}", "@type": _entity_type(rec), "identifier": rec.id,
               "name": d["title"], "description": d["summary"],
               "dateCreated": d["created"]["at"], "additionalType": f"kb:{rec.kind}"}
        if d.get("date"):
            ent["datePublished"] = d["date"]
        if d.get("license"):
            ent["license"] = d["license"]
        if d.get("subject"):
            ent["keywords"] = d["subject"]
        if d.get("identifiers", {}).get("doi"):
            ent["sameAs"] = f"https://doi.org/{d['identifiers']['doi']}"
        authors = d.get("creators") or [a["who"] for a in d.get("asserted_by", [])]
        if authors:
            ent["author"] = [{"@id": f"#{a}"} if a in kb.items else a for a in authors]
        if rec.inputs():
            ent["isBasedOn"] = [{"@id": f"#{r.split('#')[0]}"} for r in rec.inputs()]
        if d.get("supersedes"):
            ent["isBasedOn"] = ent.get("isBasedOn", []) + [{"@id": f"#{r}"} for r in d["supersedes"]]
        parts = []
        for r in rec.renditions:
            fid = None
            path = content.resolve(kb, rec, r, fetch_urls=False)
            if copy and path is not None:
                fn = next((l.get("filename") for l in r["locations"] if l["type"] == "db"), None)
                rel = Path(rec.id) / (fn or Path(path).name)
                (outdir / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, outdir / rel)
                fid = str(rel)
            else:
                urls = [l["url"] for l in r["locations"] if l["type"] == "url"]
                fid = urls[0] if urls else f"#{rec.id}/{r['name']}"
            f = {"@id": fid, "@type": "File", "name": r["name"], "encodingFormat": r["media_type"]}
            if r.get("sha256"):
                f["sha256"] = r["sha256"]
            if r.get("size") is not None:
                f["contentSize"] = str(r["size"])
            graph.append(f)
            parts.append({"@id": fid})
        if parts:
            ent["hasPart"] = parts
        graph.append(ent)
        root["hasPart"].append({"@id": f"#{rec.id}"})
    persons = {a for e in graph for a in [x.get("@id", "")[1:] for x in e.get("author", []) if isinstance(x, dict)]}
    for pid in sorted(persons):
        p = kb.items.get(pid)
        if p is not None:
            graph.append({"@id": f"#{pid}", "@type": "Person", "name": p.data.get("name", pid),
                          **({"affiliation": p.data["affiliation"]} if p.data.get("affiliation") else {})})
    meta = outdir / "ro-crate-metadata.json"
    meta.write_text(json.dumps({"@context": ROCRATE_CONTEXT, "@graph": graph}, indent=2) + "\n")
    return meta


# -- Frictionless data package inference -----------------------------------

_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _field_type(values: list[str]) -> str:
    vals = [v for v in values if v not in ("", "NA", "NaN", "nan", "null")]
    if not vals:
        return "string"
    for t, test in (("integer", _INT.match), ("number", _NUM.match),
                    ("boolean", lambda v: v.lower() in ("true", "false")),
                    ("date", _DATE.match), ("datetime", _DATETIME.match)):
        if all(test(v) for v in vals):
            return t
    return "string"


def infer_resource(path: Path, name: str, sample: int = 1000) -> dict:
    delim = "\t" if path.suffix.lower() in (".tsv", ".tab") else ","
    with open(path, newline="") as f:
        reader = csv.reader(f, delimiter=delim)
        header = next(reader, None)
        if header is None:
            raise KBError(f"{path} is empty")
        cols = [[] for _ in header]
        rows = 0
        for row in reader:
            rows += 1
            if rows <= sample:
                for i, v in enumerate(row[:len(header)]):
                    cols[i].append(v)
    return {
        "name": re.sub(r"[^a-z0-9._-]", "-", name.lower()),
        "path": path.name, "format": "tsv" if delim == "\t" else "csv",
        "mediatype": "text/tab-separated-values" if delim == "\t" else "text/csv",
        "dialect": {"delimiter": delim},
        "schema": {"fields": [{"name": h, "type": _field_type(c)} for h, c in zip(header, cols)]},
        "x-rows": rows,
    }


def describe_files(paths: list[Path]) -> dict:
    """A Frictionless data package describing CSV/TSV files (nothing is stored)."""
    bad = [p for p in paths if p.suffix.lower() not in (".csv", ".tsv", ".tab")]
    if bad:
        raise KBError(f"not CSV/TSV: {', '.join(map(str, bad))}")
    resources = []
    for p in paths:
        res = infer_resource(p, p.stem)
        res["hash"] = f"sha256:{content.sha256_file(p)[0]}"
        resources.append(res)
    return {"name": re.sub(r"[^a-z0-9._-]", "-", (paths[0].parent.resolve().name or "data").lower()),
            "resources": resources}


def datapackage(kb: Store, ident: str) -> dict:
    """Infer datapackage.json for a dataset's CSV/TSV renditions and add it as a metadata rendition."""
    rec = kb.get(ident)
    if rec.kind != "dataset":
        raise KBError(f"{rec.id} is a {rec.kind}; data packages describe datasets")
    resources = []
    for r in rec.renditions:
        if r["media_type"] in ("text/csv", "text/tab-separated-values"):
            p = content.resolve(kb, rec, r)
            if p is None:
                raise KBError(f"{rec.id}#{r['name']} is not reachable")
            res = infer_resource(p, r["name"])
            res["path"] = next((l.get("filename") for l in r["locations"] if l["type"] == "db" and l.get("filename")),
                               next((l["url"] for l in r["locations"] if l["type"] == "url"), res["path"]))
            if r.get("sha256"):
                res["hash"] = f"sha256:{r['sha256']}"
            resources.append(res)
    if not resources:
        raise KBError(f"{rec.id} has no CSV/TSV renditions")
    pkg = {"name": rec.id, "title": rec.data["title"], "description": rec.data["summary"],
           "resources": resources}
    if rec.data.get("license"):
        pkg["licenses"] = [{"name": rec.data["license"]}]
    import copy as _copy
    from .items import Item
    data = _copy.deepcopy(rec.data)
    with kb.tx():
        locs, sha, size = content.place_bytes(kb, (json.dumps(pkg, indent=2) + "\n").encode(), "datapackage.json")
        meta = {"name": "datapackage", "role": "metadata", "media_type": "application/json",
                "sha256": sha, "size": size, "locations": locs}
        data["renditions"] = [r for r in data.get("renditions", []) if r["name"] != "datapackage"] + [meta]
        kb.save(Item(data, kb), "datapackage")
    return pkg
