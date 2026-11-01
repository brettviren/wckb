"""Secrets (API tokens and the like) from sources the user controls.

A secret is given by a *spec*, never by the secret itself when avoidable:

    command   run a program; the first line of its stdout is the secret.
              Optionally an identifier is appended as the last argument:
              {command = "pass show", id = "kb/forge/github"}   <- recommended
    file      first line of a file, which should be mode 0600 (kb warns otherwise)
    env       an environment variable
    value     the secret itself (command line or config): works, with a warning

As a string: "cmd:pass show kb/forge/github", "file:~/.config/kb/token",
"env:GITHUB_TOKEN", "value:abc123".  As a TOML table: {command = ..., id = ...},
{file = ...}, {env = ...}, {value = ...}.

New sources (a keyring, a vault) register a resolver in ``RESOLVERS``.
"""

import os
import shlex
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .errors import KBError

SCHEMES = ("command", "file", "env", "value")
_ALIASES = {"cmd": "command", "command": "command", "file": "file", "env": "env", "value": "value"}


def _warn(msg: str) -> None:
    import logging
    from .logs import tell
    tell(logging.getLogger(__name__), logging.WARNING, msg)


@dataclass
class Spec:
    scheme: str
    value: str | list[str]
    id: str | None = None

    def describe(self) -> str:
        """Where the secret comes from, without revealing it."""
        if self.scheme == "command":
            cmd = self.value if isinstance(self.value, str) else shlex.join(self.value)
            return f"command: {cmd}" + (f" {self.id}" if self.id else "")
        if self.scheme == "value":
            return "value: (given directly)"
        return f"{self.scheme}: {self.value}"


def parse(spec) -> Spec:
    if isinstance(spec, Spec):
        return spec
    if isinstance(spec, dict):
        keys = [k for k in spec if k in SCHEMES]
        if len(keys) != 1:
            raise KBError(f"a secret spec needs exactly one of {', '.join(SCHEMES)}; got {sorted(spec)}")
        k = keys[0]
        if k != "command" and "id" in spec:
            raise KBError("'id' only goes with 'command'")
        return Spec(k, spec[k], spec.get("id"))
    if isinstance(spec, str):
        scheme, sep, rest = spec.partition(":")
        if not sep or scheme not in _ALIASES:
            raise KBError(f"secret spec {spec[:12]!r}...: use cmd:, file:, env: or value: (cmd: with pass is recommended)")
        return Spec(_ALIASES[scheme], rest)
    raise KBError(f"bad secret spec: {type(spec).__name__}")


def _command(s: Spec, warn) -> str:
    argv = shlex.split(s.value) if isinstance(s.value, str) else list(s.value)
    if not argv:
        raise KBError("secret command is empty")
    if s.id:
        argv.append(s.id)
    try:
        # stdin and stderr stay connected so gpg/pinentry can ask for a passphrase.
        out = subprocess.run(argv, stdout=subprocess.PIPE, text=True, timeout=120)
    except FileNotFoundError:
        raise KBError(f"secret command not found: {argv[0]}") from None
    except subprocess.TimeoutExpired:
        raise KBError(f"secret command timed out: {argv[0]}") from None
    if out.returncode != 0:
        raise KBError(f"secret command failed ({out.returncode}): {shlex.join(argv)}")
    return out.stdout.split("\n", 1)[0].strip()


def _file(s: Spec, warn) -> str:
    p = Path(os.path.expandvars(str(s.value))).expanduser()
    try:
        mode = p.stat().st_mode
    except FileNotFoundError:
        raise KBError(f"secret file not found: {p}") from None
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        warn(f"secret file {p} has permissions {stat.S_IMODE(mode):04o}, more open than 0600: chmod 600 {p} "
             "(better still, keep it in pass and use a command spec)")
    lines = p.read_text().splitlines()
    return lines[0].strip() if lines else ""


def _env(s: Spec, warn) -> str:
    if s.value not in os.environ:
        raise KBError(f"environment variable {s.value} is not set")
    return os.environ[s.value].strip()


def _value(s: Spec, warn) -> str:
    warn("secret given directly (it may end up in shell history, process lists or files); "
         "prefer a command spec such as cmd:pass show NAME")
    return str(s.value).strip()


RESOLVERS: dict[str, Callable[[Spec, Callable], str]] = {
    "command": _command, "file": _file, "env": _env, "value": _value,
}


def resolve(spec, warn: Callable[[str], None] = _warn) -> str:
    s = parse(spec)
    secret = RESOLVERS[s.scheme](s, warn)
    if not secret:
        raise KBError(f"secret from {s.describe()} is empty")
    return secret


def first_available(specs, warn: Callable[[str], None] = _warn) -> tuple[str, Spec]:
    """The first spec that yields a secret; env specs that are unset are skipped quietly."""
    errors = []
    for spec in specs:
        if spec is None:
            continue
        s = parse(spec)
        if s.scheme == "env" and s.value not in os.environ:
            continue
        try:
            return resolve(s, warn), s
        except KBError as exc:
            errors.append(str(exc))
    raise KBError("no secret available" + (": " + "; ".join(errors) if errors else ""))


def mask(secret: str) -> str:
    return (secret[:4] + "…" if len(secret) > 8 else "…") + f" ({len(secret)} chars)"
