"""OCR: text from images of text (photos of whiteboards, scanned pages).

Optional and pluggable.  An *OCR engine* turns one image file into text.
Engines are tried in order of priority; the first available one is used,
unless ``ocr.engine`` names one.  Built in:

- ``tesseract``: the tesseract program (``apt install tesseract-ocr``), with
  ``ocr.language`` (tesseract's ``-l``, by default ``eng``).

Others are configured, so a group's local models (behind llama.cpp, ollama
...) plug in without code: ``[ocr.engines.NAME]`` with ``command``, a list
whose ``{input}`` is replaced by the image's path and whose standard output
is the text, and optionally ``priority`` (lower first; tesseract is 50) and
``version``::

    [ocr.engines.qwen-vl]
    command = ["ocr-with-ollama", "--model", "qwen2.5vl", "{input}"]
    priority = 10

New kinds of engine (an HTTP API, say) subclass :class:`Engine` and join
:func:`engines`.

OCR is used by the ``ocr`` transform (images) and for PDF pages that have
no text of their own (scans), when an engine is available.  Text from OCR
has fidelity ``ocr``.
"""

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .errors import KBError

log = logging.getLogger(__name__)

#: Images an engine is given (others are converted first, where that is possible).
IMAGE_TYPES = ("image/png", "image/jpeg", "image/tiff", "image/gif", "image/bmp", "image/webp")


@dataclass
class Engine:
    name: str
    priority: int = 50
    description: str = ""
    config: dict = field(default_factory=dict)

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str:
        return ""

    def run(self, image: Path) -> str:
        raise NotImplementedError


class Tesseract(Engine):
    def available(self):
        return shutil.which("tesseract") is not None

    def version(self):
        r = subprocess.run(["tesseract", "--version"], capture_output=True, text=True)
        first = (r.stdout or r.stderr).splitlines()[:1]
        return first[0].replace("tesseract ", "") if first else ""

    def run(self, image):
        argv = ["tesseract", str(image), "-", "-l", self.config.get("language", "eng")]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            raise KBError(f"tesseract {image.name}: {(r.stderr.strip().splitlines() or ['failed'])[-1]}")
        return r.stdout


class Command(Engine):
    """Any program that reads an image and prints its text: ``command`` with ``{input}``."""

    def available(self):
        argv = self.config.get("command") or []
        return bool(argv) and shutil.which(argv[0]) is not None

    def version(self):
        return str(self.config.get("version", ""))

    def run(self, image):
        argv = [str(a).replace("{input}", str(image)) for a in self.config["command"]]
        r = subprocess.run(argv, capture_output=True, text=True)
        if r.returncode != 0:
            raise KBError(f"OCR {self.name} {image.name}: {(r.stderr.strip().splitlines() or ['failed'])[-1]}")
        return r.stdout


def engines(settings: dict | None = None) -> list[Engine]:
    """Every engine, configured ones included, best first."""
    settings = settings or {}
    out: list[Engine] = [Tesseract("tesseract", 50, "the tesseract program", {
        "language": settings.get("language", "eng")})]
    for name, conf in (settings.get("engines") or {}).items():
        if "command" not in conf:
            raise KBError(f"OCR engine {name} (ocr.engines.{name}) needs a command")
        out = [e for e in out if e.name != name]
        out.append(Command(name, int(conf.get("priority", 20)), conf.get("description", " ".join(conf["command"])),
                           conf))
    return sorted(out, key=lambda e: e.priority)


def engine(settings: dict | None = None) -> Engine | None:
    """The engine to use: ``ocr.engine`` if set (it must be available), else the best available; None if
    there is none (OCR is optional)."""
    settings = settings or {}
    wanted = settings.get("engine")
    for e in engines(settings):
        if wanted and e.name != wanted:
            continue
        if e.available():
            return e
        if wanted:
            raise KBError(f"OCR engine {wanted} (ocr.engine) is not available on this machine")
    if wanted:
        raise KBError(f"no OCR engine {wanted!r} (have: {', '.join(e.name for e in engines(settings))})")
    return None


def available(settings: dict | None = None) -> bool:
    try:
        return engine(settings) is not None
    except KBError:
        return False


def read(image: Path, settings: dict | None = None) -> tuple[str, Engine]:
    """The text of IMAGE, and the engine that read it."""
    e = engine(settings)
    if e is None:
        raise KBError("no OCR engine here: install tesseract (apt install tesseract-ocr), or configure one "
                      "in [ocr.engines.NAME]")
    log.debug("OCR %s with %s", image, e.name)
    return e.run(image), e
