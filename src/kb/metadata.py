"""Metadata found for what ``kb add`` adds: from arXiv, PDF files and images.

``kb add`` merges what is found here under the options it was given, so an
explicit ``--title`` (or any field) always wins over a guess.  What is found:

- **arXiv** (a URL of arxiv.org, ``--identifier arxiv=ID``, or an arXiv ID
  printed on a PDF's first page): title, authors, dates, the abstract (whose
  first sentences become the summary, and which is kept as a rendition),
  categories (as tags), DOI and journal reference, from arXiv's API.  An
  arXiv page's URL is added as its PDF.
- **PDF**: the document information (title, author, subject, creation
  date), unless it is the kind of title tools leave behind ("Microsoft Word
  - x.docx"); a DOI on the first page; a first page wider than tall makes it
  a presentation.  Read with PyMuPDF if installed, else poppler's
  ``pdfinfo`` and ``pdftotext``.
- **Images**: EXIF (or PNG text) date taken, artist, description.  Read with
  Pillow if installed, else ``exiftool``.

Nothing found is no error: the item then needs what the schema requires from
the options.  Lookups on the network that fail are reported and skipped.
"""

import json
import logging
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: Look things up on the network (arXiv)?  Tests turn it off unless they serve a fake.
LOOKUP = True
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF = "https://arxiv.org/pdf/"
ARXIV_HOSTS = ("arxiv.org", "www.arxiv.org", "export.arxiv.org")
_ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z][a-z.-]*(?:\.[A-Z]{2})?/\d{7})(v\d+)?"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_DOI = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)\]])")
_JUNK_TITLE = re.compile(r"(?i)^(untitled|microsoft (word|powerpoint)|powerpoint presentation|slide \d+|"
                         r"presentation\d*$|document\d*$)|\.(docx?|pptx?|tex|dvi|pdf|odt|odp)\b")


@dataclass
class Found:
    """Fields found for an item, and where each came from."""
    fields: dict = field(default_factory=dict)
    whence: dict[str, str] = field(default_factory=dict)       # field -> where it was found
    abstract: str | None = None
    #: A source to add instead of the one given (an arXiv page's PDF).
    instead: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)              # lookups that failed, for people

    def take(self, other: "Found", over: bool = False) -> None:
        """Merge OTHER in: its fields fill gaps (or, with OVER, replace these)."""
        for k, v in other.fields.items():
            if k == "identifiers":
                ids = self.fields.setdefault("identifiers", {})
                for ik, iv in v.items():
                    if over or ik not in ids:
                        ids[ik] = iv
                        self.whence[f"identifiers.{ik}"] = other.whence.get(f"identifiers.{ik}", "")
            elif over or k not in self.fields:
                self.fields[k] = v
                self.whence[k] = other.whence.get(k, "")
        if other.abstract and (over or not self.abstract):
            self.abstract = other.abstract
        self.instead.update(other.instead)
        self.notes += other.notes

    def put(self, key: str, value, whence: str) -> None:
        if value in (None, "", [], {}):
            return
        if key.startswith("identifiers."):
            self.fields.setdefault("identifiers", {})[key.split(".", 1)[1]] = value
        else:
            self.fields[key] = value
        self.whence[key] = whence

    def explain(self) -> str:
        by: dict[str, list[str]] = {}
        for k, w in self.whence.items():
            by.setdefault(w, []).append(k)
        return "; ".join(f"{', '.join(ks)} from {w}" for w, ks in by.items())


def summary_of(text: str, limit: int = 400) -> str:
    """The first sentences of TEXT, up to three, within LIMIT characters."""
    text = " ".join(text.split())
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    out = ""
    for s in sentences[:3]:
        if len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    return out or text[:limit - 1].rstrip() + "…"


def _date(text: str | None) -> str | None:
    """An ISO date (YYYY-MM-DD) from what PDF, EXIF and Atom give."""
    if not text:
        return None
    m = re.search(r"(?:D:)?(\d{4})[-:]?(\d{2})[-:]?(\d{2})", str(text))
    return f"{m[1]}-{m[2]}-{m[3]}" if m and m[1] > "1900" else None


def _people(text: str | None) -> list[str]:
    if not text or not text.strip():
        return []
    parts = re.split(r"\s*;\s*|\s+and\s+|\s*&\s*", text.strip())
    if len(parts) == 1 and text.count(",") > 1:
        parts = text.split(",")
    return [p.strip() for p in parts if p.strip()]


# -- arXiv -----------------------------------------------------------------------------------------

def arxiv_id(text: str) -> str | None:
    """The arXiv ID (with its version, if given) in an arxiv.org URL, or an ID itself."""
    p = urllib.parse.urlsplit(text)
    if p.scheme in ("http", "https"):
        if p.hostname not in ARXIV_HOSTS:
            return None
        m = re.match(rf"^/(?:abs|pdf|html|format)/{_ARXIV_ID}(?:\.pdf)?/?$", p.path)
        return (m[1] + (m[2] or "")) if m else None
    m = re.fullmatch(rf"(?:arXiv:)?{_ARXIV_ID}", text.strip())
    return (m[1] + (m[2] or "")) if m else None


def from_arxiv(ident: str, timeout: float = 30) -> Found:
    """What arXiv's API says of the paper IDENT (a version, if given, else the latest)."""
    url = f"{ARXIV_API}?{urllib.parse.urlencode({'id_list': ident})}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        feed = ET.fromstring(r.read())
    entry = feed.find("a:entry", _ATOM)
    if entry is None or entry.find("a:title", _ATOM) is None:
        raise LookupError(f"arXiv has no paper {ident}")
    text = lambda path: " ".join((entry.findtext(path, "", _ATOM) or "").split())   # noqa: E731
    got = text("a:id").rsplit("/abs/", 1)[-1]
    m = re.fullmatch(_ARXIV_ID, got)
    base, version = (m[1], m[2]) if m else (ident, None)
    whence = f"arXiv {base}"
    f = Found()
    f.put("title", text("a:title"), whence)
    abstract = text("a:summary")
    f.abstract = abstract or None
    if abstract:
        f.put("summary", summary_of(abstract), whence)
    f.put("creators", [a.findtext("a:name", "", _ATOM).strip() for a in entry.findall("a:author", _ATOM)], whence)
    f.put("date", _date(text("a:published")), whence)
    f.put("identifiers.arxiv", base, whence)
    f.put("identifiers.doi", text("arxiv:doi"), whence)
    journal = text("arxiv:journal_ref")
    f.put("doctype", "paper" if journal else "preprint", whence)
    f.put("venue", journal, whence)
    f.put("version", version.lstrip("v") if version else None, whence)
    f.put("tags", [c.get("term") for c in entry.findall("a:category", _ATOM) if c.get("term")], whence)
    f.instead[ident] = f"{ARXIV_PDF}{base}{version or ''}"
    return f


def _arxiv(ident: str) -> Found:
    if not LOOKUP:
        return Found()
    try:
        return from_arxiv(ident)
    except (urllib.error.URLError, OSError, ET.ParseError, LookupError) as exc:
        return Found(notes=[f"arXiv lookup of {ident} failed ({exc}); give the metadata as options"])


# -- PDF -----------------------------------------------------------------------------------------

def _pdf_info(path: Path) -> tuple[dict, str, tuple[float, float] | None]:
    """(document information, first page's text, first page's size)."""
    try:
        import pymupdf
        with pymupdf.open(path) as doc:
            meta = {k.lower(): v for k, v in (doc.metadata or {}).items() if v}
            page = doc[0] if doc.page_count else None
            return meta, page.get_text() if page else "", (page.rect.width, page.rect.height) if page else None
    except ImportError:
        pass
    except Exception as exc:          # a damaged PDF: say so and find nothing
        log.warning("%s: cannot read it as PDF (%s)", path, exc)
        return {}, "", None
    meta, size, text = {}, None, ""
    if shutil.which("pdfinfo"):
        r = subprocess.run(["pdfinfo", "-isodates", str(path)], capture_output=True, text=True, errors="replace")
        for line in r.stdout.splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k in ("title", "author", "subject", "keywords", "creator", "producer") and v:
                meta[k] = v
            elif k == "creationdate":
                meta["creationdate"] = v
            elif k == "page size":
                m = re.match(r"([\d.]+) x ([\d.]+)", v)
                size = (float(m[1]), float(m[2])) if m else None
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-l", "1", "-enc", "UTF-8", str(path), "-"], capture_output=True,
                           text=True, errors="replace")
        text = r.stdout
    return meta, text, size


def from_pdf(path: Path, lookup: bool = True) -> Found:
    meta, text, size = _pdf_info(path)
    whence = f"the PDF's {'information' if meta else 'first page'}"
    f = Found()
    title = (meta.get("title") or "").strip()
    if title and not _JUNK_TITLE.search(title) and len(title) <= 200:
        f.put("title", title, "the PDF's information")
    f.put("creators", _people(meta.get("author")), "the PDF's information")
    subject = (meta.get("subject") or "").strip()
    if 0 < len(subject) <= 400:
        f.put("summary", subject, "the PDF's information")
    f.put("date", _date(meta.get("creationdate")), "the PDF's information")
    creator = (meta.get("creator") or "") + " " + (meta.get("producer") or "")
    if (size and size[0] > size[1]) or "beamer" in creator.lower() or "powerpoint" in creator.lower():
        f.put("doctype", "presentation", "the PDF's page shape" if size and size[0] > size[1] else whence)
    head = text[:4000]
    m = re.search(rf"arXiv:{_ARXIV_ID}", head)
    if m:
        f.put("identifiers.arxiv", m[1], "the PDF's first page")
        if lookup:
            f.take(_arxiv(m[1] + (m[2] or "")), over=True)      # arXiv knows better than the PDF's tags
    m = _DOI.search(head)
    if m and "doi" not in f.fields.get("identifiers", {}):
        f.put("identifiers.doi", m[1], "the PDF's first page")
    return f


# -- web pages -------------------------------------------------------------------------------------

def _html_tags(path: Path) -> dict[str, str]:
    """The <title> and <meta> name/property -> content of an HTML page (the head is enough)."""
    from html.parser import HTMLParser

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.tags, self.in_title, self.title = {}, False, ""

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "title":
                self.in_title = True
            elif tag == "meta":
                key = (a.get("name") or a.get("property") or a.get("itemprop") or "").lower()
                if key and a.get("content") and key not in self.tags:
                    self.tags[key] = a["content"].strip()

        def handle_endtag(self, tag):
            if tag == "title":
                self.in_title = False

        def handle_data(self, data):
            if self.in_title:
                self.title += data

    p = P()
    with open(path, encoding="utf-8", errors="replace") as f:
        p.feed(f.read(400_000))
    if p.title.strip():
        p.tags.setdefault("title", " ".join(p.title.split()))
    return p.tags


def from_html(path: Path, last_modified: str | None = None) -> Found:
    """A web page's own title, description, author and date (Open Graph, citation_*, article:*), else the
    date the server says it was last modified."""
    tags = _html_tags(path)
    f = Found()
    w = "the page's metadata"
    title = tags.get("citation_title") or tags.get("og:title") or tags.get("title")
    if title and len(title) <= 200:
        f.put("title", title, w)
    desc = tags.get("description") or tags.get("og:description") or tags.get("twitter:description")
    if desc:
        f.put("summary", summary_of(desc), w)
    f.put("creators", _people(tags.get("citation_author") or tags.get("author") or tags.get("article:author")), w)
    date = _date(tags.get("citation_publication_date") or tags.get("citation_date")
                 or tags.get("article:published_time") or tags.get("datepublished") or tags.get("date"))
    if date:
        f.put("date", date, w)
    elif last_modified:
        f.put("date", last_modified, "the server's Last-Modified")
    return f


# -- images --------------------------------------------------------------------------------------

def _image_tags(path: Path) -> dict:
    try:
        from PIL import ExifTags, Image
        with Image.open(path) as im:
            tags = {k.lower(): v for k, v in (im.info or {}).items() if isinstance(v, str)}
            exif = im.getexif()
            named = {ExifTags.TAGS.get(k, str(k)).lower(): v for k, v in exif.items()}
            named.update({ExifTags.TAGS.get(k, str(k)).lower(): v
                          for k, v in exif.get_ifd(ExifTags.IFD.Exif).items()})
            tags.update({k: v for k, v in named.items() if isinstance(v, str)})
            return tags
    except ImportError:
        pass
    except Exception as exc:
        log.warning("%s: cannot read it as an image (%s)", path, exc)
        return {}
    if shutil.which("exiftool"):
        r = subprocess.run(["exiftool", "-json", "-n", str(path)], capture_output=True, text=True)
        try:
            return {k.lower(): str(v) for k, v in json.loads(r.stdout)[0].items()}
        except (ValueError, IndexError):
            return {}
    return {}


def from_image(path: Path) -> Found:
    tags = _image_tags(path)
    whence = "the image's EXIF" if any(k in tags for k in ("datetimeoriginal", "artist", "make")) else "the image's tags"
    f = Found()
    title = tags.get("imagedescription") or tags.get("title") or tags.get("description")
    if title and len(title.strip()) <= 200:
        f.put("title", title.strip(), whence)
    f.put("creators", _people(tags.get("artist") or tags.get("author")), whence)
    f.put("date", _date(tags.get("datetimeoriginal") or tags.get("createdate") or tags.get("creation time")
                        or tags.get("datetime")), whence)
    camera = " ".join(x.strip() for x in (tags.get("make"), tags.get("model")) if x)
    if f.fields or camera:
        f.put("doctype", "other", whence)
    return f


# -- all of it -----------------------------------------------------------------------------------

def for_source(src: str, lookup: bool = True) -> Found:
    """What a source's name says, before anything is fetched: an arXiv URL."""
    ident = arxiv_id(src) if "://" in src else None
    if not (ident and lookup):
        return Found()
    f = _arxiv(ident)
    f.instead = {src: url for url in f.instead.values()}     # the paper's PDF, whatever page was given
    return f


def for_file(path: Path, media_type: str, lookup: bool = True, last_modified: str | None = None) -> Found:
    """What a local file (or a fetched one: LAST_MODIFIED, as its server said) says of itself."""
    if media_type == "application/pdf":
        return from_pdf(path, lookup)
    if media_type in ("text/html", "application/xhtml+xml"):
        return from_html(path, last_modified)
    if media_type.startswith("image/"):
        return from_image(path)
    return Found()


def for_identifiers(identifiers: dict, lookup: bool = True) -> Found:
    """What the identifiers given say: an arXiv ID."""
    ident = identifiers.get("arxiv")
    return _arxiv(ident) if ident and lookup else Found()
