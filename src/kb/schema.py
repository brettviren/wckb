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
REVISION = "1.3"
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
    return [_describe(e) for e in errors]


def _describe(error) -> str:
    path = "/".join(str(p) for p in error.absolute_path) or "(item)"
    if error.validator in ("oneOf", "anyOf") and error.context:
        # The nearest sub-error usually says what was actually wrong.
        best = max(error.context, key=lambda e: len(list(e.absolute_path)))
        return f"{path}: {best.message}"
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
