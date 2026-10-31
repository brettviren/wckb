"""Item IDs and references.

An ID is a kind prefix and a random suffix, e.g. ``obs-7k2m9q4xha``.  Random
suffixes need no central allocator, so items made in different stores can
always be merged.  A reference is an ID, optionally with ``#rendition``.
"""

import re
import secrets

from .errors import KBError

PREFIX = {
    "observation": "obs",
    "document": "doc",
    "dataset": "dat",
    "config": "cfg",
    "snapshot": "snp",
    "software": "sw",
    "topic": "top",
    "view": "view",
    "person": "per",
    "policy": "pol",
    "kset": "ks",
    "asset": "as",
}
KIND = {v: k for k, v in PREFIX.items()}
KINDS = tuple(PREFIX)

# Sources state what was asserted and are append-only; the rest are derived
# (topic, view) or descriptive (person) and may be edited.  Policies are
# append-only once released (see checks).
PRIMARY_KINDS = frozenset({"observation", "document", "dataset", "config", "snapshot", "software", "kset"})

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32, lower case
_ID = re.compile(r"^(%s)-[a-z0-9][a-z0-9-]{2,62}$" % "|".join(KIND))
_REF = re.compile(r"^(?P<id>(%s)-[a-z0-9][a-z0-9-]{2,62})(#(?P<rendition>[A-Za-z0-9_.-]+))?$" % "|".join(KIND))


def new_id(kind: str, slug: str | None = None) -> str:
    """A fresh ID for *kind*; *slug* gives a readable suffix (persons only)."""
    try:
        prefix = PREFIX[kind]
    except KeyError:
        raise KBError(f"unknown kind {kind!r}; kinds are: {', '.join(KINDS)}") from None
    if slug is not None:
        if kind != "person":
            raise KBError("only person items take a readable slug")
        ident = f"{prefix}-{slug}"
        if not _ID.match(ident):
            raise KBError(f"bad person slug {slug!r}: use 3-63 of a-z, 0-9 and -")
        return ident
    return prefix + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(10))


def is_id(text: str) -> bool:
    return bool(_ID.match(text))


def kind_of(ident: str) -> str:
    """The kind an ID's prefix declares."""
    return KIND[ident.split("-", 1)[0]]


def parse_ref(ref: str) -> tuple[str, str | None]:
    """Split ``ID#rendition`` into (ID, rendition or None)."""
    m = _REF.match(ref)
    if not m:
        raise KBError(f"not an item reference: {ref!r}")
    return m["id"], m["rendition"]


def is_ref(text: str) -> bool:
    return bool(_REF.match(text))
