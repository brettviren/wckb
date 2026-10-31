# Exchange repositories, git hooks, forges and secrets

Stores are SQLite files, not git repositories. Git still has a job: it
carries **bundles**, the immutable files that move knowledge between stores
([review](review.md)), and forges give them review by pull request.

## Exchange repositories

An exchange repository is a git repository of bundle files
(`bundles/YYYY/<set>-<bundle>.kbundle.json`) that proposes knowledge to one
store. Bundles are uniquely named and never edited, so branches and merges
over them never conflict.

```
kb exchange init ~/exchange/tpc        # git init, a README, bundles/, the hooks
```

Push it to the forge, and have contributors clone it. A contributor then
links it to their edge to the store:

```
kb neighbor add tpc --relation up --url https://example.org/kb/tpc.sqlite --land pr --exchange ~/exchange/tpc
kb push --to tpc obs-…                 # commits the bundle on a branch, pushes it, opens the pull request
```

The pull request's description is the bundle rendered for review (`kb bundle
show --format md`), against the store if a copy of it is visible. After
merging, the store's maintainer imports:

```
git -C ~/exchange/tpc pull
kb -C /data/kb/tpc inbox import ~/exchange/tpc --accept   # the pull request was the review
```

## Git hooks

```
kb git hooks install [--repo PATH]    # pre-commit, pre-push, post-merge
kb git hooks status  [--repo PATH]
kb git hooks remove  [--repo PATH]
```

| hook | effect |
|---|---|
| `pre-commit` | staged bundle files must verify (format, checksum, content hashes). Changing or deleting an existing bundle is refused: bundles are immutable |
| `pre-push` | the same for every commit being pushed |
| `post-merge` | one line: how many bundles the repository holds, and how to import them |

How installation behaves:

- `kb` adds a marked block (`# >>> kb >>>` … `# <<< kb <<<`) to the hook
  scripts, after the shebang. Your own hook content stays, and `remove`
  takes out only kb's block.
- It respects `core.hooksPath`.
- `kb exchange init` installs the hooks. Hooks aren't cloned, so after
  cloning an exchange repository, run `kb git hooks install` once.

In an emergency, `git commit --no-verify` skips the hooks. CI checks again
([ci](ci.md)).

## Forges: pull requests from the command line

`kb` talks to GitHub, Gitea and Forgejo through their APIs. Gogs has no
pull-request API, which `kb` tells you. The forge and repository come from
the git remote of the directory you run in (for example an exchange clone),
matched by host.

```
kb forge pr list                                   # open pull requests on this repository's remote
kb forge pr show 12
kb forge pr create --head kb/ks-… --title "..." --body "..."
kb forge pr comment 12 --body "Please add evidence for the second item."
kb forge pr merge 12 --method squash
kb forge pr close 13
```

Give `--forge NAME --repo OWNER/NAME` for another repository. The
`kb-review-prs` skill teaches Claude to review bundle pull requests this
way. Merging and closing follow the `merge` autonomy level: `never` in
group stores and `ask` in home and project stores, by default.

The API token is separate from git's own credentials. `git push` still
uses your SSH key or credential helper.

## Configuring a forge and its token

github.com works without configuration if `GH_TOKEN` or `GITHUB_TOKEN` is
set. For everything else, or to keep the token in `pass`:

```
kb forge add work --type forgejo --host git.example.org --token-from "cmd:pass show kb/forge/work"
kb forge auth work
```

`kb forge auth` tells you where to create the token (the forge's
settings page) and which scopes it needs, and then tests it:

```
Forge work: forgejo at git.example.org
1. Create a token at https://git.example.org/user/settings/applications
   with token scopes write:repository and read:user.
2. Store it in pass (recommended):  pass insert kb/forge/work
...
3. Testing...
   token works: logged in as you (abcd… (40 chars))
```

The forge table goes in **your** config (`~/.config/kb/config.toml`), never
in a store: tokens are personal.

```toml
[forges.work]
type = "forgejo"
host = "git.example.org"
token = {command = "pass show", id = "kb/forge/work"}
```

## Secrets

Wherever `kb` needs a secret, it takes a *spec* saying where to get it:

| spec | where the secret comes from | |
|---|---|---|
| `cmd:pass show kb/forge/work` or `{command = "pass show", id = "kb/forge/work"}` | first line of the command's output | **recommended** |
| `file:~/.config/kb/work.token` or `{file = "..."}` | first line of the file | `kb` warns if its permissions are more open than 0600 |
| `env:FORGEJO_TOKEN` or `{env = "..."}` | an environment variable | |
| `value:abc123` | the token itself | works, with a warning. `kb forge add` refuses to write it into a file |

With a command spec, the identifier (`id`) is appended as the last
argument, so one `command` serves many secrets. The command runs with your
terminal attached, so `pass`/gpg can ask for a passphrase. Any program that
prints the secret on its first line works: `pass`, `gopass`,
`secret-tool lookup ...`, `op read ...`.

Check a spec without revealing the secret:

```
kb secret test "cmd:pass show kb/forge/work"
command: pass show kb/forge/work -> abcd… (40 chars)
```

Tokens are looked up in this order:

1. `--token-from` (on `kb forge auth`);
2. `KB_FORGE_TOKEN_<NAME>`, then `KB_FORGE_TOKEN`;
3. the configured `token`;
4. the forge's usual variables (`GH_TOKEN`/`GITHUB_TOKEN`,
   `GITEA_TOKEN`/`FORGEJO_TOKEN`).

**For LLM sessions:** Claude never needs to see a token. `kb` resolves it
internally. Don't paste tokens into a chat; store them in `pass` and let the
command spec fetch them.
