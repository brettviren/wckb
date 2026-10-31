# CI, hooks and scheduled jobs

Stores are not git repositories, so there's no CI "on the store". The
store's database enforces append-only itself, on every write. CI has two
jobs:

1. **check bundles proposed by pull request** to an exchange repository;
2. **run the scheduled work** of a group store: rebuild views, publish them,
   and publish a fresh snapshot for readers.

| where | command | catches |
|---|---|---|
| Claude Code hook | `kb hook check` (policy `kb-check-hook`) | bad edits in a `kb checkout` directory, while Claude writes |
| exchange repository git hooks | `kb git hooks install` | corrupt or edited bundles, before commit or push |
| CI on pull requests to the exchange repository | `kb bundle check FILE` against a snapshot of the store | bundles that would not apply cleanly. **The gate for pull requests** |
| the store, on every write | database triggers | any change to what a source asserts |
| the store, on a schedule | `kb check --deep` | tampering outside kb, missing or corrupted content |

Protect the exchange repository's main branch so merging needs a passing
pipeline and one approval.

## Checking bundle pull requests (GitLab CI)

The job needs a read-only copy of the store. The store's maintainer
publishes one with `kb snapshot`. CI downloads it and checks each new
bundle against it.

```yaml
# .gitlab-ci.yml in the exchange repository
image: ghcr.io/astral-sh/uv:python3.12-bookworm

variables:
  KB_TOOL: "git+https://gitlab.example/group/wckb.git@v0.1.0"   # pin the kb version
  SNAPSHOT: "https://example.org/kb/tpc.sqlite"

check-bundles:
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - mkdir -p /tmp/store && curl -fsSL "$SNAPSHOT" -o /tmp/store/kb.sqlite
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - |
      for f in $(git diff --name-only --diff-filter=A origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME -- 'bundles/*.kbundle.json' 'bundles/**/*.kbundle.json'); do
        uvx --from "$KB_TOOL" kb -C /tmp/store bundle check "$f" || exit 1
        uvx --from "$KB_TOOL" kb -C /tmp/store bundle show "$f" --format md >> report.md
      done
  artifacts:
    paths: [report.md]
```

`kb bundle check` verifies the bundle (format, checksum, content hashes),
validates every item, and plans the application against the store. It
exits 1 on schema errors, conflicts, or anything that wouldn't apply
cleanly. The markdown report is what a reviewer reads.

## GitHub Actions

```yaml
name: bundles
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: astral-sh/setup-uv@v5
      - run: mkdir -p /tmp/store && curl -fsSL https://example.org/kb/tpc.sqlite -o /tmp/store/kb.sqlite
      - run: |
          for f in $(git diff --name-only --diff-filter=A origin/${{ github.base_ref }} -- 'bundles/**'); do
            uvx --from "git+https://github.com/group/wckb@v0.1.0" kb -C /tmp/store bundle check "$f" || exit 1
          done
```

## Scheduled work for a group store

Run it on the machine that holds the store: its single writer. Never run
it on a copy, and never on a network filesystem.

```sh
#!/bin/sh
# nightly, e.g. from cron on the store's host
set -e
STORE=/data/kb/tpc
git -C ~/exchange/tpc pull -q
kb -C $STORE inbox import ~/exchange/tpc --accept      # merged pull requests
kb -C $STORE build --world --publish                   # re-extract, rebuild stale views, publish
kb -C $STORE check --deep                              # consistency and content hashes
kb -C $STORE snapshot /eos/group/www/kb/tpc.sqlite     # what members and CI read
kb -C $STORE snapshot /backup/kb/tpc-$(date +%F).sqlite
```

## Notes

- **Pin the `kb` version** in CI. A store records the schema revision it
  needs (`kb config get kb`), and an older `kb` refuses a store that needs a
  newer schema rather than misreporting.
- The scheduled job needs the content and publish stores mounted, and
  credentials for any URLs it must fetch. URLs behind SSO need a service
  account; otherwise run `kb verify` from members' machines.
- `kb check --json` and `kb bundle check` give machine-readable output for
  bots that comment on pull requests.
