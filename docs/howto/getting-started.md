# Getting started

## Install

See [installing wckb](install.md).

## Basics of `kb` command 

The `kb` command is the entry point for a rich command tree.  Simply run the
command with no arguments to see the first level of help.  The top-level `kb`
command accepts top-level options and then a sub-command is given.  Each
sub-command may have its own options and potentially sub-sub-commands.  All
commands in the tree should accept `-h/--help` option and most, but not all,
will print the help if not given any argument.  These all print some help.

```
$ kb
$ kb init -h
$ kb add
```

Some top-level sub-commands are categorized as: 

- **Everyday verbs**, at the top: `kb init`, `kb status`, `kb add`,
  `kb new`, `kb show`, `kb open`, `kb ls`, `kb search`, `kb context`, `kb log`,
  `kb check`, `kb lint`, `kb doctor`, `kb config`, `kb push`, `kb pull`,
  `kb sync`.
- **One family per thing**, named by a word from the
  [glossary](../glossary.md): `kb item` (change, correct, withdraw, edit),
  `kb rendition`, `kb asset`, `kb source`, `kb kset`, `kb edge` (links to
  other stores), `kb bundle`, `kb inbox` (review), `kb view`, `kb data`,
  `kb policy`, `kb store`, and `kb exchange`, `kb forge`, `kb secret` for
  git hosting.

A family run without a subcommand lists its commands (`kb item`). When in
doubt, `kb status` says what needs attention and `kb doctor --fix` repairs
the common problems ([health](health.md)). Every
command has `--help`. The full reference is
[reference/cli.md](../reference/cli.md).

## What a store is

A store is one SQLite file, `kb.sqlite`, in a directory. Next to it, `.kb/`
holds this machine's settings (`local.toml`), a cache and a build area.

- **Settings begin in the store.** Manage them with `kb config show`,
  `kb config get` and `kb config set`. User and directory config files augment those settings. `kb config show` prints TOML that
  could serve as an exaustive config file (`--json` for JSON). `kb config show --blame` shows what is in
  effect and which file set each line ([contexts](contexts.md)).
- **The database enforces append-only.** You never edit it directly; every
  change goes through `kb`.
- **Each command uses its store context's store.** In a git working tree
  that is the tree's store, wherever it is kept; `kb init --scope project`
  there makes one in `~/.local/share/kb/contexts/`. `kb status` says which
  store, and why ([contexts](contexts.md)).
- **Stores are not git repositories.** Knowledge moves between stores as
  transfers and bundles ([stores-and-promotion](stores-and-promotion.md),
  [review](review.md)).

## Your stores

Most people end up with three kinds of store:

```
kb init ~/kb --scope home                                  # yours, private
cd ~/work/myproject && kb init kb --scope project --up ~/kb  # one per project, as needed
cd ~/kb && kb edge add tpc --relation up --path /data/kb/tpc   # the group's store, if it's on this machine
```

`kb` finds a store from wherever you are, by its store context
([contexts](contexts.md)). A store inside a git working tree gets its own
files (`kb.sqlite*`, `.kb/`, `content/`) added to the `.gitignore` there, and
nothing else, so the code repository never versions the database. Better
still, keep stores out of working trees: `kb init` in a git working tree
puts the store in `~/.local/share/kb/contexts/`.

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
[cas.default]
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
kb show obs-...       # as a person reads it
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
