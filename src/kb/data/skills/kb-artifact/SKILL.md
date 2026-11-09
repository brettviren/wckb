---
name: kb-artifact
description: Make an artifact (a technote, an HTML demo, a notebook) that presents knowledge from the knowledge base (kb), so it can be traced back to that knowledge, found, shared, and remade when it changes. Use when the user wants to write up, present, plot, demo or explain what the KB holds, or says "make a technote/demo/notebook".
---

# Make an artifact with the KB

An artifact is a document made here that presents knowledge: a technote,
a demo or a notebook. kb records what it presents and how it was made, so
it can be traced, found and remade. The habits below cost little; don't
let them get in the way of making a good artifact.

## 1. Start a workspace

```
kb artifact start ~/work/noise-note --title "TPC noise after the grounding fix"
cd ~/work/noise-note
```

The workspace is a DataLad dataset. Work in it: sources (`.tex`, `.py`,
`.js`), data, outputs. kb finds it from the working directory; there is
nothing to switch. If you work on something else in between, that is fine:
kb notes where everything happened, and you sort it out at the end.

A directory that already holds the work (scripts, results, clones, an
existing note) is adopted, not started over: `kb artifact adopt DIR`. It
changes nothing there; it ignores nested git clones and build products
(`*.o *.so *.a build/ /bin /lib /include`) so they don't bloat the dataset,
and annexes only files above 1MB, unlocked, so no file is read-only. Do not
`git annex add` or lock things by hand. Check `git status --ignored` if a
file you expected isn't there (the user's own git ignore rules apply).

## 2. Read the knowledge through kb, there

Get what the artifact presents with `kb` commands run **in the workspace**:
`kb show ID`, `kb item content ID text`, `kb open ID --print`,
`kb rendition path ID#NAME`, `kb context ID`. kb notes each item it gives
you, so the artifact's inputs are known without listing them. Searching
(`kb search`, `kb context SUBJECT`) is not noted: read the hits you use.

## 3. Make it, and run code through kb

Run code that turns knowledge into figures and tables with
`kb artifact run`, so the commit says what it read and made:

```
kb artifact run --input dat-… -- python fit.py inputs/dat-…-noise.csv
kb artifact run -- latexmk -pdf note.tex
```

Edit files as usual (kb's hooks note an LLM's prompts and edits here). File
what you establish along the way (a fit result, a number read off a figure)
as observations with kb-capture, citing what they came from.

Describe the note in `note.toml` beside it, whatever its format (Markdown,
org, LaTeX, `.qmd`, HTML): `title`, `summary` (one to three sentences for
the next LLM), `subject`, `creators`, `date`, `doctype`, `tags`, `inputs`
(item IDs it presents), `source` (its files) and `build` (its command).
`kb artifact finish` reads it. Use the format already in the directory; if
there is none, ask which. The deliverable is a PDF, or an HTML page that
works as a unit.

## 4. Finish it

```
kb artifact inputs                       # what was read: here, or elsewhere in the same session
kb artifact finish note.pdf --summary "..." --creators "A. Person" --date 2026-11-02 \
    --subject detector/tpc/noise --drop obs-…   # not an input after all
```

- Prune the inputs to what the artifact presents (`--drop`), add any read
  outside kb (`--input`); `--with-session` takes what was read elsewhere in
  the same session.
- A demo: `kb artifact finish site/index.html --doctype demo ...`; it opens
  from the workspace with its scripts (`kb open ID`).
- Then write a short summary of the artifact (what it shows, its key numbers)
  and register it: `kb rendition summary ID summary.md`.
- Check the record: `kb artifact steps ID`, `kb item links ID`, `kb check`.

### An artifact published on claude.ai

To keep a claude.ai artifact (a page you or the user published), ingest a
copy: list its files with the `Artifact` tool (`action: list`, `scope:
files`), read a single page with `action: read` (it is saved as a local
file), or a multi-file one with `read` of its `paths` and an `out_dir`.
Put the files in a workspace and
`kb artifact finish index.html --doctype demo --published-at URL --summary "..."`.
Read and finish again when it is republished: a new version supersedes the old.

## 5. Later

`kb artifact list --stale` shows artifacts whose inputs changed. Remake it
in its workspace and `kb artifact finish` again (a new version supersedes
the old), or, if it still holds, `kb item set ID --replace-ref OLD=NEW` and
`kb item stamp ID`.
