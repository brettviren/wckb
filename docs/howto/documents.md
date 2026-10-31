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

**A paper on arXiv.** It's fetchable, so `kb` downloads it to hash it:

```
kb add https://arxiv.org/pdf/2601.01234 --set doctype=preprint \
  --identifier arxiv=2601.01234 --title "..." --summary "..." --creators "..." --date 2026-01 --extract
```

**A public URL you don't want to download now:**
`--no-fetch` records the URL without a hash. `kb lint` will remind you, and
`kb verify --record ID` fills the hash in later.

## Where does the file go?

`kb add` decides:

| situation | result |
|---|---|
| file of 1 MiB or less | held in the store's database, with its file name |
| larger file, content store configured | copied into the content-addressed store (`cas`), addressed by SHA-256 |
| larger file, no content store | recorded as local to your host. `kb lint` warns, because nobody else can read it |
| URL | always recorded; downloaded content is cached in `.kb/cache` and stored per the rows above |

The URL is the canonical location, and the copy in the store or the content
store is a mirror that is checked by hash. Later mirrors can be added with
`kb rendition locate ID#original --store default` or `--url URL`.
Where content can live is a pluggable set of *data sources* (`db`, `cas`,
`url`, `local`, and experimentally `annex` for DataLad datasets);
see [storage](storage.md).

## Duplicates

When an identifier (`doi`, `arxiv`, `indico`, ...) already appears in the
KB, `kb add` refuses and names the existing record. Use `--force` only for
a genuinely different document that happens to share it.

## Text extraction

```
kb transforms                 # what's available on this machine
kb extract doc-3vq8r2m1kd     # picks a transform by media type, writes the 'text' rendition
kb content doc-3vq8r2m1kd text    # read it
kb path doc-3vq8r2m1kd#text       # or get it as a file, for other tools
```

Built-in transforms:

| transform | input | notes |
|---|---|---|
| `pdftotext` | PDF | needs poppler |
| `pymupdf` | PDF | extra `pdf` |
| `pptx` | PowerPoint | titles, text, tables, speaker notes |
| `pandoc` | DOCX, ODT, HTML, LaTeX, org, RST | |
| `root-describe` | ROOT files | |

To add a better converter for everyone using the store (docling, marker,
markitdown), declare it in the store's settings:

```
kb config set transforms.docling '{"command": ["docling", "--to", "md", "--output", "{outdir}", "{input}"],
  "output": "{outdir}/{stem}.md", "media_types": ["application/pdf"], "version_command": ["docling", "--version"]}'
```

Configured transforms take precedence over built-in ones for their media
types.

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
