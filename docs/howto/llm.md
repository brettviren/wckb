# Asking the store, and planning changes, with an LLM

`kb llm` has kb call an LLM itself, through simonw's
[llm](https://llm.datasette.io) package: any model llm can reach (OpenAI,
Claude by llm-anthropic, an OpenAI-compatible server such as llama.cpp or
vLLM, other llm plugins). It is optional: install kb with the `llm` extra
(see [install](install.md)).

```
kb llm ask "why was run 1234 noisy?"          # an answer from the store, with item IDs
kb llm do "record that the noise in 1234 came from the HV supply" -o plan.sh
kb llm settings --check                       # which model, which key, from where
```

`kb llm ask` and `kb llm do` don't let the model run `kb`. The model reads
the store only through a few functions that read, and the store is opened
read-only.

## Choosing a model and giving it a key

Settings come from three places, and the last one that sets a value wins:

1. the `[llm]` table of your configuration (`~/.config/kb/config.toml`, a
   project's `.kb/config.toml`, `.kb/local.toml`);
2. the environment;
3. options of `kb llm`, given before `ask` or `do`.

```toml
[llm]
model = "anthropic/claude-sonnet-4-5"     # any name `llm models` lists
password_command = "pass show"            # the default, when password_name is given
password_name = "kb/llm/anthropic"        # so the key is: pass show kb/llm/anthropic
# key_from = "file:~/.config/kb/anthropic.key"   # or any secret spec instead (cmd:, file:, env:)
# access = "group"                        # keep private items away from this model
# chain_limit = 12                        # rounds of tool calls (default: 12 for ask, 30 for do)
# presearch_chars = 8000                  # how much the first search may give the model

[llm.env]                                 # set for llm and its plugins, unless already set
LLM_USER_PATH = "~/.config/io.datasette.llm"

[llm.options]                             # model options (llm models --options)
temperature = 0.2
```

For a model behind an OpenAI-compatible server that llm doesn't list:

```toml
[llm]
model = "qwen3-coder"
base_url = "http://localhost:8080/v1"
# provider = "anthropic"                  # if the server speaks Anthropic's API instead
```

| setting | config (`[llm]`) | environment | option |
|---|---|---|---|
| model | `model` | `KB_LLM_MODEL`, `LLM_MODEL` | `-m`, `--model` |
| API of an unknown model | `provider` | `KB_LLM_PROVIDER` | `--provider` |
| base URL | `base_url` | `KB_LLM_BASE_URL`, `OPENAI_BASE_URL` (`OPENAI_API_BASE`), `ANTHROPIC_BASE_URL` | `--base-url` |
| key, as a secret spec | `key_from` | `KB_LLM_KEY_FROM`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, a plugin's key variable | `--key-from` |
| key, by a password command | `password_command`, `password_name` | `KB_LLM_PASSWORD_COMMAND`, `KB_LLM_PASSWORD_NAME` | `--password-command`, `--password-name` |
| items the model may see | `access` | `KB_LLM_ACCESS` | `--access` |
| other variables | `[llm.env]` | as set | `--env NAME=VALUE` |
| model options | `[llm.options]` | | `-o KEY=VALUE` |

The provider's variables (`OPENAI_*` for OpenAI models and compatible
servers, `ANTHROPIC_*` for Claude) count only for a model of that
provider. With no key given anywhere, llm's own keys (`llm keys set`) are
used. Other variables llm and its plugins read (`LLM_USER_PATH`,
`LLM_LOAD_PLUGINS`, `GEMINI_API_KEY` ...) pass through as they are.

`kb llm settings` shows each setting and where it came from. It describes
where the key comes from but doesn't fetch it, unless you give `--check`,
and even then it shows the key masked. Keep keys out of the store's own
settings (`kb config set`): the store is shared, your keys are not.

**What the model sees.** The model sees the items it searches for and
reads, which may include private ones. Set `llm.access = "group"` (or
`--access`) to keep more restricted items away from a model run by a cloud
service. A local server can see everything.

## `kb llm ask`

```
kb llm ask "what do we know about TPC grounding?"
kb llm ask --reach 1 "who measured the gain in 2026?"   # neighbor stores too
echo "long question ..." | kb llm ask -
kb llm ask -v ...            # show each tool call on stderr
kb llm ask --json ...        # answer, model, cited IDs, cited IDs not in the store
```

How it works:

1. kb searches the store for the question.
2. The model gets kb's instructions, then what the search found (topics
   with their text, other items with the words they matched), then the
   question.
3. If the model can call tools, it may then read on: `search` with other
   words, `show` an item, `read` its text page by page, follow its `links`,
   `list_items` by kind, subject or tag, and list `subjects`.
4. The answer cites item IDs. After the answer, kb lists any cited ID that
   the store doesn't have.

`--no-tools` gives the model only the first search (one call, cheapest).
`--no-search` leaves all the finding to the model's tools. Nothing is
recorded, and the store is opened read-only (SQLite `mode=ro`).

## `kb llm do`

```
kb llm do "file an observation that run 1250 had the HV trip, from the elog" -o plan.sh
less plan.sh && bash plan.sh
kb llm do --json ...         # the steps, why, which are for review, what was rejected
```

The model reads the store and finds commands (`find_commands`,
`command_help`). It adds one step at a time with `add_step`, and kb checks
each step as it comes, telling the model what is wrong so it can fix the
step. **Nothing is run.** The result is a script for you to read and run,
or to copy lines from:

```bash
#!/usr/bin/env bash
# kb llm do: record that the noise is not coherent
# Proposed by anthropic/claude-sonnet-4-5 for the store wckb on 2026-11-14 10:02. Nothing has been run:
# ...
set -euo pipefail

# First, a copy of the store as it is now, to go back to:
kb store snapshot "kb-before-llm-do-$(date +%Y%m%d-%H%M%S).sqlite"

# 1. record the finding
NEW=$(kb new observation --title 'Noise isn'"'"'t coherent' --summary '...' --by claude --how '...' --text '...')

# 2. relate them
kb item link "${NEW}" contradicts obs-9hzyva117b

# 3. REVIEW (withdraws an item): the old claim is wrong
# kb item retract obs-9hzyva117b --reason superseded

kb check
```

**Guardrails** (`kb.proposals`):

- **Words, not shell text.** A step is a list of words, and kb quotes each
  one when it writes the script, so nothing the model writes can become a
  shell command. The model's explanations become comment lines. `$NAME`
  is allowed only for the ID printed by an earlier `kb new`, `kb add` or
  `kb item supersede` step that captured it.
- **Only kb, and datalad to get data** (`clone`, `get`, `install`,
  `status`, `siblings`, `subdatasets`).
- **Real commands, real IDs.** The command must exist and its options must
  parse, just as when it runs. Every item ID it names must exist, here or
  in a store it reaches. Options before the command, `--help` and stdin
  are refused.
- **Never proposed:** interactive commands (`item edit`, `open`, artifact
  workspaces), policy changes, secrets and tokens, `init`, `kb llm` itself,
  `--approved` (the approval is yours, given by running the step) and
  `--relabel`.
- **Left for you to decide** (commented out, marked REVIEW): retracting,
  accepting or rejecting inbox entries, push, pull and sync, edges,
  settings, bundles applied, migrations, forge actions, publishing,
  `--remove`, `--force`, `--replace-ref`, `--drop`, `--access`,
  `doctor --fix`, clearing a field (`key=`), replacing an item's text.
- **A way back:** a script that may change anything first takes a
  snapshot of the store (`kb store snapshot`, a consistent single-file
  copy), and it ends with `kb check`.

A model that can't call tools can't plan. Use `ask` with it instead.
