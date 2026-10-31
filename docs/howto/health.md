# Status and doctor: keeping a store healthy

## At a glance: `kb status`

```
kb status            # the store, its items, inbox, edges, content stores, and what needs attention
kb status --json     # the same, for programs and LLMs
```

`kb status` is cheap: it counts and runs only the *quick* diagnoses of
`kb doctor`. Its "Needs attention" list is what `kb doctor --quick` would
report.

## Diagnose and fix: `kb doctor`

```
kb doctor                  # every diagnosis: what is wrong and how it would be fixed (exit 1 if anything)
kb doctor --fix            # apply the safe fixes; ask about the others
kb doctor --fix --yes      # ... without asking
kb doctor stale dangling   # only these diagnoses
kb doctor --list           # what kb doctor knows about
```

Each finding has a mode:

| mode | meaning | with `--fix` |
|---|---|---|
| `auto` | safe and mechanical (rebuild the search tables, re-extract text, enact policy, download a snapshot) | applied |
| `ask` | worth a moment's thought: it uses the network or tools, copies content, or changes files outside the store | applied after you confirm (or `--yes`) |
| `manual` | kb cannot fix it; the finding says what to do | shown as advice |

What `kb doctor` knows today (`kb doctor --list` is authoritative):

| code | problem | fix |
|---|---|---|
| `network-fs` | the store is on NFS, EOS, CIFS ...; writes are refused | move it to local disk, publish snapshots (advice) |
| `query-tables` | search tables out of step with the items | `kb store reindex` (auto) |
| `gitignore` | a store inside a git working tree is not ignored, or `kb.sqlite` is tracked | write `.gitignore` (auto); untrack (advice) |
| `kb-on-path` | `kb` is not on PATH, so hooks and LLM sessions can't run it | install it as a uv tool (advice) |
| `user` | items are attributed to your login name | set `user` in config.toml (advice) |
| `policy` | policy not enacted, outdated, or edited by hand | `kb policy enact` (auto, unless hand edits would be lost: then advice) |
| `cas` | a content store holding renditions has no path here, or isn't mounted | set its path in `.kb/local.toml` (advice) |
| `edges` | a neighbor can't be reached | `kb edge fetch` for snapshot neighbors (auto); otherwise advice |
| `secret-perms` | a forge token file is readable by others | `chmod 600` (ask) |
| `stale` | derived renditions, views or topics whose inputs changed | re-extract (auto); rebuild views (ask); revise topics (advice: an LLM's job) |
| `datalad-support` | DataLad sources in use, but DataLad not installed | install the extra (advice) |
| `gitattributes` | your git ignore rules ignore `.gitattributes`, which DataLad needs | remove the pattern (advice) |
| `embeddings` | items without embeddings for semantic search | `kb store embed` (ask) |
| `schema-version` | items written by an older kb | `kb store migrate` (ask) |
| `dangling` | references to items not in this store | `kb pull --dangling` (ask) |
| `clones` | DataLad datasets used here but not cloned on this machine | `kb source clone` (ask) |
| `local-only` | content only on this machine while a content store is mounted | `kb rendition locate` (ask) |
| `check` | errors from the consistency scan | advice; details from `kb check` |

The last five (and `check`) are *full* diagnoses: `kb doctor` runs them,
`kb status` doesn't.

## LLMs and the doctor

An LLM may run `kb doctor` and `kb status` freely. Applying a fix follows the
autonomy of the remedy's action (`curate` for most, `policy` for enacting
policy, `pull` for fetching references): `auto` lets it fix, `ask` needs
`--approved "<the human's words>"`, `never` leaves it to you.

## When you meet a new problem

`kb doctor` is meant to grow with use. If you hit a problem that kb could
have spotted, or fixed, write it down (`docs/questions.md`, or tell the kb
maintainers): a remedy is a diagnosis function and, optionally, a fix, in
`src/kb/doctor.py`.
