"""Render the schemas as a markdown reference (docs/reference/schema.md)."""

from .ids import KINDS, PREFIX, SOURCE_KINDS
from .schema import REVISION, load_schemas


def _type(p: dict) -> str:
    if "$ref" in p:
        return p["$ref"].split("/")[-1].replace(".json", "")
    if "const" in p:
        return f"`{p['const']}`"
    if "enum" in p:
        return " \\| ".join(f"`{e}`" for e in p["enum"])
    t = p.get("type", "")
    if t == "array" and "items" in p:
        return f"list of {_type(p['items'])}"
    return t or "(see schema)"


def _rows(props: dict, required) -> list[str]:
    out = []
    for name, p in props.items():
        if name in ("kind", "id") and set(p) <= {"const", "type", "pattern"}:
            continue
        desc = p.get("description", "").replace("|", "\\|")
        out.append(f"| `{name}` | {'yes' if name in required else ''} | {_type(p)} | {desc} |")
    return out


def render() -> str:
    s = load_schemas()
    lines = [
        "# Record schema reference",
        "",
        f"Generated from the JSON Schemas in `src/kb/schemas/v1/` (revision {REVISION}) by",
        "`kb schema --markdown`; a test keeps this file in sync. `kb schema KIND` prints",
        "the schema itself, which is authoritative.",
        "",
        "## Fields common to every record",
        "",
        "| field | required | type | description |",
        "|---|---|---|---|",
        *_rows(s["record"]["properties"], s["record"]["required"]),
        "",
    ]
    for kind in KINDS:
        k = s[kind]
        props = {n: p for n, p in k["properties"].items() if n not in ("kind", "id")}
        lines += [f"## {kind}", "",
                  f"ID prefix `{PREFIX[kind]}-`; {'a source (append-only)' if kind in SOURCE_KINDS else 'not a source (editable)'}.",
                  "", k.get("description", ""), ""]
        if props or k.get("required"):
            lines += ["| field | required | type | description |", "|---|---|---|---|",
                      *_rows(props, k.get("required", []))]
            extra = [r for r in k.get("required", []) if r not in props]
            if extra:
                lines.append("")
                lines.append("Also required here: " + ", ".join(f"`{r}`" for r in extra) + ".")
            lines.append("")
    defs = s["common"]["$defs"]
    lines += ["## Shared definitions", ""]
    for name in ("rendition", "location", "valid", "assertion", "identifiers", "derivation", "origin"):
        d = defs[name]
        lines.append(f"### {name}")
        lines.append("")
        if d.get("description"):
            lines += [d["description"], ""]
        if "properties" in d:
            lines += ["| field | required | type | description |", "|---|---|---|---|",
                      *_rows(d["properties"], d.get("required", [])), ""]
        elif "oneOf" in d:
            for alt in d["oneOf"]:
                t = alt["properties"]["type"]["const"]
                fields = ", ".join(f"`{f}`" for f in alt["properties"] if f != "type")
                lines.append(f"- `{t}`: {fields}")
            lines.append("")
        elif "anyOf" in d:
            lines += ["A ref string, or `{ref, digest}` once stamped.", ""]
    return "\n".join(lines).rstrip() + "\n"
