"""Indico sites as sources: DataLad datasets filled by git-annex-remote-indico.

An ``indico`` source is a DataLad dataset holding what kb was asked to take
from one Indico site (categories, events and their files), made and kept
current with ``git indico ingest`` (from git-annex-remote-indico), run under
``datalad run`` so that ``datalad rerun`` repeats it.  The files are
registered, not downloaded: ``datalad get`` downloads them, through the
dataset's ``indico-<host>`` special remote.  One dataset per site: adding
another category or event of the site ingests it into the same dataset.

A URL is taken for an Indico URL when one of its host name's labels is
``indico`` (``indico.bnl.gov``, ``indico.cern.ch``), or with ``--type indico``.
"""

import logging
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .errors import KBError

log = logging.getLogger(__name__)

INSTALL = "uv tool install git+https://github.com/brettviren/git-annex-remote-indico"
#: Options of ``git indico ingest`` that ``kb source add -o KEY=VALUE`` passes on.
OPTIONS = {"since": "a day (YYYY-MM-DD): for a category, events from then",
           "until": "a day: for a category, events until then",
           "max-size": "no file bigger than this (such as 200M)",
           "type": "only files of this kind (pdf; several separated by commas)",
           "materials": "all or none",
           "events": "yes or no: for a category, also its events (default yes)"}

_TARGET = re.compile(r"^(?P<base>.*?)/(?:event|category)/\d+(?:/|$)")


def need() -> None:
    if shutil.which("git-indico") is None:
        raise KBError(f"Indico sources need git-annex-remote-indico (git indico): {INSTALL}")


def is_url(target: str) -> bool:
    p = urlsplit(target)
    return p.scheme in ("http", "https") and bool(p.netloc)


def looks_like(target: str) -> bool:
    """Is TARGET the URL of an Indico site (a host label is ``indico``)?"""
    return is_url(target) and "indico" in (urlsplit(target).hostname or "").split(".")


def site(url: str) -> str:
    """The site's base URL: what comes before /event/N or /category/N, else the URL's scheme and host."""
    p = urlsplit(url.strip())
    m = _TARGET.match(p.path)
    path = m.group("base") if m else ("" if p.path.strip("/") in ("", "category", "event") else p.path)
    return f"{p.scheme}://{p.netloc}{path.rstrip('/')}"


def host(url: str) -> str:
    return urlsplit(url).hostname or "indico"


def sites(root: Path) -> list[str]:
    """The Indico sites whose special remotes the repository at ROOT has (git-annex:remote.log)."""
    r = subprocess.run(["git", "-C", str(root), "cat-file", "-p", "git-annex:remote.log"],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.splitlines():
        fields = dict(f.split("=", 1) for f in line.split()[1:] if "=" in f)
        if fields.get("externaltype") == "indico" and fields.get("url"):
            out.append(fields["url"].rstrip("/"))
    return out


def ingest_args(urls: list[str], options: dict[str, str]) -> list[str]:
    args = ["git", "indico", "ingest"]
    for k, v in options.items():
        if k not in OPTIONS:
            raise KBError(f"-o {k}: not an option of Indico sources (have: {', '.join(OPTIONS)})")
        if k == "events":
            args.append("--events" if v.lower() in ("yes", "true", "1", "on") else "--no-events")
        elif k == "type":
            args += [a for t in v.split(",") for a in ("--type", t.strip())]
        else:
            args += [f"--{k}", v]
    return args + list(urls)


def ingest(root: Path, urls: list[str], options: dict[str, str] | None = None) -> str:
    """Ingest URLS into the dataset at ROOT under ``datalad run`` (one commit); what git indico said."""
    need()
    from . import datasets
    datasets.need()
    cmd = ingest_args(urls, options or {})
    import datalad.api as dl
    from datalad.distribution.dataset import Dataset
    message = "Ingest " + " ".join(urls)
    try:          # given a Dataset (not a path), datalad run runs the command at the dataset's top
        res = dl.run(cmd=shlex.join(cmd), dataset=Dataset(str(root)), message=message, result_renderer="disabled",
                     on_failure="ignore", return_type="list")
    except Exception as exc:          # DataLad's own error types
        raise KBError(f"git indico ingest in {root}: {str(exc).splitlines()[0]}") from None
    bad = [r for r in res if r.get("status") in ("error", "impossible")]
    if bad:
        detail = bad[0].get("message") or bad[0].get("stderr") or bad[0]
        if isinstance(detail, tuple):
            detail = detail[0] % detail[1:] if len(detail) > 1 else detail[0]
        raise KBError(f"git indico ingest in {root} failed: {str(detail).strip().splitlines()[-1]}")
    log.info("ingested %s into %s", " ".join(urls), root)
    return message
