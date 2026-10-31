# Open questions

A living list of what still needs deciding. Each entry says what `kb` does
*today* (the default we live with until decided) and what is open. When a
question is settled, record the decision and move it to "Decided" at the
end, rather than deleting it.

Status:

- **open**: not discussed yet;
- **exploring**: being tried or discussed;
- **leaning**: a preferred answer, still to be confirmed;
- **decided**.

## S. The knowledge store (see decisions/0001, accepted)

**S1. Where does a project's database live?** *(leaning)*
- Today: `<project>/kb/kb.sqlite`, with a `.gitignore` of `*` so the
  project's repository doesn't version it. Knowledge reaches collaborators
  by bundles.
- Open: should a JSONL export be committed with the code, for projects
  whose collaborators don't use kb?

**S2. Who writes a group store?** *(open)*
- A maintainer, a CI job applying accepted bundles, or a small service? How
  is the read-only snapshot published (EOS, web)?

**S3. Signed bundles?** *(open)*
- minisign or ssh signatures, so an inbox can verify the sender.

**S5. `datalad run` vs our provenance.** *(open; DataLad sources are done, see decisions/0003)*
- Can it replace view manifests and transform provenance, or should it
  complement them?

**S6. Growth.** *(open)*
- Blobs and events are never deleted (by design). At what size does a store
  need compaction: archiving old events, moving large blobs to a content
  store? `kb check --deep` cost grows with blob volume.

**S7. Snapshot publishing.** *(open)*
- Who publishes a group store's read-only snapshot (`kb store snapshot`), where
  (EOS, a web server), and how often (on every accepted bundle, nightly)?

## V. Files, versions and sources (see decisions/0002)

**V1. Does Indico version attachments?** *(open; to verify)*
- Believed: Indico keeps earlier files of an attachment internally when one
  is replaced, but neither the UI nor the HTTP API exposes them.
- To check against our Indico instance and its source. If the history is
  reachable, an `indico` source could offer `native` versions; if not, it
  is `none`, and `keep = auto` is what preserves history.
- Also: is a "material" a folder (a file set) or one attachment (a file)?

**V2. DocDB access.** *(open)*
- DocDB documents have versions, each with its own file set
  (`ShowDocument?docid=N&version=V`). Which interface is reachable from our
  machines (XML export, HTML scraping), and with what authentication (kb
  secrets)?
- Versioning there would be `native`, with labels the DocDB version
  numbers.

**V3. What is "latest"?** *(exploring)*
- Today: the last version *registered*. With labels from several sources
  (DocDB 7, a directory `2026-10-28`, Indico observed later), registration
  order may not be the real order.
- Options: an explicit order field; dates per version (`date`); per-asset
  ordering rules.

**V4. More generous matching.** *(open)*
- Today: content only (exact paths or content multiset).
- Some sources can list names and sizes cheaply, but content only by
  download (Indico, DocDB, large remote files). Should a scan accept
  "probably the same" (names and sizes match) as a provisional sighting,
  confirmed by hash later?

**V5. Partial file sets.** *(open)*
- A source may hold only some files of a version (Indico has the PDF; the
  directory also has the PPTX source).
- Should resolution assemble a version from several sources file by file
  (every file verified against the manifest)? That is natural with
  manifests, but the procedure gets more complex.

**V6. Hashing cost.** *(exploring)*
- Today: a per-user cache keyed by (path, size, mtime) means each file is
  hashed once.
- Large trees on EOS still mean a first full read. DataLad's annex keys
  carry hashes for free when the dataset uses `SHA256E` keys (DataLad's
  default is `MD5E`: then kb fetches and hashes once); for plain trees, should kb trust size and mtime for
  "known" status between full checks?

**V7. Label collisions across sources.** *(open)*
- Two versioned sources may both call different content `v2`. Today the
  first registered wins; the other is reported as a conflict.
- Should bindings namespace their labels (`docdb:7`), with explicit
  aliases?

**V8. Citing one file of a set.** *(leaning)*
- Today: a rendition location names an asset, a version and a path within
  the set. Should observations and topics be able to cite an asset version
  directly as evidence (`as-…@v2`), rather than going through a document
  item?

**V9. DataLad mapping.** *(partly decided: decisions/0003)*
- Done: tags are native versions; files and annex keys are listed from git
  without fetching; many assets may bind to one dataset (each binding
  names a path).
- Open: commits or branch heads as versions too? Subdatasets (a binding
  path crossing into a subdataset is not followed yet)? Should kb offer
  to make datasets use `SHA256E` keys so hashes are free?

**V10. Kept content.** *(open)*
- `keep = auto` copies versions from unversioned sources into the store (or
  its content store). Over time that is a store of old slides. Is there a
  retention rule (keep only versions some item cites)? Blobs are never
  deleted today (S6).

**V11. Version granularity.** *(open)*
- A version is the whole file set. DocDB and Indico change individual
  files. Is a per-file history inside a set (file-level versions) worth
  having, or is set-level enough, with manifests showing what changed?

## A. Group setup and infrastructure

**A1. Where do the content store (cas) and the publish store live?** *(open)*
- Today: content stores (`cas`) are paths in the store settings or `.kb/local.toml`; snapshots and publications are anywhere.
- Options: EOS, a group NFS area, or S3 via rclone.
- Affects CI (it must mount them) and laptops.

**A2. Does CI get an SSO service account?** *(open)*
- Indico and the elog sit behind SSO. Without an account, `kb rendition verify` and
  nightly world builds that fetch URLs only work on members' machines.

**A3. Person registry, or free-text names?** *(open)*
- Today: `created.by` and `asserted_by.who` accept a `per-…` ref or any
  string, and nothing checks them.
- A registry gives stable attribution across stores but needs upkeep.
- Could person items be pushed up from home stores, so each person
  maintains their own?

**A4. Who approves topic revisions?** *(open)*
- Today: any merge reviewer.
- Alternative: topics name an owner whose approval is needed, enforced by
  CODEOWNERS rather than `kb`.

## B. Federation

**B2. Should a project store be its own repository or part of the project's?** *(open)*
- Today: the store is kept out of the project's repository (`.gitignore` of `*`); see S1.
- Inside the project's repo, knowledge is versioned with the code and
  shared with the code's collaborators. A separate (or ignored) `kb/` keeps
  private notes out of a public code repository.
- Is `access` enough to guard a public repo, or should `kb init` refuse
  `private` inside a repo with a public remote?

**B3. Should the same ID be kept across stores?** *(leaning: yes)*
- Today: copies keep their ID, and `origin` records the sender, the path of
  stores and the set. `origin` is excluded from the digest.
- Arrival along several paths is reconciled (B15).

**B4. What should happen upstream when a pushed item is superseded at the source?** *(open)*
- Today: the correction becomes a top candidate ("corrects X, which was
  pushed"). Pushing it supersedes X in the parent too, provided X is there.
- Open: should this be automatic under `auto` autonomy? Should the parent
  be told when the source *retracts* without pushing?

**B5. Pulling down.** *(exploring)*
- Today: `kb pull` exists, and an up edge pulls `all` by default, so
  `kb pull --from home` in a project copies all of home that is new or
  changed. That follows "downward flow is accepted", but it may be heavy for
  project stores. Should `kb init --scope project --up` set `pull = none`
  (read live with `--reach` instead), or `select`?

**B7. Access relabeling on push.** *(exploring)*
- Today: an item more restricted than the target is refused, unless
  `--access` (a human decision) or a neighbor's `relabel` setting allows it.
- Should relabeling need a second confirmation, or a recorded reason?
  Should `private` ever be relabeled by an agent, even with `ask` approval?

**B8. Which neighbors get searched by default?** *(open)*
- Today: only the local store, unless `--reach` or `--in` is given. The
  `kb-routine` instructions tell Claude to widen the reach when the local
  store is thin.
- Should each store set a default reach? How should results from different
  stores be weighted?

**B9. How are neighbors discovered?** *(open)*
- Today: declared explicitly, with user-wide neighbors in `config.toml`.
- Should `kb` offer group stores from a registry (a well-known group store
  listing its member and child stores)?

**B10. Garbage and divergence.** *(open)*
- Pushed copies in a parent can be curated, superseded or retracted there.
  Should the source learn about that, for example a `kb edge news`
  that reads what happened upstream to records it pushed?

**B11. Did we read "a down edge accepts all" the right way?** *(open)*
- There are two readings:
  - (a) knowledge flowing *downward* (broad to narrow) is accepted
    wholesale, so a down edge pushes `all` and an up edge pulls `all`;
  - (b) the broader store *accepts* all that a narrower one sends, so the
    selection happens only at the sender.
- Implemented: (a) for the push/pull forms, plus `accept = direct` on
  up/down edges, which also gives (b): what a narrower store pushes lands
  without review unless the edge says `land = inbox|pr`.
- To confirm with the group.

**B12. Peer defaults.** *(open)*
- Today: `select` both ways, with pulled items landing in the inbox for review.
  A deliberately conservative placeholder.

**B13. Believability beyond `prefer`.** *(open)*
- An up store can be "more or less believable". Today that's expressed as
  `pull` (all, select, none), `accept` (direct, review) and `prefer` (ours,
  theirs).
- Should an edge also *downgrade* what arrives, e.g. land items from a less
  trusted store as `unverified` whatever their status? That would be a
  mutable-field rewrite on arrival.

**B14. Conflicts.** *(exploring)*
- Today: any conflicting item refuses the whole transfer (atomic), with the
  reasons reported.
- Wanted: a resolution workflow. Options include `--skip-conflicts`, a
  quarantine area (`.kb/conflicts/`), or `kb conflicts` showing both sides
  side by side, and recording the resolution as an item.

**B15. Reconciling mutable fields without clocks.** *(exploring)*
- Today: lists are united. Single values take the incoming value only from
  the same sender as before, or when the edge prefers theirs; otherwise
  ours is kept and the difference noted.
- There is no notion of "newer". A per-item revision counter bumped by
  `kb item set` would give a partial order, but concurrent edits remain genuine
  conflicts.

**B16. Knowledge-set proliferation.** *(open)*
- Every push or pull makes a set (`subject kb/transfer`, `purpose
  transfer`), stored in the sender, or in the receiver for pulls. They're
  hidden from candidates but listed by `kb ls`.
- Should `kb ls` hide them by default, or should transfer sets live in a
  separate area?

**B17. Deep transfers across stores.** *(open)*
- A deep expansion stops at records the sender doesn't have, which are then
  reported as dangling at the receiver. Should one transfer be able to pull
  missing pieces from third stores (a multi-source deep transfer), or is
  `kb pull` afterwards enough?

**B18. Scale of "all".** *(open)*
- `all` compares every current item of both stores. That's fine for
  thousands of items. A digest index in the ledger would make it
  incremental.

**B19. Twins.** *(exploring)*
- Today: twins are copies of one store (same id). `kb sync` moves new and
  changed items both ways, and the later change wins per item.
- Open: is per-item last-writer-wins enough, or do we need field-level
  merges with real clocks (B15)? And how do twins on machines that can't
  see each other meet: through a snapshot plus bundles, or an exchange
  repository?

## C. Policy

**C1. Who writes policy, and how is it reviewed?** *(open)*
- Today: anyone can draft. Releasing needs a human, or `policy` autonomy
  set to `auto`. Released versions are frozen, and group policies change
  through reviewed bundles (inbox or pull request) into the group store.
- Open: a policy owner role? Required reviewers for policies in a group
  store?

**C2. How should policies be evaluated?** *(open)*
- We have no way to tell whether a policy change made Claude behave better.
- Ideas:
  - a small set of scripted scenarios (prompts plus a fixture store) run
    against two policy versions, comparing the items filed, the
    candidates proposed and the tokens used;
  - `claude plugin eval` may fit.

**C3. Settings-type policies.** *(open)*
- Reserved in the schema. Should autonomy presets ("cautious", "trusting")
  be named, versioned policies that a store selects, instead of hand-edited
  `[autonomy]`?

**C4. Policy precedence and conflicts.** *(exploring)*
- Today: the nearest source wins per name: here, then reachable stores,
  then builtin.
- Open: should a group be able to *require* a policy in member stores
  ("must use group-review@≥2")? How should conflicting instructions from
  two up-neighbors be handled?

**C5. Enacting into `~/.claude`.** *(open)*
- Today: `--target user` writes the user-wide CLAUDE.md, skills and hooks.
  That affects every Claude session.
- Should the home store enact there by default? How should it combine with
  project-level enactment (both blocks get loaded, so duplication costs
  context)?

**C6. Distribution as a Claude Code plugin.** *(open)*
- Today: kb writes files directly.
- A plugin marketplace would give updates through Claude Code itself, but
  splits versioning between two systems.

**C7. Policy text quality and size.** *(exploring)*
- Today the enacted block is about 120 lines with the defaults. How much
  can be moved into skills, which cost nothing until used, without losing
  the "as a matter of course" behavior?

**C8. Should git hooks be policy elements?** *(open)*
- Agent hooks are policy (named, versioned, enacted). Git hooks now guard
  exchange repositories and are installed by `kb exchange hooks` or
  `kb exchange init`. They are not versioned with policy. Unify?

## D. LLM behavior and autonomy

**D1. How should routine consult-and-file be triggered?** *(exploring)*
- Today there are three layers:
  - `kb-routine` instructions;
  - the recall hook (keyword search on every prompt; prints at most five
    lines, only when at least two keywords match);
  - the session hook.
- Open: is keyword recall precise enough, or should it use embeddings
  (slower; needs the model running)? Should a Stop hook nudge capture at
  the end of a task, or would that nag?

**D2. Capture granularity and noise.** *(open)*
- With `capture = auto`, how much is too much? Ideas:
  - a per-session cap;
  - a `session` tag plus a periodic digest;
  - letting lint flag low-value items (short summary, no subject, never
    consulted).

**D3. When should Claude start a promotion review itself?** *(open)*
- Today: `review = auto` lets it start one "at a natural milestone", a
  phrase the instructions leave to the model's judgment.
- Should `kb` track signals ("12 new items since the last review", "a
  pushed item was corrected") and surface them in the session hook,
  instead of relying on judgment?

**D4. Is agent detection by environment enough?** *(leaning: yes, as a guardrail)*
- `CLAUDECODE`, `AI_AGENT` and `KB_AGENT` are trusted. A model could unset
  them, so this is not security. Is the `--approved` audit trail sufficient
  for the group?

**D5. Graduated autonomy.** *(open)*
- Should autonomy be per kind or per subject ("auto-push observations and
  configs, ask for topics"), or tied to record status ("auto-push
  verified")?

## E. Items and schema

**E1. Should hearsay become a status?** *(open)*
- Today `unverified` covers both "nobody checked" and "I heard it".

**E2. Topics across scopes.** *(open)*
- A home-level topic synthesizing several projects cites records that live
  in project stores. Since B6, references needn't resolve locally. Is a
  shallow transfer plus `kb pull` on demand the right pattern, or should
  such topics travel deep?

**E3. Making `kb` recognize that an item is used.** *(open)*
- Recording which records a session consulted, for ranking candidates and
  finding dead weight. This has privacy and effort costs.

## F. Tooling

**F1. An MCP server.** *(open)*
- Read-only `ls`, `search` and `context` (federated) and `candidates`. It
  would spare start-up cost and give structured results.

**F2. Should semantic recall run in the recall hook?** *(open)*
- See D1. It needs a running embedding service and must answer within the
  hook's timeout.

**F3. What should `kb doctor` learn next?** *(open; grows with user testing)*
- Problems seen in testing go here first, then become remedies in
  `doctor.py` ([howto/health](howto/health.md)).
- Already noticed: `kb item set ID status=verified` is accepted without
  evidence; only `kb check` (and so `kb doctor`) objects afterwards.
  Should `set` refuse it, as `new` does? Should doctor offer to set such
  items back to `unverified`?
- Should the SessionStart hook report the quick doctor findings, so an LLM
  can offer `kb doctor --fix` at the start of a session?
- Should a broken diagnosis (an exception in a remedy) be reported more
  loudly than as a finding?

## G. Git, forges and secrets

**G2. API tokens vs git credentials.** *(open)*
- Pull requests use an API token, while `git push` uses SSH keys or a
  credential helper. Should `kb` offer to configure a git credential helper
  from the same secret spec, for HTTPS remotes?

**G3. Gogs.** *(open)*
- No pull-request API. Is anyone on Gogs? If so, `land = pr` would push the
  branch and print the web URL for opening the pull request by hand.

**G4. More secret sources.** *(open)*
- Command specs cover most stores: `pass`, `gopass`, `secret-tool` and
  `op read` all print the secret. Native resolvers (keyring, Vault) plug into
  `secrets.RESOLVERS` when needed.

**G5. Secrets in CI.** *(open)*
- CI gets tokens from its own secret variables (the `env:` spec). Document a
  minimal-scope bot token per store.

**G6. Merge locally or through the API?** *(open)*
- `kb forge pr merge` merges on the forge. For stores without a forge, a
  accepting from the inbox (B20) is the equivalent.

## R. Rendering and review (see rendering.md)

**R1. Web front end.** *(open)*
- Flask (simple, ubiquitous) or FastAPI plus htmx? Static HTML export first
  (reviewable by email) or a server first?

**R2. Authentication for web review.** *(open)*
- A reverse proxy with the lab's SSO in front of a small kb server. Who
  hosts it?

**R3. Thumbnails and figures.** *(open)*
- Slide thumbnails as derived renditions (`pdftoppm`). How many pages, and
  what resolution? Kept in the store (small) or the content store?

**R4. What does each kind need in a render?** *(open; needs the group's input)*
- [rendering.md](rendering.md) proposes a table per kind. Which of it
  matters for real review decisions?

**R5. Structured LLM reviews.** *(open)*
- Beyond prose: per-item verdicts and suspected duplicates as JSON, for a
  web review page to display and sort.

**R6. TUI.** *(open)*
- Textual-based tree and item panes. Worth it alongside a web view?

## DS. Data sources (see decisions/0001 §13)

**DS0. Two source interfaces.** *(exploring)*
- Rendition locations (`kb.locations`: db, cas, url, local, asset, datalad)
  resolve one file by hash.
- Asset sources (`kb.assets`: kb, fs, url, datalad; planned indico, docdb)
  enumerate versions of files and file sets.
- DataLad is both (decisions/0003): a `datalad` location for one committed
  file (by annex key), an asset source for versioned material. Should
  other new sources be asset sources only, reached through the `asset`
  location?

**DS1. DataLad as a source.** *(decided: decisions/0003; follow-ups open)*
- Decided: source items, clones by dataset ID, source directories,
  `datalad` locations and asset source, optional extra.
- Open:
  - `datalad run` vs our provenance (S5);
  - EOS through rclone or WebDAV special remotes;
  - Indico through datalad-next credentials (DS2);
  - adding a file whose annex content is not present locally (today kb
    needs the content to hash it);
  - dropping content (`datalad drop`) that no item here needs.

**DS2. Indico.** *(open)*
- A native `indico` source (Indico's API with a token), or URLs through
  DataLad? Either way the token comes from `kb.secrets`.

**DS3. Credentials for sources.** *(leaning)*
- Reuse `kb.secrets` specs per source, as forges do (for example
  `[sources.indico] token = {command = "pass show", id = "..."}` in the
  user config).

**DS5. Source items for every source.** *(partly done)*
- DataLad datasets and named filesystem trees (`--type filesystem`) are
  source items. Indico and DocDB servers next, the same way?
- Should a file in a filesystem source get its own location type
  (`{source, path}`), rather than `local` (one host) or an asset? And a
  file committed in a git source (`{source, path, commit}`), as DataLad
  files have `datalad` locations?
- git sources are done: identity by first commit, clones, asset source.
- Should a filesystem source be guessable in some cases (a directory with a
  marker file), or always need `--type filesystem`?

**DS6. One clone, several users.** *(open)*
- A dataset on a shared filesystem (a `paths` entry for a host) is used
  in place. Getting content into it needs write access; should kb then
  clone from it into the user's source directory instead?

**DS4. Third-party sources.** *(open)*
- Register `Source` classes through Python entry points
  (groups `kb.locations` and `kb.asset_sources`), so groups can add their own without changing kb.

## Decided

**S4. DataLad dataset granularity.** *(decided 2026-10-31, decisions/0003)*
- Zero or more DataLad datasets per store, each described by a source
  item (remote URL, local tree, or both). Knowledge sets carry the source
  items their items need; receivers clone on demand into a configured
  directory or ask where, and reuse a clone they already have (matched by
  dataset ID).

**S0. git + JSON, or a database, for knowledge items?** *(decided 2026-10-31)*
- A SQLite store per store, with bundles for transfer and review and git
  only as transport (exchange repositories). See decisions/0001 §13.

**B20. Reviewing pulled knowledge.** *(resolved 2026-10-31)*
- Pulled items with `accept = review` go into the inbox: `kb inbox show`,
  `kb inbox review`, `kb inbox accept` or `kb inbox reject`. No branches.

**G1, G7.** *(obsolete 2026-10-31)*
- Both were about stores as git repositories (pre-commit on the index, a
  globally ignored `.gitattributes`). Stores are no longer git
  repositories. Git hooks now guard exchange repositories.

**B1. Home store per person or per account?** *(decided 2026-10-31)*
- Scope labels (`home`, `project`, ...) are descriptive and only pick
  defaults at `kb init`. Relationships are the edges.
- Either keep one store per account, synced as `twin`s (`kb sync`)
  (`kb sync`), or keep a separate store per account, linked as `peer`s with
  copy transfers. Both are supported. What a `peer` edge should do by
  default is B12.

**B6. Cross-store references.** *(decided 2026-10-31)*
- Items can always reference other items, store-independently, by
  global ID.
- Obtaining a referenced item is a separate step, as with a content-addressed
  store: `kb pull` fetches from reachable stores, and transfers are
  shallow or deep through knowledge sets.
- `kb check` reports unresolved references as `dangling` warnings.
- Follow-ups: B14, B15 and B17.
