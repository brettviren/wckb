# Artifacts: technotes, demos and notebooks

An *artifact* is something you make (often with an LLM) to collect, present
and explain knowledge: a LaTeX technote, an HTML page with an interactive
demo, a notebook. In kb an artifact is a `document` of doctype `technote`,
`demo` or `notebook`, with the items it presents as its inputs. That gives
it what documents have (search of its text, `kb open`, versions by
supersession), and it goes stale when the knowledge it presents changes.

This guide covers what is built (phase 1 of
[decisions/0004](../decisions/0004-artifacts.md)): artifacts as documents,
co-authors, LLM summaries, and HTML artifacts opened from their source.
Workspaces that record how an artifact was made come next.

## Add an artifact

Say what it presents with `--derived-from`, one per item:

```
kb add noise-technote.pdf --set doctype=technote --set provenance=cited \
  --title "TPC noise after the grounding fix" \
  --summary "Shows coherent noise fell 20% after the grounding change, from runs 1200-1300; fits and figures." \
  --creators "A. Person" --date 2026-11-01 --subject detector/tpc/noise \
  --derived-from obs-… --derived-from dat-… --derived-from top-…
```

- The inputs are pinned by digest as they are now, so kb knows what the
  artifact presented.
- `provenance` says how much of its making is recorded: `cited` (its inputs,
  as here), `recorded` (also the steps), `rebuildable` (a view remakes it).
- kb extracts the PDF's text for search, as for any PDF.
- `kb new document --help` lists every field and the allowed doctypes.

## Find and follow them

```
kb artifact list                   # each artifact: doctype, provenance, inputs, who made it with whom
kb artifact list --stale           # those whose inputs changed since
kb search fit residuals            # finds words inside artifacts (in #text, page 3)
kb item links doc-…                # what it presents; and, from an input, "used to build" it
kb open doc-…                      # the PDF, or the demo in your browser
```

## When the knowledge changes

When an input is corrected (`kb item supersede`) or its status changes,
the artifact is stale: `kb artifact list --stale`, `kb item stale`, and
`kb status`. Remake or revise it:

- for a new version, add the remade file with `--supersedes doc-…`, so
  citations of the old one still say what it said;
- if it still holds (the change doesn't affect it), point it at the new
  input and pin it again:

  ```
  kb item set doc-… --replace-ref obs-OLD=obs-NEW
  kb item stamp doc-…
  ```

## HTML artifacts (demos)

A demo is several files: a page, its scripts, styles and data. They live
together in a source, usually a DataLad dataset (the artifact's working
directory, made with `datalad create`), and the document points at the
entry page there:

```
cd ~/work/noise-demo && datalad save -m "demo"
kb add ~/work/noise-demo/site/index.html --set doctype=demo --title ... --derived-from ...
```

`kb open doc-…` opens the page in its dataset, so its scripts and data load
by their relative paths. On another machine, it clones the dataset and
`datalad get`s the page's directory first. Share the dataset (a sibling,
`kb source add --shared`) so others can open it.

## Who made it: co-authors

Everything kb writes records who made it (`created.by`, as before) and who
took part, as git's `Co-Authored-By` does:

```
kb --coauthor "claude-opus-5-5 (Claude Code)" add ...
export KB_COAUTHOR="claude-opus-5-5 (Claude Code); A. Colleague"
```

In Claude Code, kb's session hook sets `KB_COAUTHOR` for every command of
the session, so what Claude writes says so without anyone remembering. It
shows in `kb show` ("made with") and in `kb log` ("per-ann with
claude-opus-5-5 (Claude Code)").

## LLM summaries

Any document (an artifact, a paper, slides, a topic) can have a summary
written by an LLM. It is a rendition, `summary`, kept with the document,
searched like extracted text (`in #summary`), and stale when what it
summarizes changes:

```
kb rendition summary doc-… summary.md        # the model: --model, else $KB_COAUTHOR
kb rendition summary doc-…#slides summary.md # of another rendition: slides-summary
```

kb doesn't write summaries itself; an LLM does, and registers them. When
one is stale, `kb doctor` says so, for an LLM to write it again.
