# 0004. Artifacts: technotes, demos and other made things

Status: **accepted in outline** (2026-11-01): AR1–AR5 decided as recorded
below ("Decisions"); AR6 (the gallery) and replaying LLM steps stay open.
Phase 1 is built (2026-11-01); see "Phases" and howto/artifacts.md.

## The need

People in the group, with LLM help, make *artifacts* that collect, present
and explain what they have learned: a LaTeX technote (PDF), an HTML page
with an interactive JavaScript demo, a slide deck, a notebook. Artifacts
hold figures and tables made by code from values. People want to:

- **make** them, with code, prompted LLM agents and their own iterations;
- **keep** them, and share them with the group;
- **browse, view and search** them, including what is inside (text,
  captions, tables);
- **trace** an artifact back to the knowledge it presents;
- **remake** it when that knowledge changes.

The tension: capturing an artifact's making as a fully reproducible DAG
would take the human's (and the LLM's) attention away from making a good
artifact. The design must give tracing and remaking **without asking for
attention while the work happens**.

## What kb has already

- **Documents**: primary items with renditions (a PDF original, extracted
  text), searched by rendition, chunk and page, and opened with `kb open`.
- **Views**: a recipe (source renditions plus a build tool: make,
  snakemake, quarto, pandoc, a command) that kb builds from items. The
  build records a manifest (input digests, source hashes, outputs), the
  output is published by manifest hash, and a view goes stale when its
  inputs change. A view is the fully reproducible case.
- **Topics**: LLM-written syntheses whose every sentence cites items. They
  are stamped (inputs pinned by digest), go stale when an input changes,
  and are revised by an LLM, not rebuilt.
- **Transforms**: text from PDFs, slides, web pages and images, with
  provenance (`derived_from`, `transform`, `fidelity`).
- **The graph**: `derived_from` (with digests), evidence, typed links and
  back links; `kb item stale`.
- **DataLad**: datasets as sources, the content dataset, `datalad run` and
  `rerun`.

An artifact is most of these at once: a document people read, made like a
view, revised like a topic, and its text extracted like any PDF.

## Proposal

### 1. An artifact is a document with a provenance record

There is no new storage system and no new kind of thing to learn. An
artifact is a `document` item (new doctypes: `technote` exists; add `demo`
for interactive HTML, and `notebook`). It has:

- **outputs** as renditions: `original` (the PDF, or the HTML entry page);
  `bundle` (a file set for a multi-file HTML artifact: page, scripts, data,
  images; an asset or a directory in the content dataset); extracted `text`,
  as now;
- **sources** as renditions (role `source`): the `.tex`, scripts, notebook,
  JS, so the artifact can be read and remade;
- **inputs**: the items it presents, in `derived_from` (with digests, as
  topics have), so it has back links ("used to build") and can go stale;
- **a process record**: how it was made (§3), a `metadata` rendition
  `process.json`, summarized in the item;
- **a provenance level** (§2), field `provenance`.

`kb ls --kind document --doctype technote,demo`, or a saved filter
`kb artifact list`, gives the collection.

### 2. Three levels of provenance, chosen per artifact

The middle ground is to let each artifact say how much of its making is
captured, record the cheap levels automatically, and treat staleness
according to the level.

| level | what is captured | cost while making | when an input changes |
|---|---|---|---|
| `cited` | the inputs (items it presents), stamped | none: harvested afterwards (§4) | flagged stale; a person or LLM revises it, as for a topic |
| `recorded` | inputs, plus the steps: code runs (`datalad run`), LLM steps (prompt, model, a summary of the exchange), human edits (commits) | none if made in an artifact workspace (§3) | code steps replay (`datalad rerun`); LLM and human steps are listed for a person to redo or accept |
| `rebuildable` | a view recipe that makes the outputs from the inputs | writing the recipe (make, snakemake, quarto) | kb rebuilds it (`kb view build`), as views are now |

An artifact can move up a level when it proves worth it. Most start
`cited` or `recorded`; the ones remade often (a weekly status note) earn a
recipe.

### 3. The artifact workspace: capture without attention

An artifact is made in a **workspace**: a DataLad dataset that kb creates
and registers as a source (`kb artifact start "TPC noise technote"`), or
an existing one adopted. Everything done there is captured as a side
effect:

- **What kb served.** kb commands run in the workspace (`kb show`,
  `kb context`, `kb item content`, `kb rendition path`, `kb open`, search
  hits that were then read) append the items and digests they served to the
  workspace's journal, `.kb-artifact/journal.jsonl`. This is how inputs are
  captured without anyone listing them.
- **Code.** `kb artifact run -- CMD` is `datalad run` with kb's inputs
  materialized (an `inputs/` directory and `inputs.json`, as view builds
  have) and declared, so the commit records the command, its inputs and
  its outputs. Plain `datalad run` works too. Edits are ordinary commits.
- **LLM steps.** A Claude Code hook (PostToolUse, Stop) appends what the
  session did in the workspace: the prompt, the model, the files it
  changed, the kb items it read. It records a pointer to the transcript and
  a short summary, not the whole transcript, for privacy and size.
- **Human steps.** Commits by a person, with their messages.

Nobody has to stop and describe the process while making the artifact.
The workspace's git history plus the journal is the raw record.

### 4. Harvest: from the raw record to the item

When the artifact is ready, `kb artifact finish` (or the `kb-artifact`
skill) turns the workspace into the item:

1. **Outputs**: the declared output (the PDF, the HTML entry and its
   bundle) become renditions, placed as large content is (content dataset
   or content store), and their text is extracted (PDF text, the HTML's
   main content, figure captions, tables as CSV). Each figure that was made
   from data keeps a link to the data item or the script.
2. **Inputs**: the items served in the workspace become candidate inputs.
   An LLM (or the person) prunes them to what the artifact presents. They
   go into `derived_from` with their digests, and the artifact is stamped.
3. **Process**: the journal and the `datalad run` commits become the
   process record: a DAG of steps (§5). An LLM writes a one-paragraph
   account of how the artifact was made (a `digest`, fidelity `llm`).
4. **Level**: `cited`, `recorded` or `rebuildable`, from what was captured.
5. **New facts**: facts established while making it (a fit result, a
   number read off a figure) become observations citing the artifact
   (kb-capture), so the artifact's knowledge is in the KB itself, not only
   in its PDF.

The workspace stays: a datalad source item with its URL, so the
artifact's sources and history can be cloned later.

### 5. The process record

`process.json` holds a small DAG:

```json
{"level": "recorded",
 "workspace": "src-…",                    // the DataLad dataset
 "steps": [
   {"id": "s1", "type": "kb", "reads": ["obs-…@<digest>", "dat-…@<digest>"]},
   {"id": "s2", "type": "code", "run": "<commit>", "command": "python fit.py",
    "inputs": ["s1", "inputs/dat-….csv"], "outputs": ["fig/noise.pdf", "fit.json"]},
   {"id": "s3", "type": "llm", "model": "claude-opus-5-5", "prompt": "Draft section 2 from fit.json …",
    "inputs": ["s2"], "outputs": ["note.tex"], "transcript": "…", "summary": "…"},
   {"id": "s4", "type": "human", "commit": "<commit>", "outputs": ["note.tex"]},
   {"id": "s5", "type": "code", "run": "<commit>", "command": "latexmk note.tex",
    "inputs": ["s3", "s4", "s2"], "outputs": ["note.pdf"]}],
 "outputs": {"original": "note.pdf"}}
```

Step types: `kb` (items read), `code` (replayable), `llm` (regenerable, not
reproducible), `human`, and `tool` (an external program run by hand). Item
references keep their digests. `kb artifact graph ID` shows it (dot or
text), joined to the KB's own graph at the items.

### 6. Remaking

`kb item stale` already reports artifacts whose inputs changed (they have
`derived_from` with digests). `kb artifact remake ID` then acts by level:

- **rebuildable**: build the view, publish, and supersede the artifact with
  the new outputs;
- **recorded**: clone the workspace, update `inputs/` to the current
  items, `datalad rerun` the code steps downstream of what changed, and
  list the LLM and human steps for review (with their prompts, so an LLM
  can redo them under supervision). The result is a new version, which
  supersedes the old after review;
- **cited**: open a revision task, as for a stale topic.

Artifact versions are items, as documents are: a new version supersedes the
old, so links and citations keep pointing at what was said when.

### 7. Collections, viewing and sharing

- **Browsing**: `kb artifact list` (title, doctype, date, level, stale,
  thumbnail). A built-in **gallery view** makes a static HTML index of a
  store's artifacts (or a subject's): title, summary, thumbnail (R3), what
  each presents (its inputs), links to open it. The gallery is published
  like any view, for example to EOS web space for the group.
- **Viewing**: `kb open ID` opens the PDF, or materializes the HTML
  bundle in the cache and opens its entry page in a browser, so the demo's
  JavaScript works.
- **Searching**: artifacts are documents, so their extracted text,
  captions and tables are searched by rendition and page. A hit names the
  artifact, and its back links lead to the knowledge it presents. The
  `digest` rendition describes figures and interactive behavior that text
  extraction can't see.
- **Sharing**: knowledge sets and bundles carry the artifact item; its
  outputs travel by the content dataset (siblings, `datalad push`) or the
  publish store. A `deep` set also carries its inputs, so the receiver can
  trace and remake it.

### 8. What this asks of people and LLMs

- Make artifacts in a workspace (`kb artifact start`), and get knowledge
  through `kb` there. That is the one habit the design relies on: it is
  what makes inputs capturable.
- Say when it's done (`kb artifact finish`), and prune the inputs it
  proposes.
- Write a recipe only for artifacts that are remade often.

A `kb-artifact` skill would carry these habits for Claude, as `kb-research`
does for research.

## Decisions (2026-11-01)

- **AR1. An artifact is a document**, with doctypes `technote`, `demo` and
  `notebook`.
- **AR2. Three provenance levels**: `cited`, `recorded`, `rebuildable`.
- **AR3. The artifact workspace**, with what kb served as the proxy for
  inputs. People bounce between artifacts, and between an artifact and
  other work, and won't keep workspaces apart by switching. So kb keeps no
  "current workspace" state to switch. Instead:
  - **The workspace is where the work is.** kb finds it from the working
    directory, as it finds a store context: the nearest directory that is
    an artifact workspace.
  - **Every journal entry says where and when.** It records the working
    directory, the time and the session (Claude Code's session ID, when
    there is one). Work done outside any workspace goes to the store's own
    journal.
  - **Harvest offers candidates and asks.** It proposes, from every
    journal, what falls in the artifact's time span or session, marked by
    how sure kb is: done in the workspace, or only in the same session.
    The person or LLM claims or rejects each.
  - **Misplaced work can be moved.** A step logged in the wrong workspace is
    reassigned at harvest, not lost.
- **AR4. Every write says who made it, people and LLMs alike**, as git's
  `Co-Authored-By` does. A write records the person (`created.by`, as now)
  and the co-authors that took part: an LLM (`claude-opus-5-5 via Claude
  Code`) or another person.
  - **How it is given:** `--coauthor NAME`, or `KB_COAUTHOR`. kb's
    SessionStart hook sets `KB_COAUTHOR` for every command the session
    runs (through Claude Code's session environment file), so LLMs don't
    have to remember it.
  - **Where it is kept:** in the item (`created.with`) and in the event
    log, so `kb log` shows it.
- **AR4. Prompts can be documents**: doctype `prompt`, captured by a
  UserPromptSubmit hook when the prompt is given in an artifact workspace
  (opt-in, and never outside one). The prompt's text is the item's
  content, and its co-author is the model that answered. It is linked
  `part-of` the artifact at harvest. What else an LLM step would need for
  replay stays open (AR4b).
- **AR4. LLM summaries of anything.** A rendition `summary` (role `derived`,
  fidelity `llm`, `derived_from` the rendition it summarizes, the model as
  its transform and co-author). It is to an LLM what extracted text is to
  pdftotext: kb supports it for any document (and any item with content),
  indexes it like other text, and search hits say `in #summary`. kb does
  not call an LLM itself: an LLM writes the summary and registers it
  (`kb rendition summary ID FILE`, a shorthand for `kb rendition add ...
  --name summary --role derived --fidelity llm --from original`). A summary
  goes stale when what it summarizes changes, as extracted text does; the
  doctor lists stale summaries for an LLM to rewrite. (A summary is a
  rendition, not an item of its own: it belongs to its document, travels
  with it, and is replaced, not superseded, when rewritten.)
- **AR5. A multi-file HTML artifact lives in a source** (a DataLad
  dataset, usually the artifact's workspace, or a git or filesystem
  source). The document's rendition points at the entry page in that source
  (a `datalad` or `source` location), and `kb open` opens it there so its
  scripts and data load.
- **Open**: AR6 (the gallery, which may take several forms by person) and
  AR4b (capturing enough of an LLM step to replay it).

## Options considered

- **A new item kind `artifact`.** Clearer in listings, but it duplicates
  `document` (renditions, extraction, search, open, supersede). Rejected for
  now: a doctype plus `provenance` gives the same, and `kb artifact` commands
  give the listing.
- **Full DAG capture always** (every step replayable). This is the tension
  named above: it costs attention and fails for LLM steps, which are not
  deterministic. Kept as the top level (`rebuildable`), not the rule.
- **Only `derived_from`** (the `cited` level alone). Cheap, but it can't
  tell what to redo when something changes. Kept as the bottom level.
- **Capturing whole LLM transcripts** in the store. Large, and possibly
  private beyond the artifact's access level. Rejected: kb keeps a pointer
  and a summary, and the transcript stays where the session keeps it.
- **A workflow engine** (snakemake for everything). It suits `rebuildable`
  artifacts, via views, not exploratory making.

## Phases

1. **Artifacts as documents, attribution and summaries.**
   - doctypes `demo`, `notebook` and `prompt`;
   - co-authors on every write (`--coauthor`, `KB_COAUTHOR`, set by the
     session hook);
   - LLM `summary` renditions for any item, indexed, going stale with what
     they summarize;
   - `derived_from` and stamping for documents, so they go stale like
     topics;
   - HTML bundles in a source (an entry page plus files), with `kb open`
     opening them there;
   - text extraction for HTML artifacts (figures' captions, tables);
   - `kb artifact list`.

   Value with no new habits: tracing (`cited`) and finding.
2. **Workspaces and harvest.**
   - `kb artifact start`, `run` and `finish`;
   - the journals of what kb served (located by working directory, time and
     session, with no workspace switching);
   - the Claude Code hooks for LLM steps and `prompt` documents;
   - `process.json`;
   - the `kb-artifact` skill.

   Gives the `recorded` level.
3. **Remake.**
   - `kb artifact remake` (rerun code steps, list LLM and human steps);
   - versions by supersession;
   - the gallery view;
   - thumbnails.

The `rebuildable` level is views, which exist.

## Questions for the group

See [questions](../questions.md) AR1–AR6.
