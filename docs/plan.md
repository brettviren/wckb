# kb: implementation plan

This plan delivers the vertical slice described in [design.md](design.md). It
is organized as phases, each ending with something the group can use and a
point at which to decide whether to continue as planned.

## Phase 0: foundation (this repository)

Deliverables:

| item | where | status |
|---|---|---|
| JSON Schemas v1 for nine kinds | `src/kb/schemas/v1/` | done |
| Item model, IDs, canonical JSON and digest | `src/kb/items.py`, `ids.py` | done |
| Configuration: store settings, config files (user, here and above, `-c`), `.kb/local.toml`, `KB_*` env | `src/kb/config.py` | done |
| Store contexts: which store a command uses (`--context`, `KB_CONTEXT`, `git-directory`) | `src/kb/config.py` | done |
| Logging: daily files, `-l`, `-L`, `--debug` | `src/kb/logs.py` | done |
| Indico sources: DataLad datasets filled by git-annex-remote-indico (`kb source add URL`) | `src/kb/indico.py`, `sources.py` | done |
| `kb add`: metadata found (arXiv, PDF, EXIF); `--target` copies into a source | `src/kb/metadata.py`, `authoring.py` | done |
| Search by rendition and chunk; text extracted on add; vectors as a cache by model and text | `src/kb/items.py`, `query.py`, `embed.py`, `transforms.py` | done |
| Shared sources on one computer: shared datasets, shared clones (`--shared`, `kb source share`) | `src/kb/sharing.py` | done |
| The SQLite store: items, blobs, events, settings, inbox; guard triggers; transactions | `src/kb/db.py`, `items.py` | done |
| Data sources (polymorphic): db, cas, url, local, asset, datalad | `src/kb/locations.py`, `content.py` | done |
| DataLad datasets: source items, clones by dataset ID, `datalad` locations and asset source (optional extra) | `src/kb/datasets.py` | done |
| `kb check` (gate) and `kb lint` (advice) | `src/kb/checks.py`, `lint.py` | done |
| Append-only in the database; consistency and tamper scan | `src/kb/db.py`, `checks.py` | done |
| Query tables, full-text search, faceted listing | `src/kb/query.py` | done |
| Embeddings and semantic or hybrid search | `src/kb/embed.py`, `search.py` | done |
| Transform registry with built-in transforms | `src/kb/transforms.py` | done |
| Dependency graph, staleness, stamping | `src/kb/graph.py` | done |
| View build, manifest, publish | `src/kb/build.py` | done |
| Editing within policy: `kb item set`, `kb rendition add\|locate` | `src/kb/edit.py` | done |
| LLM context packs (`kb context`) | `src/kb/context.py` | done |
| Migrations framework | `src/kb/migrate.py` | done |
| FAIR export: RO-Crate, datapackage inference | `src/kb/fair.py` | done |
| `kb init --scope`: project, home, group stores with scope defaults | `src/kb/scaffold.py` | done |
| Federation: neighbors (up, peer, down), reachability, federated ls/search/context | `src/kb/federation.py`, `federated.py` | done |
| Promotion: candidates, push (same IDs, origin, deps, access relabeling, modes), ledger | `src/kb/push.py` | done |
| Policy as items: builtins, sources, resolve, draft/release/revise, enact, status, capture, diff | `src/kb/policy.py`, `src/kb/data/policies*` | done |
| Autonomy: per-action levels, agent detection, `--approved` audit trail | `src/kb/config.py`, `push.py`, `policy.py` | done |
| Claude Code hooks: check, recall, session | `src/kb/hooks.py` | done |
| Knowledge sets (shallow/deep, follow/inherit) and the transfer engine: atomic copy, apply/inbox/pr landing, multi-path reconciliation, ledgers | `src/kb/transfer.py` | done |
| Edge transfer forms (push/pull select/all/none, accept, prefer), pull, obtain, store-independent references | `src/kb/federation.py`, `push.py` | done |
| Twins: item-level sync, newer wins | `src/kb/sync.py` | done |
| Bundles, inbox, human and LLM reviews, accept autonomy | `src/kb/bundle.py` | done |
| Rendering for people: view model; text, markdown, JSON | `src/kb/render.py` | done |
| Checkout/checkin/edit, content, snapshots, history (`kb log`) | `src/kb/edit.py`, `items.py` | done |
| Exchange repositories: bundle pull requests; git hooks guarding bundles | `src/kb/exchange.py`, `githooks.py` | done |
| Assets: abstract files/file sets, content-identified versions, bindings (kb, fs, url), scan/register/resolve, the `asset` rendition location | `src/kb/assets.py` | done |
| Forges: GitHub, Gitea, Forgejo (Gogs: no PR API) pull requests | `src/kb/forge.py` | done |
| Secrets: command (pass), 0600 file, env, value | `src/kb/secrets.py` | done |
| CLI | `src/kb/cli.py` | done |
| Tests | `tests/` | done |
| How-tos; generated schema and CLI references (kept in sync by tests) | `docs/howto/`, `docs/reference/` | done |

## The store decision

[Decision 0001](decisions/0001-knowledge-store.md) was accepted on
2026-10-31: knowledge items live in a SQLite store per store. Spikes S1 (the
store) and S2 (bundles, inbox, review, rendering) are done. S3 (DataLad as a
data source) is deferred until DataLad is better understood. S4 (twins)
has a first version (newer wins).

## Phase 1: pilot (2–4 weeks, two or three people)

Steps:

1. Create the group store with `kb init --scope group` on its writer's local
   disk. Configure a content store (`cas`) and a publish store on shared
   storage, an exchange repository for pull requests (`kb exchange init`)
   with CI (`docs/howto/ci.md`), and a nightly `kb store snapshot` for members.
2. Each pilot user installs `kb` with `uv tool install`, creates a home store
   (`kb init ~/kb --scope home`), links the group store as its `up`
   neighbor (its published snapshot, plus the exchange repository), adds `[neighbors.home]` to their user config so every project
   store sees it, and creates a project store (`kb init ./kb --scope project`)
   in one active project. Each sets up a forge token (`kb forge auth`), and
   the group store's edge lands pushes as pull requests (`--land pr`).
3. Seed the KB with one real topic: its slides from Indico, two or three
   papers, a handful of observations, one dataset, and one view built for a
   real meeting.
4. Keep a log of friction: every time `kb check` was wrong, every time a skill
   did the wrong thing, and every field nobody used or somebody missed.

5. Try the routine behavior for real: does Claude consult and file without
   being reminded? Is the recall hook useful or noise? How many items does
   a week of `capture = auto` produce, and how many survive promotion? Feed
   the answers into [questions.md](questions.md) (D1–D3).

**Exit criteria:**

- At least one question was answered from the KB by someone who didn't add
  the material.
- At least one correction propagated to a rebuilt view.
- Items travelled project → home → group by promotion reviews, and the
  human effort per review was a few minutes.
- At least one policy was revised from daily use (`kb policy capture`),
  released and adopted by another member.
- The schema changes the pilot asks for are known.

## Phase 2: hardening, informed by the pilot

Rendering next steps (TUI, web, thumbnails) are in [rendering.md](rendering.md),
and data sources (DataLad, Indico) in questions DS1–DS4. The next sources
are asset sources ([decision 0002](decisions/0002-assets-and-versions.md)):
DataLad is done ([decision 0003](decisions/0003-datalad-sources.md)); Indico
and DocDB follow once questions V1 and V2 are answered, as source items
like DataLad datasets.

Planned, in rough priority order. Open design questions are tracked in
[questions.md](questions.md):

- **Schema 1.x revisions** from pilot feedback. Then exercise the migration
  path with one real breaking change, if the pilot calls for one, before
  the KB grows.
- **Indico importer.** Event and contribution metadata become `document`
  items. Reuse the orgface Indico access code rather than writing new code.
- **arXiv source fetching**: prefer the LaTeX source over the PDF for text
  renditions.
- **MCP server** exposing `ls`, `show`, `search`, `context` (federated),
  `candidates` and `stale`, read only.
- **Policy evaluation**: scripted scenarios to compare policy versions
  (questions C2).
- **Upstream news**: what happened in a parent to records this store pushed
  (questions B10).
- **Plugin marketplace.** Package the skills as a Claude Code plugin in a git
  marketplace so updates arrive with one command.
- **sqlite-vec** for the vector index once it holds more than tens of
  thousands of chunks (brute-force cosine is fine below that).
- **Published-view status.** A `_kb_status.json` next to each published
  version listing superseded inputs, and a banner in HTML views.
- **HDF5 and Jupyter transforms**, and **docling as a recommended command
  transform** once its output has been compared on our own slides.
- **Browse site.** Quartz or MkDocs over topic pages, and a Datasette
  configuration over the index.

## Phase 3: scale-out (only when needed)

- Store compaction and archiving for closed periods (questions S6).
- Searching many stores at once without opening each (a shared index of snapshots).
- Service-account CI for verifying SSO-protected URLs.

## Risks

| risk | mitigation |
|---|---|
| The KB fills with low-value captures | Promotion goes through review (the receiver's inbox or a pull request), the `kb-capture` skill is told to be selective, and `kb-curate` reviews old unverified claims. |
| Schema friction stops people contributing | `kb new`/`kb add` write the plumbing; the hook explains errors; `x` is the escape hatch; schema revisions are cheap. |
| Topics drift from their sources | Stamping with digests, `kb item stale` in CI, and corrections travel with the topic revisions they caused. |
| External URLs rot or change | Hashes, `kb rendition verify`, blob-store snapshots of important originals. |
| Python start-up cost in tight LLM loops | Lazy imports; `kb context` gathers in one call what would otherwise be many calls; a compiled reader later if needed. |
| Over-engineering | Phase gates; nothing in Phase 2 starts without a pilot user asking for it. |

## Development conventions

- `uv sync`, `uv run pytest`, `uv run kb`.
- The CLI (`cli.py`) parses arguments, calls logic modules and renders output.
  It contains no data processing.
- Tests run against real temporary stores and git repositories. They
  don't mock internals. Tests that need an embedding model are marked
  `embed`.
- Schema changes come with a revision bump and an entry in
  `docs/reference/schema.md`. Breaking changes also need a migration and a
  migration test.
