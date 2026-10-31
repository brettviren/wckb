# Policy: what the LLM is told, as named versions

Everything that shapes how Claude uses a store is a **policy element** with
a name and a version:

| type | example | becomes |
|---|---|---|
| `instructions` | `kb-core`, `kb-routine`, `kb-federation` | a section of the generated block in `CLAUDE.md` |
| `skill` | `kb-capture`, `kb-promote`, `kb-review` | `.claude/skills/NAME/SKILL.md` |
| `hook` | `kb-check-hook`, `kb-recall-hook`, `kb-session-hook` | an entry in `.claude/settings.json` |

## See what you have

```
kb policy list          # * marks selected; source: here, a neighbor, or builtin
kb policy show kb-routine
kb policy status        # anything needing attention
```

## Select and enact

The store's settings hold the selection (`kb config get policy`). `kb init` writes defaults for the store's
scope.

```
kb policy use kb-view                # follow the latest release
kb policy use group-review@2         # pin a version
kb policy drop kb-recall-hook
kb policy enact --dry-run            # show the diff
kb policy enact
```

`enact` writes:

- a block in `CLAUDE.md` between `<!-- kb:policy:begin … -->` and
  `<!-- kb:policy:end -->`. Everything outside the block is yours and is
  never touched;
- the selected skills;
- the selected hooks. Hooks not run by `kb` are left alone.

The block starts with a generated section: where the store is, its
neighbors, and the autonomy table.

Where it writes depends on the setting `policy.target`:

- `.`: the store itself;
- `..`: the project directory around a `./kb` store;
- `user`: `~/.claude`, which affects every session.

The store setting `policy_lock` records exactly what was written.

## Improve a policy from daily use

You, or Claude at your request, will tweak the enacted text. That's
expected. `kb policy status` notices it:

```
kb-routine           edited in CLAUDE.md since enacted
                     fix: kb policy capture kb-routine (keep the edit) or kb policy enact (discard)
```

```
kb policy capture kb-routine          # the edit becomes kb-routine@2, a draft in this store
kb policy diff kb-routine@draft --against 1
kb policy release kb-routine          # freeze it
kb policy enact
```

A builtin or a neighbor's policy, once revised here, becomes a local fork
(`based_on: builtin:kb-routine@1`) that takes precedence in this store.

## Write a new policy

```
kb policy new group-review --type instructions --file review.md --order 40 \
   --summary "How the group reviews incoming bundles."
kb policy edit group-review --file review.md     # while it is a draft
kb policy use group-review@draft && kb policy enact   # try it out
kb policy release group-review
```

Hooks are JSON, and their command must be a `kb hook …` command:

```json
{"event": "UserPromptSubmit", "command": "kb hook recall", "timeout": 10}
```

## Share policy

Policies are items, held in the store with their text:

- A group publishes policies in its store. They reach it like any
  knowledge: a bundle reviewed in its inbox, or a pull request to its
  exchange repository. Members adopt them by name. Resolution searches this
  store, then reachable `up` and `peer` stores, then the builtins shipped
  with `kb`.
- A policy improved in a project store can be promoted with `kb push` like
  any item.

Released versions are frozen (the store refuses changes to their name,
version, type or text), so `name@N` means the same text everywhere.

## Who may change policy

- **Autonomy** (the store setting `autonomy`) is set by a person:
  `kb policy autonomy capture=auto push.home=ask accept=ask`. Agents are
  refused. The actions are `capture`, `review`, `curate`, `policy`, `pull`,
  `accept` (bundles from the inbox), `merge` (forge pull requests) and
  `push` (per neighbor).
- **Selection, release and deprecation** follow the `policy` autonomy
  level, which is `ask` by default: Claude needs your words in
  `--approved`.
- **Drafts and enactment** are mechanical and always allowed.
