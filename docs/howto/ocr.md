# Images and scans: text by OCR

An image of text (a slide saved as a picture, a photo of a whiteboard, a
scanned page) has nothing for search to find until its text is read. With an
OCR engine installed, `kb add` reads it: the text becomes a rendition of the
item (`text`, fidelity `ocr`), and `kb search` finds words in it.

This walks through adding one slide image, `antsarmies.jpg`, as was done
for this guide.

## 1. Install an OCR engine

```
sudo apt install tesseract-ocr          # plus tesseract-ocr-deu etc. for other languages
tesseract --version                     # tesseract 5.5.0
tesseract --list-langs                  # eng, osd
```

kb uses tesseract whenever it is installed. To say so explicitly, and to
choose its language, add this to your user config,
`~/.config/kb/config.toml`:

```toml
[ocr]
engine = "tesseract"     # the engine to use (default: the best available)
language = "eng"         # tesseract -l; several as "eng+deu"
```

You can check what tesseract makes of an image before adding it:

```
tesseract ~/antsarmies.jpg - -l eng
```

## 2. Add the image

Images rarely carry metadata kb can use. kb reads EXIF (the date taken,
the artist, a description) when there is some, and there was none here. So
give what a document needs: a title, a summary, its creators, a date and a
`doctype`:

```
kb add ~/antsarmies.jpg \
  --title "Amdahl's law: ant colony vs marching army (slide 6)" \
  --summary "Slide contrasting multicore/HTC independent progress (ant colony) with GPU/exascale coupled progress (marching army), then cross-frontier computing themes. Author unknown; date is the image file's timestamp." \
  --set doctype=presentation --creators unknown --date 2021-12 \
  --subject computing/hpc
```

kb says what it did:

```
extracted doc-wyy1zah3qt#text from #original with ocr
doc-wyy1zah3qt
stored document doc-wyy1zah3qt in wckb
```

Notes:

- When you don't know something, say so (`--creators unknown`, and how the
  date was guessed, in the summary) rather than inventing it. Correct it
  later with `kb item set` (summary, subject, tags) or `kb item supersede`.
- A small image is held in the store's database. A large one goes to a
  content store or the store's content dataset ([storage](storage.md)).
- `--no-extract` adds the image without reading it.

## 3. Check the result

The item has two renditions, the image and the text read from it:

```
kb show doc-wyy1zah3qt --json | jq '.renditions[] | {name, media_type, fidelity, transform}'
```

```
{"name": "original", "media_type": "image/jpeg", "fidelity": null, "transform": null}
{"name": "text", "media_type": "text/plain", "fidelity": "ocr",
 "transform": {"name": "ocr", "version": "tesseract 5.5.0", "options": {"ocr": "tesseract 5.5.0"}}}
```

Search finds words that are only in the slide's text, and says where they
are:

```
$ kb search workflows feedback
doc-wyy1zah3qt   document    -          Amdahl's law: ant colony vs marching army (slide 6)
                 in #text (text/plain), from #original (image/png)
                 …approaches vs rewriting algorithms avoid separate [workflows], ensure [feedback] between the two
```

Open the image with `kb open doc-wyy1zah3qt`, and the text read from it
with `kb open doc-wyy1zah3qt --text` (`--print` gives just the path).
OCR makes mistakes ("dificult",
"viggertoroker" for "trigger/broker" here). That is why its fidelity is
`ocr`, and why the title and summary, which you wrote, matter.

## Scanned PDFs

A PDF page with no text of its own (a scan) is read the same way, page by
page, when the PDF transform is PyMuPDF (`pip install 'wckb[pdf]'`). Hits
then name the page (`in #text, page 3`). The `text` rendition records which
pages were read by OCR (`transform.options.ocr_pages`).

## Images added before the OCR engine was installed

They have no text yet. `kb doctor` finds them, and the fix reads them:

```
kb doctor unextracted          # what has no text
kb doctor unextracted --fix    # read it now
```

## Other engines

Any program that prints an image's text can be an engine: a script that
asks a local vision model behind llama.cpp or ollama, say. Configure it, and
make it preferred with a lower `priority` (tesseract's is 50) or by naming it
in `ocr.engine`:

```toml
[ocr.engines.qwen-vl]
command = ["ocr-with-ollama", "--model", "qwen2.5vl", "{input}"]   # {input}: the image's path
priority = 10
version = "qwen2.5vl"
```

The engine's name and version are recorded with the text it made.
