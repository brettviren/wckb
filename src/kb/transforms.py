"""Transforms: one rendition in, a derived rendition out.

Built-in transforms cover what the group meets most (PDF, PPTX, office and
markup formats through pandoc, ROOT files).  Most extract text for search;
a *page* transform (monolith) instead makes a web page self-contained, its
styles, scripts, images and fonts inlined, so it still shows when they are
gone (the rendition ``page``).  Others are declared in the store setting "transforms"
as command transforms, so a better converter can be adopted without a kb
release.  Every output records the transform's name and version and the
SHA-256 of its input, which is how staleness is detected.
"""

import copy
import importlib.util
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import content
from .errors import KBError
from .items import Store, Item
from .ocr import IMAGE_TYPES

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_PANDOC_FROM = {
    DOCX: "docx",
    "application/vnd.oasis.opendocument.text": "odt",
    "text/html": "html",
    "text/x-tex": "latex",
    "text/x-org": "org",
    "text/x-rst": "rst",
    "application/epub+zip": "epub",
}


@dataclass
class Transform:
    name: str
    media_types: tuple[str, ...]
    output_type: str = "text/markdown"
    suffix: str = ".md"
    fidelity: str = "extracted"
    priority: int = 50          # lower wins when several accept a media type
    description: str = ""
    #: "text": extracts text for search (chosen by media type); "page": a self-contained copy of a web page
    purpose: str = "text"
    config: dict = field(default_factory=dict)

    def available(self) -> bool:
        raise NotImplementedError

    def version(self) -> str:
        return ""

    def run(self, src: Path, dst: Path, media_type: str) -> dict | None:
        """Write DST from SRC; optionally say more: {"fidelity": ..., "options": {...}} (OCR was used ...).
        Text of several pages separates them with form feeds (\\f), so search can name the page.
        A page transform is also given ``base``: the URL the page was read from, if any."""
        raise NotImplementedError


def _cmd_version(argv) -> str:
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0].strip() if text else ""


def _run(argv, **kw):
    try:
        out = subprocess.run(argv, capture_output=True, text=True, **kw)
    except OSError as exc:
        raise KBError(f"cannot run {argv[0]}: {exc}") from exc
    if out.returncode != 0:
        raise KBError(f"{' '.join(map(str, argv))} failed ({out.returncode}): {out.stderr.strip()[:500]}")
    return out


def _pkg_version(name: str) -> str:
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version(name)
    except PackageNotFoundError:
        return ""


class PdfToText(Transform):
    def available(self):
        return shutil.which("pdftotext") is not None

    def version(self):
        return _cmd_version(["pdftotext", "-v"]).replace("pdftotext version ", "")

    def run(self, src, dst, media_type):
        _run(["pdftotext", "-layout", "-enc", "UTF-8", str(src), str(dst)])


class PyMuPDF(Transform):
    """Text per page, pages separated by form feeds; a page without text of its own (a scan) is read by
    OCR when an engine is available (config: the ocr settings)."""

    #: A page with fewer characters than this is taken for a scan.
    SCANNED = 20

    def available(self):
        return importlib.util.find_spec("pymupdf") is not None

    def version(self):
        return _pkg_version("pymupdf")

    def run(self, src, dst, media_type):
        import tempfile
        import pymupdf
        from . import ocr
        pages, read, used = [], [], None
        with pymupdf.open(src) as doc, tempfile.TemporaryDirectory() as tmp:
            for i, page in enumerate(doc, 1):
                text = page.get_text().strip()
                if len(text) < self.SCANNED and ocr.available(self.config):
                    image = Path(tmp) / f"page-{i}.png"
                    page.get_pixmap(dpi=300).save(image)
                    got, used = ocr.read(image, self.config)
                    if got.strip():
                        text, read = got.strip(), read + [i]
                pages.append(text)
        dst.write_text("\f".join(pages) + "\n")
        if read:
            return {"fidelity": "ocr" if len(read) == len(pages) else "extracted",
                    "options": {"ocr": f"{used.name} {used.version()}".strip(), "ocr_pages": read}}
        return None


class Ocr(Transform):
    """Text from an image by OCR (kb.ocr: tesseract, or a configured engine)."""

    def available(self):
        from . import ocr
        return ocr.available(self.config)

    def version(self):
        from . import ocr
        e = ocr.engine(self.config)
        return f"{e.name} {e.version()}".strip() if e else ""

    def run(self, src, dst, media_type):
        from . import ocr
        text, used = ocr.read(src, self.config)
        dst.write_text(text)
        return {"fidelity": "ocr", "options": {"ocr": f"{used.name} {used.version()}".strip()}}


class Pandoc(Transform):
    def available(self):
        return shutil.which("pandoc") is not None

    def version(self):
        return _cmd_version(["pandoc", "--version"]).replace("pandoc ", "")

    def run(self, src, dst, media_type):
        info = None
        if media_type == "text/html":       # a web page: only its main content (kb.readability)
            from .readability import main_content
            page = Path(src).read_text(encoding="utf-8", errors="replace")
            content, how = main_content(page)
            src = dst.with_name(dst.stem + ".main.html")
            src.write_text(f"<html><body>{content}</body></html>", encoding="utf-8")
            info = {"options": {"readability": how}}
        # no raw HTML in the output: it is for reading and searching.  "-raw_html" on the writer is
        # not enough: the HTML/EPUB readers keep <div> as a native Div, which the gfm writer still
        # prints as a raw tag, so divs are read as plain blocks instead.  Spans are already dropped
        # by the writer, and "-native_spans" would split paragraphs at each span (pandoc 2.17).
        reader = _PANDOC_FROM[media_type]
        if reader in ("html", "epub"):
            reader += "-native_divs"
        _run(["pandoc", "-f", reader, "-t", "gfm-raw_html", "--wrap=none", str(src), "-o", str(dst)])
        return info


_MISSED = re.compile(r"^\S+ \(.+\)$")


class Monolith(Transform):
    """A web page as one HTML file: what it loads (styles, scripts, images, fonts) inlined as data URLs.

    Relative references resolve against BASE (the page's URL) when there is one, else against the file.
    The copy is cut off from the network (-I), so it shows what was kept, and carries no time of
    making (-M), so making it twice gives the same file when what the page loads is unchanged.
    What can't be fetched is left out (-e) and said in the options."""

    def available(self):
        return shutil.which("monolith") is not None

    def version(self):
        return _cmd_version(["monolith", "--version"]).replace("monolith ", "")

    def run(self, src, dst, media_type, base=None):
        argv = ["monolith", str(src), "-o", str(dst), "-I", "-M", "-e",
                "-t", str(self.config.get("timeout", 60))]
        if base:
            argv += ["-b", base]
        out = _run(argv, timeout=int(self.config.get("timeout", 60)) * 20)
        options = {"base": base} if base and not base.startswith("file:") else {}    # no local paths
        # monolith names each resource it reads on stderr, with "(why)" after those it could not get
        missed = [m.group(0) for m in map(_MISSED.match, out.stderr.splitlines()) if m]
        if missed:
            options["missing"] = missed[:20]
        return {"options": options} if options else None


class Pptx(Transform):
    """Slide titles, text, tables and speaker notes; pictures become placeholders."""

    def available(self):
        return importlib.util.find_spec("pptx") is not None

    def version(self):
        return _pkg_version("python-pptx")

    def run(self, src, dst, media_type):
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        prs = Presentation(str(src))
        out = []
        for i, slide in enumerate(prs.slides, 1):
            title = slide.shapes.title.text.strip() if slide.shapes.title is not None and slide.shapes.title.has_text_frame else ""
            out.append(f"## Slide {i}" + (f": {title}" if title else ""))
            figures = 0
            for shape in slide.shapes:
                if shape == slide.shapes.title:
                    continue
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(r.text for r in para.runs).strip()
                        if text:
                            out.append("  " * para.level + f"- {text}")
                elif getattr(shape, "has_table", False) and shape.has_table:
                    rows = [[c.text.strip().replace("|", "\\|") for c in r.cells] for r in shape.table.rows]
                    if rows:
                        out.append("")
                        out.append("| " + " | ".join(rows[0]) + " |")
                        out.append("|" + "---|" * len(rows[0]))
                        out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
                        out.append("")
                elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE or getattr(shape, "has_chart", False):
                    figures += 1
            if figures:
                out.append(f"\n[{figures} figure(s) not extracted]")
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    out.append(f"\nNotes: {notes}")
            out.append("")
        dst.write_text("\n".join(out))


class RootDescribe(Transform):
    """Structure of a ROOT file: trees with branches and entries, histograms with their contents."""

    def available(self):
        return importlib.util.find_spec("uproot") is not None

    def version(self):
        return _pkg_version("uproot")

    def run(self, src, dst, media_type):
        import uproot
        desc = {"objects": []}
        with uproot.open(src) as f:
            for key, cls in sorted(f.classnames().items()):
                item = {"key": key, "class": cls}
                obj = f[key]
                if cls.startswith(("TTree", "TNtuple", "ROOT::RNTuple")):
                    item["entries"] = int(obj.num_entries)
                    try:
                        types = obj.typenames()
                    except Exception:  # noqa: BLE001 - RNTuple readers lack typenames in some versions
                        types = {}
                    item["branches"] = [{"name": k, "type": str(types.get(k, ""))}
                                        for k in obj.keys(recursive=False)]
                elif cls.startswith("TH1") or cls.startswith("TH2") or cls.startswith("TProfile"):
                    values, *edges = obj.to_numpy(flow=False)
                    item["title"] = str(obj.title)
                    item["axes"] = [{"bins": len(e) - 1, "low": float(e[0]), "high": float(e[-1])} for e in edges]
                    item["integral"] = float(values.sum())
                    if values.ndim == 1 and values.size <= 200:
                        item["values"] = [float(v) for v in values]
                desc["objects"].append(item)
        dst.write_text(json.dumps(desc, indent=2) + "\n")


class Command(Transform):
    """A transform declared in the store setting transforms.NAME."""

    def available(self):
        return shutil.which(self.config["command"][0]) is not None

    def version(self):
        vc = self.config.get("version_command")
        return _cmd_version(vc) if vc else ""

    def run(self, src, dst, media_type, base=None):
        with tempfile.TemporaryDirectory() as outdir:
            subst = {"input": str(src), "output": str(dst), "outdir": outdir, "stem": src.stem, "base": base or ""}
            argv = [a.format(**subst) for a in self.config["command"]]
            _run(argv, timeout=self.config.get("timeout", 3600))
            produced = Path(self.config.get("output", "{output}").format(**subst))
            if produced != dst:
                if not produced.is_file():
                    raise KBError(f"transform {self.name} did not produce {produced}")
                shutil.move(produced, dst)


def builtin() -> list[Transform]:
    return [
        PdfToText("pdftotext", ("application/pdf",), "text/plain", ".txt", priority=40,
                  description="poppler pdftotext -layout"),
        PyMuPDF("pymupdf", ("application/pdf",), "text/plain", ".txt", priority=30,
                description="PyMuPDF text per page; OCR for scanned pages (extra: pdf)"),
        Ocr("ocr", IMAGE_TYPES, "text/plain", ".txt", fidelity="ocr", priority=50,
            description="OCR (tesseract, or an engine in [ocr.engines])"),
        Pandoc("pandoc", tuple(_PANDOC_FROM), priority=50, description="pandoc to GitHub markdown"),
        Monolith("monolith", content.MARKUP, "text/html", ".html", fidelity="lossless", purpose="page",
                 description="monolith: a web page made self-contained (rendition 'page')"),
        Pptx("pptx", (PPTX,), priority=50, description="python-pptx: titles, text, tables, notes (extra: pptx)"),
        RootDescribe("root-describe", ("application/x-root",), "application/json", ".json", priority=50,
                     description="uproot: trees, branches, histograms as JSON (extra: root)"),
    ]


def registry(kb: Store) -> list[Transform]:
    out = builtin()
    for t in out:
        if isinstance(t, (PyMuPDF, Ocr)):
            t.config = kb.cfg.raw.get("ocr", {}) or {}
        elif isinstance(t, Monolith):
            t.config = kb.cfg.raw.get("page", {}) or {}
    for name, conf in kb.cfg.transforms.items():
        if "command" not in conf:
            raise KBError(f"transform {name} (setting transforms.{name}) needs a command")
        out = [t for t in out if t.name != name]
        out.append(Command(
            name, tuple(conf.get("media_types", ())),
            output_type=conf.get("output_type", "text/markdown"),
            suffix=conf.get("suffix", ".md"),
            fidelity=conf.get("fidelity", "extracted"),
            priority=int(conf.get("priority", 20)),  # configured ones win over built-ins
            description=conf.get("description", " ".join(conf["command"])),
            purpose=conf.get("purpose", "text"),
            config=conf))
    return out


def choose(kb: Store, media_type: str, name: str | None = None, purpose: str = "text") -> Transform:
    """Transform NAME, else the best available one for MEDIA_TYPE with that PURPOSE (text, page)."""
    candidates = registry(kb)
    if name:
        for t in candidates:
            if t.name == name:
                if not t.available():
                    raise KBError(f"transform {name} is not available on this machine")
                return t
        raise KBError(f"no transform {name!r}; see kb rendition transforms")
    usable = sorted((t for t in candidates if media_type in t.media_types and t.purpose == purpose
                     and t.available()), key=lambda t: t.priority)
    if not usable:
        what = "transform" if purpose == "text" else f"{purpose} transform"
        raise KBError(f"no available {what} for {media_type}; see kb rendition transforms")
    return usable[0]


def extract(kb: Store, rec: Item, source: str | None = None, transform: str | None = None,
            name: str | None = None, force: bool = False, base: str | None = None) -> dict:
    """Derive rendition *name* from *source* (default: the first original).  NAME defaults to ``text``,
    or for a page transform to ``page`` (page_name).  BASE: where a web page's relative
    references resolve (default: the source's URL, if it has one, else the file kb holds)."""
    rec = kb.get(rec.id)          # the stored version, whatever the caller holds
    if source is None:
        originals = [r for r in rec.renditions if r["role"] in ("original", "content")]
        if not originals:
            raise KBError(f"{rec.id} has no original rendition to extract from")
        source = originals[0]["name"]
    src_r = rec.rendition(source)
    t = choose(kb, src_r["media_type"], transform)
    if name is None:
        name = page_name(source) if t.purpose == "page" else "text"
    if rec.has_rendition(name) and not force:
        existing = rec.rendition(name)
        if existing.get("source_sha256") == src_r.get("sha256"):
            raise KBError(f"{rec.id}#{name} exists and is up to date (use --force to redo)")
    try:
        src = content.resolve(kb, rec, src_r)
    except content.FetchError as exc:
        content.mark_fetch(kb, rec, exc.url, exc.record())       # so that retries can go by what happened
        raise
    for loc in src_r.get("locations", []):
        if loc["type"] == "url" and loc.get("failed") and src is not None:
            content.mark_fetch(kb, rec, loc["url"], None)
    if src is None:
        raise KBError(f"{rec.id}#{source} is not reachable from here (not in the store, a content store or the cache, and no fetchable URL)")

    filename = name + t.suffix
    kb.cfg.kbdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=kb.cfg.kbdir) as tmp:
        out = Path(tmp) / filename
        extra = {}
        if t.purpose == "page":        # relative references resolve against where the page was read
            extra["base"] = base or next((loc["url"] for loc in src_r.get("locations", [])
                                          if loc["type"] == "url"), None)
        try:
            info = t.run(src, out, src_r["media_type"], **extra) or {}
        except KBError:
            raise
        except Exception as exc:          # a library's own error (a damaged or mislabelled file)
            raise KBError(f"transform {t.name} failed on {rec.id}#{source}: {exc}") from None
        if not out.is_file():
            raise KBError(f"transform {t.name} produced nothing")
        with kb.tx():
            locs, sha, size = content.place(kb, out, filename)
            if locs and locs[0]["type"] == "local":
                raise KBError(f"{filename} is too large for the database, and there is no content store or "
                              "content dataset here (git-annex is needed for one)")
            derived = {
                "name": name, "role": "derived", "media_type": t.output_type,
                "sha256": sha, "size": size, "locations": locs,
                "derived_from": source, "source_sha256": src_r.get("sha256"),
                "fidelity": info.get("fidelity", t.fidelity), "transform": {"name": t.name, "version": t.version()},
            }
            if info.get("options"):
                derived["transform"]["options"] = info["options"]
            if derived["source_sha256"] is None:
                derived.pop("source_sha256")
            data = copy.deepcopy(rec.data)
            data["renditions"] = [r for r in data.get("renditions", []) if r["name"] != name] + [derived]
            kb.save(Item(data, kb), "extract", {"rendition": name, "transform": t.name})
    return derived


def text_name(source: str) -> str:
    """The name of the text extracted from rendition SOURCE: ``text`` for the original, else SOURCE-text."""
    return "text" if source == "original" else f"{source}-text"


def page_name(source: str) -> str:
    """The name of the self-contained copy of web page SOURCE: ``page`` for the original, else SOURCE-page."""
    return "page" if source == "original" else f"{source}-page"


def _page_makers(kb: Store) -> set[str]:
    return {t.name for t in registry(kb) if t.purpose == "page"}


def unextracted(kb: Store, rec: Item) -> list[tuple[str, Transform]]:
    """The renditions of REC that are not text and have no text extracted yet, with the transform that
    would extract it here (renditions no available transform handles are left out)."""
    from .content import needs_text
    makers = _page_makers(kb)
    have = {r.get("derived_from") for r in rec.renditions
            if r["role"] == "derived" and r.get("transform", {}).get("name") not in makers}
    out = []
    for r in rec.renditions:
        if r["role"] not in ("original", "content") or not needs_text(r["media_type"]) or r["name"] in have:
            continue
        try:
            out.append((r["name"], choose(kb, r["media_type"])))
        except KBError:
            continue
    return out


def extract_missing(kb: Store, rec: Item) -> tuple[list[dict], list[str]]:
    """Extract text from every rendition of REC that needs it (unextracted): (what was made, what failed)."""
    made, failed = [], []
    for source, t in unextracted(kb, rec):
        try:
            made.append(extract(kb, rec, source, t.name, text_name(source)))
        except KBError as exc:
            failed.append(f"{rec.id}#{source}: {exc}")
    return made, failed


def unpaged(kb: Store, rec: Item) -> list[str]:
    """The web pages (original or content renditions) of REC with no self-contained copy yet."""
    from .content import MARKUP
    makers = _page_makers(kb)
    have = {r.get("derived_from") for r in rec.renditions
            if r["role"] == "derived" and r.get("transform", {}).get("name") in makers}
    return [r["name"] for r in rec.renditions
            if r["role"] in ("original", "content") and r["media_type"] in MARKUP and r["name"] not in have]


def page_missing(kb: Store, rec: Item, bases: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """Make a self-contained copy of every web page of REC that has none (unpaged), when a page
    transform is available here: (what was made, what failed).  BASES: per rendition name, where its
    relative references resolve (the file kb add was given: kb holds a copy without its neighbours)."""
    made, failed = [], []
    for source in unpaged(kb, rec):
        try:
            t = choose(kb, rec.rendition(source)["media_type"], purpose="page")
        except KBError:
            break                       # none here: not a failure
        try:
            made.append(extract(kb, rec, source, t.name, page_name(source), base=(bases or {}).get(source)))
        except KBError as exc:
            failed.append(f"{rec.id}#{source}: {exc}")
    return made, failed
