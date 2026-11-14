"""JSON Schema validation of items.

Schemas are hand-written JSON Schema (draft 2020-12) files shipped in
``kb/schemas/v<major>/``.  Their ``$id`` URIs live under the unresolvable
``https://schemas.kb.invalid/`` and are served from the package, never fetched.

``REVISION`` is the schema revision this package implements.  Additive
changes bump the minor part and leave items alone; breaking changes bump
the major part, which is the ``schema_version`` written in every item, and
come with a migration (see ``kb.migrate``).
"""

import json
from functools import cache
from importlib import resources

from .errors import KBError
from .ids import KINDS

MAJOR = 1
REVISION = "1.12"
BASE_URI = "https://schemas.kb.invalid/v%d/"


def _files(major: int):
    return resources.files("kb") / "schemas" / f"v{major}"


@cache
def load_schemas(major: int = MAJOR) -> dict[str, dict]:
    """Every schema of a major version, by file stem."""
    out = {}
    for f in _files(major).iterdir():
        if f.name.endswith(".json"):
            out[f.name[:-5]] = json.loads(f.read_text())
    return out


@cache
def _validators(major: int = MAJOR):
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource

    schemas = load_schemas(major)
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas.values()
    )
    return {k: Draft202012Validator(schemas[k], registry=registry) for k in KINDS}


def validate(data: dict) -> list[str]:
    """Problems with *data* as ``path: message`` strings; empty when valid."""
    if not isinstance(data, dict):
        return ["item is not a JSON object"]
    kind = data.get("kind")
    if kind not in KINDS:
        return [f"kind: {kind!r} is not one of {', '.join(KINDS)}"]
    version = data.get("schema_version")
    if version != MAJOR:
        return [f"schema_version: {version!r}, but this kb implements {MAJOR} (run kb store migrate, or upgrade kb)"]
    validator = _validators(MAJOR)[kind]
    errors = sorted(validator.iter_errors(data), key=lambda e: list(map(str, e.absolute_path)))
    if any(e.validator != "unevaluatedProperties" for e in errors):
        # A failing allOf part leaves its properties "unevaluated": noise next to the real error.
        errors = [e for e in errors if e.validator != "unevaluatedProperties"]
    return [_describe(e) for e in errors]


#: The command-line option that gives a field, for messages about a missing or bad one.
OPTION = {"tags": "--tag", "links": "--link RELATION=ID", "identifiers": "--identifier SCHEME=VALUE",
          "valid": "--runs, --start, --end", "status": "--status", "access": "--access",
          "derived_from": "--derived-from ID", "dates": "--dated QUALIFIER=DATE", "title": "--title", "summary": "--summary", "creators": "--creators", "date": "--date",
          "doctype": "--set doctype=paper|preprint|presentation|manual|report|other|...",
          "asserted_by": "--by, --how, --when", "subject": "--subject", "system": "--set system=...",
          "parameters": "--set 'parameters={...}'", "query": "--set query=...", "retrieved": "--set retrieved=DATE"}


def _describe(error) -> str:
    path = "/".join(str(p) for p in error.absolute_path) or "(item)"
    if error.validator in ("oneOf", "anyOf") and error.context and all(
            e.validator == "required" for e in error.context):
        # alternatives, each missing: name them all, and how to give each
        names = [e.message.split("'")[1] for e in error.context if "'" in e.message]
        return f"{path}: needs one of " + ", ".join(
            f"'{n}'" + (f" (give {OPTION[n]})" if n in OPTION else "") for n in dict.fromkeys(names))
    if error.validator in ("oneOf", "anyOf") and error.context:
        # The nearest sub-error usually says what was actually wrong.
        best = max(error.context, key=lambda e: len(list(e.absolute_path)))
        return f"{path}: {best.message}"
    if error.validator == "required":
        missing = error.message.split("'")[1] if "'" in error.message else ""
        if missing in OPTION:
            return f"{path}: {error.message} (give {OPTION[missing]})"
    if error.validator == "maxLength":
        return f"{path}: too long: {len(error.instance)} characters, at most {error.validator_value}"
    if error.validator == "unevaluatedProperties" and "asserted_by" in error.message:
        return (f"{path}: --by/--how/--when attribute the assertion of an observation or config; a "
                f"{error.instance.get('kind', 'item')} has no such field (its authors are --creators; who "
                "filed it is recorded anyway)")
    return f"{path}: {error.message}"


def show(kind: str) -> dict:
    """The schema of *kind*, for humans and LLMs."""
    if kind not in load_schemas():
        raise KBError(f"no schema {kind!r}; have: {', '.join(sorted(load_schemas()))}")
    return load_schemas()[kind]


def check_revision(required: str) -> None:
    """Fail if the store requires a newer schema revision than this package has."""
    def parse(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except ValueError:
            raise KBError(f"bad schema revision {v!r} in the store's settings") from None
    if parse(required) > parse(REVISION):
        raise KBError(f"this store needs schema {required} but kb implements {REVISION}: upgrade kb")


def field_guide(kind: str, major: int = MAJOR) -> list[dict]:
    """The fields of an item of KIND, for people: name, required, how to give it (option), allowed values
    (enum), and what it is; required ones first, then those of the kind, then those every item has."""
    if kind not in KINDS:
        raise KBError(f"no kind {kind!r} (kinds: {', '.join(KINDS)})")
    schemas = load_schemas(major)
    own, base = schemas.get(kind, {}), schemas["item"]
    required = set(own.get("required", [])) | set(base.get("required", []))
    for part in own.get("allOf", []):           # alternatives, as for a document's date or dates
        for alt in part.get("anyOf", []):
            required |= {f"{r} (or another)" for r in alt.get("required", [])} - required
    skip = {"id", "kind", "schema_version", "created", "renditions", "origin", "x"}

    defs = schemas.get("common", {}).get("$defs", {})

    def deref(spec: dict) -> dict:
        ref = spec.get("$ref", "")
        return defs.get(ref.rsplit("/", 1)[-1], spec) if "#/$defs/" in ref else spec

    def first_sentence(text: str) -> str:
        cut = text.find(". ", 40)
        return text if cut < 0 or len(text) <= 140 else text[:cut + 1]

    def enum_of(spec: dict):
        spec = deref(spec)
        if "enum" in spec:
            return spec["enum"]
        if spec.get("type") == "object" and spec.get("properties") and spec.get("additionalProperties") is False:
            return list(spec["properties"])         # qualifiers (dates): the keys it may have
        items = spec.get("items") or {}
        if isinstance(items, dict) and "enum" in items:
            return items["enum"]
        rel = (items.get("properties") or {}).get("rel") if isinstance(items, dict) else None
        return rel.get("enum") if rel else None

    out = []
    for source, props in (("kind", own.get("properties", {})), ("item", base.get("properties", {}))):
        for name, spec in props.items():
            if name in skip or any(f["name"] == name for f in out) or not isinstance(spec, dict):
                continue
            if spec.get("const") is not None:
                continue
            req = name in required or f"{name} (or another)" in required
            out.append({"name": name, "required": req, "either": f"{name} (or another)" in required,
                        "option": OPTION.get(name) or (f"--{name.replace('_', '-')}" if name in _OPTIONS else
                                                       f"--set {name}=..."),
                        "enum": enum_of(spec),
                        "description": first_sentence(spec.get("description") or deref(spec).get("description") or ""),
                        "of": source})
    return sorted(out, key=lambda f: (not f["required"], f["of"] != "kind"))


#: Fields with an option of their own (else --set NAME=...).
_OPTIONS = {"title", "summary", "subject", "tags", "status", "access", "creators", "date", "identifiers",
            "evidence", "related", "derived_from", "license", "links"}
