# Rendering knowledge for review and reading

People review what arrives in a store, and read what a store holds. They
can't judge hashes and JSON; they judge *material*: who said what, when,
about which runs, backed by what, and what it actually looks like. This
document records what rendering does now (spike S2) and where it is going.
Open questions are in [questions.md](questions.md), section R.

## Architecture

Rendering has two stages, so that every front end shows the same thing:

1. **A view model** (`kb.render`): plain dicts built from items, sets and
   bundles, with human-friendly values.
   - names instead of person IDs (bundles carry the people they mention);
   - dates such as "28 Oct 2026", sizes such as "46.1 MB", run ranges such as
     "runs 1200–1300";
   - identifiers turned into links (DOI, arXiv, INSPIRE, CDS, and Indico
     through the store setting `links.indico`);
   - where each rendition's material is, in words ("held in the store",
     "online, needs sso", "content store 'default' (/eos/...)");
   - an excerpt of the material itself (content text, extracted text, or a
     textual original);
   - relations with titles ("evidence: TPC noise update (doc-…)");
   - for bundles: the provenance, what accepting would do (new, changed,
     already here, conflicting, dangling references), field-level changes
     for updated items, conflict reasons, and reviews.

   There are no hashes. Item IDs appear only as handles for commands.
2. **Formatters** turn the view model into an output:

   | format | where | status |
   |---|---|---|
   | terminal text | `kb show`, `kb bundle show`, `kb inbox show` | done |
   | markdown | `--format md`: pull request descriptions (`--land pr`), LLM reading, pasting into chats and email | done |
   | JSON (the view model itself) | `--format json`: LLMs, scripts, future front ends | done |
   | TUI | navigable tree and item panes in a terminal | planned |
   | web | browsable store and review queue | planned |

## Per-kind rendering (now, and next)

| kind | now | next |
|---|---|---|
| observation | statement excerpt, attribution, validity, status, evidence with titles | a timeline of the subject and runs it covers; contradictions with other current items highlighted (from `kb lint` overlap) |
| document | type, creators, event, date, links (arXiv, DOI, Indico, URL), extracted-text excerpt | **slide thumbnails** (first pages as images via `pdftoppm`, stored as derived `thumbnail` renditions); a per-slide outline with titles; the abstract for papers |
| dataset | format, variables with units, producer | first rows as a table; column statistics from `datapackage.json`; a quick plot for 1-D histograms (ROOT summary) |
| config | system, validity, parameter table | a diff against the previous configuration of the same system |
| snapshot | system, query, retrieval time | as dataset |
| topic | page excerpt, cited items with titles | the full page rendered as markdown, with citations as links to the cited items; stale-input warnings inline |
| view | build tool and target, audience | a link to the latest publication; thumbnails of output pages |
| kset | members as a tree, nested sets unfolded to a chosen depth | collapsible tree (TUI/web); per-node badges (new, changed, conflict, missing); filter by kind |
| policy | name, version, type, stage | rendered text; a diff against the previous version |
| person | name, affiliation | what they asserted, recently |
| asset | file or file set, versions (files, size, when registered, where seen), sources described | a per-version file list with sizes, and a diff between versions; per-source status from the last scan (known, new, conflict) |

## Navigating hierarchy

A knowledge set is a tree (sets nest), and a deep set is a graph (items
reference each other). The terminal shows the member tree to `--depth N`,
with "… N more" beyond that.

Next:

- **TUI** ([Textual](https://textual.textualize.io/) is a good fit: pure
  Python and fast). A tree on the left, the selected item's card on the
  right. Keys to expand and collapse, open the material (`kb rendition path`), jump
  along references, and, in review mode, record a note or verdict.
- **Web** (Flask, or FastAPI plus htmx; server-rendered, with no build
  step). A page per item and per bundle, a collapsible set tree, and
  material inline:
  - PDFs in the browser's viewer;
  - slide thumbnails;
  - markdown rendered;
  - tables for CSV.

  A review queue page per store: inbox entries with their renderings, the
  reviews so far, and accept/reject buttons (respecting `accept`
  autonomy). Authentication comes from the host (a reverse proxy with the
  lab's SSO) rather than from kb. A read-only mode serves a published
  snapshot.
- **Static HTML export** (`kb show --format html`, and
  `kb bundle show --format html`): one self-contained file, so a bundle
  can be reviewed by email or on a web server with no kb installed.

## Material, not just metadata

Excerpts are the first step. To judge material a reviewer should also be
able to see:

- **Slides:** thumbnails of the first few pages, plus titles of all slides.
  Made by a transform (`thumbnails`) into derived renditions, kept in the
  content store when large.
- **Plots and figures:** images that *are* the data are shown. Plots that a
  view makes are shown from the view's published output.
- **Long text:** the excerpt, with a "full text" link to the whole rendition
  (web), or `kb item content ID NAME` (terminal).
- **External material:** a link always; a cached copy when the reviewer's
  machine can fetch it (Indico behind SSO: through the data source's
  credentials).

## For LLM reviewers

LLMs read the markdown or JSON rendering:

- markdown is compact and contains the same judgments a person sees;
- JSON gives exact fields, plan details and the tree for systematic
  checks.

The `kb-review` skill tells Claude to judge item by item, record a
specific review (`kb inbox review`), and leave the decision to the human
unless `accept` autonomy allows otherwise. A later step is for LLM
reviewers to attach *structured* findings (per-item verdicts, suspected
duplicates with IDs) as JSON alongside the prose, for the web review page
to display.

## Implementation notes

- The view model is the contract. New front ends consume `kb show
  --format json` (or the Python functions) and add no logic of their own
  about what to show.
- Rendering must stay cheap: no network calls by default. Fetching
  external material is an explicit action.
- Optional dependencies, so the core stays light: `textual` (the `tui`
  extra), `flask` (the `web` extra), and `pdftoppm` from poppler for
  thumbnails.
