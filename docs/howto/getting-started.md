# Getting started

## Install

```
uv tool install git+https://<host>/<group>/wckb.git   # or, from a checkout: uv tool install --editable .
kb --version
```

Optional features need extras:

```
uv tool install 'wckb[pdf,pptx,root] @ git+https://...'
```

| extra | adds |
|---|---|
| `pdf` | PyMuPDF |
| `pptx` | slide extraction |
| `root` | ROOT file summaries |

`kb` also uses `pdftotext` (poppler) and `pandoc` when they're installed.

## What a store is

A store is one SQLite file, `kb.sqlite`, in a directory. Next to it, `.kb/`
holds this machine's settings (`local.toml`), a cache and a build area.

- **Settings live in the store.** Manage them with `kb config show`,
  `kb config get` and `kb config set`.
- **The database enforces append-only.** You never edit it directly; every
  change goes through `kb`.
- **Stores are not git repositories.** Knowledge moves between stores as
  transfers and bundles ([stores-and-promotion](stores-and-promotion.md),
  [review](review.md)).

## Your stores

Most people end up with three kinds of store:

```
kb init ~/kb --scope home                                  # yours, private
cd ~/work/myproject && kb init kb --scope project --up ~/kb  # one per project, as needed
cd ~/kb && kb neighbor add tpc --relation up --path /data/kb/tpc   # the group's store, if it's on this machine
```

`kb` finds a store from wherever you are: `./kb.sqlite`, else `./kb/kb.sqlite`,
walking up the directory tree. A project store inside a git working tree
gets a `.gitignore` of `*`, so the code repository never versions the
database.

Tell `kb` who you are, and make your home store the parent of every project
store, in your user config:

```toml
# ~/.config/kb/config.toml
user = "per-yourname"

[neighbors.home]
relation = "up"
path = "~/kb"
```

Paths as seen from *this* machine go in the store's `.kb/local.toml`:

```toml
[stores.default]
path = "/eos/experiment/group/kb-cas"

[views]
publish_path = "/eos/experiment/group/www/kb-views"
```

Settings shared by everyone using the store go in the store itself:

```
kb config set links.indico https://indico.example.org
kb config set views.publish_url https://example.org/group/kb-views
```

## Create a group store

```
kb init /data/kb/tpc --scope group --name tpc
```

A group store has **one writer**: its maintainer, or a CI job. Members never
write it directly. They send bundles to its inbox, or pull requests to its
exchange repository ([review](review.md)), and they read a published
snapshot of it ([storage](storage.md)).

Never put a store's database on a network filesystem such as EOS or NFS:
`kb` refuses to write there.

`kb init` also selects and enacts the policy for the store's scope. That
writes `CLAUDE.md` and `.claude/` into the store's target directory: the
project directory for a `./kb` store ([policy](policy.md)).

## Your first item

```
kb new observation \
  --title "Elevated coherent noise in run 1234" \
  --summary "TPC planes showed elevated coherent noise during run 1234; cause unknown." \
  --subject detector/tpc/noise --runs 1234 \
  --by "A. Colleague" --how "verbal, collab meeting" --when 2026-10-28 \
  --text "Coherent noise was elevated on all planes during run 1234."
kb render obs-...       # as a person reads it
kb log obs-...          # its history
kb check
```

If a shared store lives on a forge, set up an API token so you and Claude can
handle pull requests from the command line ([git-and-forges](git-and-forges.md)):

```
kb forge add work --type forgejo --host git.example.org --token-from "cmd:pass show kb/forge/work"
kb forge auth work
```

Next: [working-with-claude](working-with-claude.md).
