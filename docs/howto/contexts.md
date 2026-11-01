# Store contexts, config files and logs

## Which store a command uses

Every `kb` command works on the store of its **store context**. A store
context is a short name, such as `home-ann-ana` or `tpc`, and it can label a
table in a config file.

The name is the first of these:

1. `--context NAME` on the command line;
2. `$KB_CONTEXT`;
3. `kb_context = "NAME"` in the config files (see below);
4. `git-directory`.

Two names are not used as they are. kb makes a name from a directory instead:

- `current-directory`: the working directory's path, with slashes made
  hyphens (`/home/ann/ana` gives `home-ann-ana`);
- `git-directory`: the same, for the nearest directory, here or above, that
  holds a `.git`. This is the default, so inside a git working tree the
  context is that tree's.

The store's directory (its *location*) is the first of these:

1. `-C DIRECTORY` or `$KB_ROOT`: that directory, whatever the context. Unless
   a context was named, the context is named after the directory.
2. `context.NAME.location` in the config files. A relative path is relative
   to the directory the config file is for.
3. The nearest `kb.sqlite`, `.kb/kb.sqlite` or `kb/kb.sqlite`, in the working
   directory or above.
4. `~/.local/share/kb/contexts/NAME` (`$XDG_DATA_HOME/kb/contexts/NAME`).

Outside any git working tree, with no name given, kb uses a store found
here or above and names the context after its directory. With no store
either, kb stops and says how to name a context.

`kb status` shows the context, how it was named and how its store was found:

```
context   home-ann-ana (named by the default; store: the default for the context)
config    /home/ann/.config/kb/config.toml
```

Add `--debug` to any command to see each step.

### A store for a git working tree, kept outside it

```
cd ~/ana
kb init --scope project
```

With no `PATH`, `kb init` makes the context's store. Here that is
`~/.local/share/kb/contexts/home-ann-ana`, unless a location is configured.
The store's policy (`CLAUDE.md`, `.claude/`) is enacted in the working tree
`~/ana`. Every `kb` command run anywhere in `~/ana` then uses that store.

### A store somewhere else

```toml
# ~/ana/.kb.toml (or ~/.config/kb/config.toml)
[context.home-ann-ana]
location = "/data/kb/ana"
```

### A context by name

```toml
# ~/.config/kb/config.toml
[context.tpc]
location = "/data/kb/tpc"
```

```
kb --context tpc status
KB_CONTEXT=tpc kb ls
```

## Config files

kb applies these files in order, each adding to or overriding the ones
before:

1. `~/.config/kb/config.toml` (`$XDG_CONFIG_HOME/kb/config.toml`);
2. in the working directory and each directory above it: `.kb/config.toml`,
   `.kb.toml` and `kb.toml`. The farthest is applied first, so the nearest
   wins.

`--no-init-file` skips them all. `-c FILE` (`--config FILE`) applies `FILE`
after the others, even with `--no-init-file`.

They lay over the store's own settings. The store's `.kb/local.toml` (this
machine's paths for that store) and `KB_*` environment variables lay over
them in turn.

## Logs

kb logs to a file per day, `~/.cache/kb/logs/kb-YYYY-MM-DD.log`, appending,
from level `warning`. Each record has its time, its level, the store
context's name and the part of kb that wrote it:

```
2026-11-01 08:30:37,892 WARNING [home-ann-ana] kb.cli: ...: opened read-only instead
```

| option | does |
|---|---|
| `-l LEVEL`, `--log-level LEVEL` | `debug`, `info`, `warning` (the default), `error` or `critical` |
| `-L SINK`, `--log-sink SINK` | a file (appended to), `stdout` or `stderr` |
| `--debug` | `-l debug -L stdout` |

The levels mean:

- `debug`: what went right but helps explain a surprise (which config files
  applied, which store was opened);
- `info`: a success (an item stored, a transfer, a fix);
- `warning`: something unusual that kb got over;
- `error`: more about a failure that kb reports.

A command's output, the data you might pipe on, goes to stdout and is never
logged. kb's messages to you on stderr (`stored observation ...`, warnings)
are logged too. When the log already goes to the terminal at their level,
you see them only once.
