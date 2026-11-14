# Documents: papers, slides, notes

## The usual cases

**Slides from Indico, behind SSO.** `kb` can't fetch these, so use the copy
you downloaded and record the URL as the canonical location:

```
kb add ~/Downloads/noise-update.pdf \
  --url "https://indico.example/event/12345/contributions/67/attachments/89/noise-update.pdf" \
  --set doctype=presentation --identifier indico=event/12345/contribution/67 \
  --title "TPC noise update" \
  --summary "Coherent noise rose ~20% in runs 1200-1300; grounding fix proposed; run 1243 worst." \
  --creators "A. Colleague" --date 2026-10-28 --subject detector/tpc/noise --runs 1200-1300 \
  --extract
```

**A paper on arXiv.** Give its URL (the abstract page or the PDF); `kb`
asks arXiv for the rest and adds the PDF:

```
kb add https://arxiv.org/abs/2601.01234 --subject detector/tpc/noise --extract
```

See "Metadata kb finds" below.

**A public URL you don't want to download now:**
`--no-fetch` records the URL without a hash. `kb lint` will remind you, and
`kb rendition verify --record ID` fills the hash in later.

## Where does the file go?

`kb add` decides:

| situation | result |
|---|---|
| file of 1 MiB or less | held in the store's database, with its file name |
| larger file, content store configured | copied into the content-addressed store (`cas`), addressed by SHA-256 |
| larger file, no content store | recorded as local to your host. `kb lint` warns, because nobody else can read it |
| file committed in a DataLad dataset | left there: a `datalad` location ([datalad](datalad.md)) |
| URL | always recorded; downloaded content is kept in the url source directory (`~/.cache/wckb/url/`) and stored per the rows above |

The URL is the canonical location, and the copy in the store or the content
store is a mirror that is checked by hash. Later mirrors can be added with
`kb rendition locate ID#original --cas default` or `--url URL`.
Where content can live is a pluggable set of *data sources* (`db`, `cas`,
`url`, `local`, and `datalad` for DataLad datasets);
see [storage](storage.md).

## Metadata kb finds

`kb add` looks for metadata in what it adds, and tells you what it found
(`found title, creators, date in arXiv 2601.01234`). Options you give
override it: `--title`, `--summary` and any `--set` win over any guess.
`--no-guess` turns all of this off.

| from | gives |
|---|---|
| an arXiv URL, `--identifier arxiv=ID`, or `arXiv:ID` on a PDF's first page | title, authors, date of the first version, abstract (its first sentences as the summary, all of it as the rendition `abstract`), categories as tags, DOI, journal reference (`venue`, and `doctype` paper; preprint otherwise), version |
| a PDF's document information | title (not the "Microsoft Word - x.docx" kind), authors, subject as summary, creation date |
| a PDF's first page | a DOI; a page wider than tall makes it a `presentation` |
| an image's EXIF (or PNG text) | description as title, artist, date taken (`doctype` other) |

PDFs are read with PyMuPDF if it is installed (`pip install 'wckb[pdf]'`),
else with poppler's `pdfinfo` and `pdftotext`. Images are read with Pillow,
else with `exiftool`. A file whose name doesn't say what it is (no
extension) is recognized by its first bytes. An arXiv lookup that fails is
a warning: give the metadata as options.

## Copying into a source: `--target`

`--target PATH` copies the file there: into the directory `PATH` if it
exists or ends in `/`, else to the new file's path `PATH`. If the copy lands
in a source, the source takes it in, and the rendition names the source:

| the copy lands in | kb | the rendition's location |
|---|---|---|
| a DataLad dataset (an `indico` one too) | `datalad save`s it | `datalad` (source item, path, annex key) |
| a git repository described here (`kb source add REPO`) | commits it (`git commit`) | `source` (source item, path) |
| a filesystem tree described here (`--type filesystem`) | nothing more | `source` (source item, path) |
| anything else | nothing more (a git repository that is not a source here is not committed into; kb says so) | as above, plus `local` for the copy |

A dataset that is not yet a source here gets a source item, as for files
already committed in one. A URL is fetched and then copied.

```
kb add slides.pdf --target ~/data/talks/2026/        # in a DataLad dataset: copied and saved
kb add https://arxiv.org/abs/2601.01234 --target ~/papers/
```

## Duplicates

When an identifier (`doi`, `arxiv`, `indico`, ...) already appears in the
KB, `kb add` refuses and names the existing item. Identifiers kb finds count
too, and the check comes before anything is copied or committed. Use `--force` only for
a genuinely different document that happens to share it.

## Text extraction

```
kb rendition transforms                 # what's available on this machine
kb rendition extract doc-3vq8r2m1kd     # picks a transform by media type, writes the 'text' rendition
kb item content doc-3vq8r2m1kd text    # read it
kb rendition path doc-3vq8r2m1kd#text       # or get it as a file, for other tools
```

Built-in transforms:

| transform | input | notes |
|---|---|---|
| `pdftotext` | PDF | needs poppler |
| `pymupdf` | PDF | extra `pdf` |
| `pptx` | PowerPoint | titles, text, tables, speaker notes |
| `pandoc` | DOCX, ODT, HTML, LaTeX, org, RST | |
| `root-describe` | ROOT files | |
| `monolith` | HTML | not text: a self-contained copy of a web page (below) |

To add a better converter for everyone using the store (docling, marker,
markitdown), declare it in the store's settings:

```
kb config set transforms.docling '{"command": ["docling", "--to", "md", "--output", "{outdir}", "{input}"],
  "output": "{outdir}/{stem}.md", "media_types": ["application/pdf"], "version_command": ["docling", "--version"]}'
```

Configured transforms take precedence over built-in ones for their media
types.

## Web pages: a self-contained copy

A web page loads styles, scripts, images, fonts and data from elsewhere,
which can change or vanish. When [monolith](https://github.com/Y2Z/monolith)
is installed, `kb add` of a web page (a URL, or a local `.html` file) also
keeps a copy with all of that inlined as one file, the derived rendition
`page`. The copy is cut off from the network, so it shows what was kept.

```
kb add https://example.org/report/          # #original as served, #text for search, #page to view
kb add report.html --url https://...        # a local page: what it loads is found next to the file
kb open doc-3vq8r2m1kd                      # opens #page (a page in a source's tree opens in place)
kb rendition extract doc-3vq8r2m1kd --transform monolith    # make #page later (--force: again)
```

Fonts are left out, and the text shows in the viewer's own fonts. A web
font family comes as dozens of files, one per weight and character range,
and inlining them all made one 1.1 MB page 93% fonts. To keep them:

```
kb config set page.fonts true
```

Relative references resolve against the page's URL, or for a local file,
against where the file is. What monolith could not get is listed in the
rendition's `transform.options.missing`. `--no-page` skips the copy. The
original stays as it was received, so `kb rendition verify` still compares
the URL against it.

## Web documents: many pages

A user guide, a design document or a manual is often published as many web
pages under one URL. `kb add URL --spider` reads the page at URL and every
page it links to, and theirs, within the *scope*: the same scheme and host
and a path under the URL's directory (`--scope URL` to give another). Paths
are compared whole, so `https://h/phlex/` never reaches
`https://h/phlex-design/`. Spidering happens only with `--spider`.

```
kb add https://example.org/guide/ --spider --target ~/data/docs/guide/ \
   --set doctype=manual --creators "The Team" --set version=2.1.0 \
   --title "Example user guide" --summary "..." --subject software/example \
   --exclude search.html --exclude genindex.html
```

It makes one document for the whole, identified by the scope's URL, and one
document per page, each `part-of` the whole and sharing its doctype,
creators, version, date, subject and tags. A page gets its own title (its
`h1`), summary (its description, else its first paragraph), URL, text,
self-contained copy (`page`) and sections (`sections`). The whole has the
metadata rendition `pages`: each page's path, URL, title and item, and the
links not taken and why (`kb item content ID pages`).

| option | |
|---|---|
| `--mirror` | keep pages as served and each file they load, beside them (below) |
| `--target DIR` | lay out the self-contained copies in DIR as the site is (`guide/x.html`), with their links to each other made relative so the copy can be browsed offline; in a DataLad dataset each is saved |
| `--scope URL` | take pages under this URL instead |
| `--exclude PATTERN` | shell pattern of paths not to read (repeatable) |
| `--max-pages N` | read at most N pages (200) |
| `--delay S` | seconds between requests (0.2) |

**A server whose certificate doesn't verify.** Some servers send an
incomplete certificate chain (typically one leaving out the intermediate CA
certificate). Browsers cope, since they ship or fetch the missing
intermediates; Python, curl and wget don't, and fail with
`CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`.
`kb add --no-check-certificate` (with or without `--spider`) then fetches
without verifying certificates, as does monolith (`-k`) for the
self-contained copies. What was fetched is still hashed; what is lost is
the assurance that it came from that server. Check the chain with
`openssl s_client -connect HOST:443 -servername HOST -showcerts`.

**A mirror: each file as served.** With `--mirror` (and `--target`), the
pages are kept as served and so is each file they load (style sheets, what
those import and load, scripts, images, fonts), laid out as the site is, as
`wget -r -p` does, and taken into the dataset (or git repository) at once,
by one `datalad save`. Query strings are left out of file names
(`_static/x.css?v=1` is `_static/x.css`), which is what a browser looks for
in a local copy. A page's original is then its file there (and its URL);
no self-contained copies are made. Only pages are items; the files they load
are listed, with their URL, media type and hash, in the whole's `pages`
(`files`), and what was out of scope (a CDN) in `files_skipped`: it stays
at its URL, so the copy needs the network for it.

```
kb add https://example.org/guide/ --spider --mirror --target ~/data/docs/guide/ \
   --set doctype=manual --creators "The Team" --exclude search.html
```

Links are left as served: a site whose pages link each other relatively
(Sphinx, MkDocs) browses as it is; absolute links lead back to the site.

**A new version.** `--supersedes ID` (the whole of the earlier version)
makes each page supersede the earlier version's page of the same URL; a
mirror replaces the files in place (the dataset keeps the history).

Only web pages are followed (links in `a` elements); styles and images go
into each page's copy, or with `--mirror` beside it. A page served under two URLs (`guide/` and
`guide/index.html`) is kept once. Doctype and creators must be given, and
the fields are checked before anything is read. Adding the same scope again
is refused (`--force`, or `--supersedes ID` for a new version).

**Citing a whole, a page or a section.** A reference can name a section of
a page by its anchor (the `id` of its heading or of the section it opens):
`ID@anchor`. Every page lists its sections, and kb refuses a reference to
an anchor a page doesn't have.

```
kb item content doc-W pages                  # the pages of web document doc-W
kb item content doc-P sections               # the sections of page doc-P
kb item content doc-P@limitations            # the text of one section (and its subsections)
kb new observation ... --evidence doc-P@limitations --link refines=doc-W
```

## Slides lose their plots

Text extraction keeps titles and bullets, but the results in a physics talk
are in the plots. For material people will rely on, add a digest. Read the
slides, or have Claude read them, write `digest.md` describing what each
plot shows (axes, trends, numbers), and register it as an LLM-made
rendition:

```
kb rendition add doc-3vq8r2m1kd digest.md --name digest --role derived --fidelity llm --from original --tool claude
```

Digests are searchable and useful for context, but they're never accepted
as evidence for `verified`.

## Facts inside documents

When a talk states something others will need on its own ("run 1250
excluded"), also write an observation citing the document:
`--evidence doc-3vq8r2m1kd#text`. Then the fact can be found by run and
subject, and corrected independently.
