---
name: kb-review-prs
description: Review, merge or close pull requests that propose knowledge bundles to a store's exchange repository on GitHub, Gitea or Forgejo. Use when the user asks about incoming KB pull requests or what is waiting for review on the forge.
---

# Review bundle pull requests

Some stores receive knowledge as pull requests in an *exchange repository*:
a git repository of bundle files. Each pull request adds one bundle, and
its description is the bundle rendered for review.

1. **List them:** `kb forge pr list --repo OWNER/NAME --forge NAME`, or run it
   inside the exchange clone, where the remote names the repository.
   Authentication problems: `kb forge auth NAME`. Never ask the user to
   paste a token into the chat.
2. **Inspect one** without touching the user's checkout:

   ```
   git -C EXCHANGE fetch origin <head-branch>
   git -C EXCHANGE show FETCH_HEAD --stat
   git -C EXCHANGE show FETCH_HEAD:<path>.kbundle.json > /tmp/b.kbundle.json
   kb -C STORE bundle show /tmp/b.kbundle.json      # rendered against the receiving store
   kb -C STORE bundle check /tmp/b.kbundle.json     # what accepting would do; exit 1 if it can't apply
   ```

3. **Judge it** as in kb-review: worth having, attributed, summarized, cites
   its evidence, no conflicts, access appropriate. Summarize for the human
   and recommend merge, changes or close.
4. **Act per `merge` autonomy:**
   - `never`: give the human the command;
   - `ask`: once they say so,
     `kb forge pr merge N --approved "<their words>"` (or `pr close N`);
   - `auto`: merge clean, clearly good ones and report.

   Ask for changes with `kb forge pr comment N --body "..."`.
5. **After merging:** `git -C EXCHANGE pull`, then
   `kb -C STORE inbox import EXCHANGE --accept` brings merged bundles into
   the store (the pull request was the review).
