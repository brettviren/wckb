# Artifacts: technotes, demos and notebooks

An *artifact* is something you make (often with an LLM) to collect, present
and explain knowledge: a LaTeX technote, an HTML page with an interactive
demo, a notebook. In kb an artifact is a `document` of doctype `technote`,
`demo` or `notebook`, with the items it presents as its inputs. That gives
it what documents have (search of its text, `kb open`, versions by
supersession), and it goes stale when the knowledge it presents changes.

This guide covers what is built (phases 1 and 2 of
[decisions/0004](../decisions/0004-artifacts.md)): artifacts as documents,
workspaces that record how an artifact was made, co-authors, LLM summaries,
and HTML artifacts opened from their source. Claude does this with the
`kb-artifact` skill.

## Make one in a workspace

A workspace is a DataLad dataset where an artifact is made. While you work
there, kb notes, without being asked:

- what it gives you to read (`kb show`, `kb item content`, `kb open`,
  `kb rendition path`, `kb context ID`): the artifact's inputs;
- what you run with `kb artifact run` (a `datalad run`);
- with kb's hooks, the prompts an LLM is given there and the files it writes.

```
kb artifact start ~/work/noise-note --title "TPC noise after the grounding fix"
cd ~/work/noise-note
kb show obs-…; kb item content dat-… ; kb open top-… --print     # read through kb, here
kb artifact run --input dat-… -- python fit.py inputs/dat-…-noise.csv
kb artifact run -- latexmk -pdf note.tex
kb artifact status                    # what the journal holds so far
kb artifact inputs                    # what was read: here, or elsewhere in the same session
kb artifact finish note.pdf --summary "..." --creators "A. Person" --date 2026-11-02 \
    --subject detector/tpc/noise --drop obs-…
kb artifact steps doc-…               # how it was made
```

`finish` makes the artifact document:

- **Its file:** the output, kept in the workspace (a `datalad` location).
- **Its inputs:** what was read in the workspace, pruned with `--drop`, with
  `--input` adding any you read another way. They are pinned as they are
  now.
- **Its process record:** the reads, the runs (with their commits), each
  LLM session (its model, prompts and the files it edited) and the other
  commits. Its provenance is `recorded` when it has runs or LLM steps, else
  `cited`.
- **Prompt documents:** each prompt given there becomes a document (doctype
  `prompt`), `part-of` the artifact, with the model as co-author.
  `kb artifact start --no-prompts` keeps none.

Finishing again makes a new version, which supersedes the last.

**Moving between pieces of work** needs no care: there is no workspace to
switch. kb finds the workspace from the working directory, and notes where,
when and in which session everything happened. Something read outside the
workspace in the same session is offered at `finish` as `session` (kept
with `--with-session`), and anything misplaced is dropped or added there.

In Claude Code, kb's session hook sets `KB_SESSION` (and `KB_COAUTHOR`),
and the hooks `kb-artifact-prompt-hook` and `kb-artifact-edit-hook` note
prompts and edits in workspaces only. Select them in an existing store with
`kb policy use kb-artifact kb-artifact-prompt-hook kb-artifact-edit-hook`
and `kb policy enact`.

## Notes you already have: adopt a work area

Notes grow next to the work they summarize: scripts, run output, builds,
git clones. They stay there. `kb artifact adopt` makes the directory of one
topic a workspace, around what is there; nothing is moved or changed:

```
kb artifact adopt ~/dune/xero/topics/noise --title "Noise in the xenon runs"
git -C ~/dune/xero/topics/noise status --ignored      # check what is ignored
```

Make each topic's directory the workspace, not the whole work area: it is the
unit you publish, and a work area of terabytes stays out of it. kb:

- makes the directory a DataLad dataset (or uses the git repository or
  dataset it is);
- **ignores git repositories nested in it** (clones with their own history
  and remotes: not subdatasets, which would have to be clean for every
  `kb artifact run`), and notes where each came from and which commit it was
  at in `.kb-artifact/repos.json`; `finish` refreshes that and puts it in
  the artifact's process record;
- **ignores build products**: `*.o *.obj *.a *.lib *.so *.so.* *.dylib *.dll
  *.exe`, Python caches and environments, `build/ _build/ CMakeFiles/`, an
  install prefix at the top (`/bin /lib /lib64 /include /install`), LaTeX
  leftovers (`*.aux *.fls *.toc ...`). A source directory named `lib` deeper
  down is kept. Add to the list with `--ignore PATTERN`, or for every
  workspace in a store with
  `kb config set workspace.ignore '["*.root", "scratch/"]'`;
- saves what is there. It does not look into your own git ignore rules
  (`~/.config/git/ignore`): files they match are skipped without a word, so
  check `git status --ignored` for what you expected.

Run it again on a workspace to refresh these settings and pick up new
clones. `kb artifact start` makes a new workspace with the same settings.

### Annexed files that stay writable

git-annex normally locks what it annexes: a read-only link to an object. A
job that writes to such a file fails. A workspace is set up to avoid that:

- only files **above 1MB** are annexed (`--largefiles SIZE`, or
  `kb config set workspace.largefiles 5MB`); scripts, notes, small figures
  and PDFs are plain git, so they are never locked;
- what is annexed is **unlocked** (`annex.addunlocked`): an ordinary
  writable file. Cost: a second copy of annexed content, only for what is
  large;
- the hash backend is SHA256E, so kb can use the file's digest.

`git config` does not travel with a clone: `kb doctor` (`workspace-annex`)
sets `annex.addunlocked` in a workspace clone that lacks it. Files annexed
earlier and locked: `git annex unlock PATH`. `kb artifact run` saves your
work before it runs, because `datalad run` needs a clean tree.

## Describe a note: the sidecar

Notes are Markdown, org, LaTeX, Quarto (`.qmd`, kept as Markdown) or HTML;
kb converts none. What every note has is a small TOML file beside it,
`note.toml` (or `NAME.note.toml` for a directory with several notes):

```toml
title = "Noise in the xenon runs"
summary = "Coherent noise fell 20% after the grounding change (runs 1200-1300)."
subject = "detector/tpc/noise"
creators = ["A. Person"]
date = 2026-11-09
doctype = "technote"           # technote, demo or notebook
tags = ["noise"]
inputs = ["obs-…", "dat-…"]    # items it presents, as if read in the workspace
source = ["note.org", "figs/make.py"]    # for the record
build = "make note.pdf"                  # for the record
```

`kb artifact finish note.pdf` reads it; options on the command line
override it. The deliverable is a PDF, or an HTML page that works as a unit
(scripts and styles inline, or beside it). `source` and `build` go in the
process record, so the next reader knows how to remake it.

## Claude artifacts (claude.ai/artifact)

A page published from a Claude session is private to you, can change under
the same URL, and can go away. Ingest a copy, and keep the URL:

1. In a Claude session (the `Artifact` tool reads your own artifacts): list
   its files (`list` with scope `files`). A single page is read with
   `read` and saved as a local file; a multi-file one with `read` of its
   `paths` and an `out_dir`.
2. Put the files in a workspace (`kb artifact start ~/work/NAME --title ...`).
3. `kb artifact finish index.html --doctype demo --published-at
   https://claude.ai/artifact/ID --summary "..."`. The URL and the
   artifact's ID are kept as identifiers.
4. When it is republished, read it again and finish again: the new version
   supersedes the old.

There is no `kb` command that fetches a claude.ai page by itself yet: it
needs a login (decisions/0005, question AR9).

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
