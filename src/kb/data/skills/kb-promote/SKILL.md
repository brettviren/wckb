---
name: kb-promote
description: Review this knowledge base (kb) store and promote selected records to a neighbor store (the user's home store, a group store ...). Use when the user asks to review, promote, push or share KB entries upward, or at a milestone when review autonomy allows it.
---

# Promotion review

Moving knowledge outward is the human's call, informed by you. Keep their
effort small: a short, well-argued list rather than a dump.

1. **Pick the target.** `kb neighbor list` shows the neighbors; promotion
   normally goes to an `up` neighbor. If there are several and the user
   didn't say, ask which.
2. **Tidy first.** Run `kb check` and `kb lint`. Fix weak summaries and
   missing subjects on the records you're about to propose (allowed via
   `kb set`). A record should make sense to someone outside this
   project.
3. **Get candidates:** `kb candidates --to NEIGHBOR` (`--json` for detail).
   It ranks:
   - corrections to records already pushed (always propose these);
   - records marked `promote`;
   - verified ones;
   - and it flags blockers: drafts, local-only files, access more
     restricted than the target.

   Add your own judgment. Would the wider audience need this, and does it
   generalize beyond this project? Drop project-internal trivia, and
   anything personal or half-baked.
4. **Present** at most about ten, as `ID: title. Why it is worth sharing.`
   Say what you recommend leaving behind and why, in one line each. Mention
   blockers and how to clear them. Ask about access relabeling explicitly;
   never relabel on your own.
5. **Dry run.** See what would happen, including dangling references and
   conflicts: `kb push --to NEIGHBOR ID ... --dry-run`. If the neighbor
   reviews what it receives, show the user the rendering they will see:
   `kb kset new ...`, then `kb bundle make KSET -o /tmp` and
   `kb bundle show FILE`.
6. **Act per autonomy** (see the table in CLAUDE.md, or
   `kb policy autonomy`):
   - `never`: give the human the exact `kb push` command to run;
   - `ask`: once they approve, run
     `kb push --to NEIGHBOR ID ... --approved "<their words>"`;
   - `auto`: push the clear cases, list them in your reply, and still ask
     about borderline ones.

   `kb push` makes a knowledge set (`ks-...`) of what you name and
   transfers it atomically. Add `--deep` when the receiver should get
   everything the items cite; a topic usually wants this. Otherwise the
   receiver gets dangling references it can `kb obtain` later. The edge
   decides how the push lands (`--land apply|inbox|pr` overrides): a group
   store typically takes it into its inbox, or as a pull request in its
   exchange repository.
7. **Conflicts.** A conflict means the receiver holds a *different* record
   under the same ID, or a diverged copy of an editable one. Don't force
   it: report it to the human with `kb context ID --in NEIGHBOR` for both
   sides.
8. **Record decisions to skip.** For items the human rejects for good, run
   `kb set ID --add tags=no-promote:NEIGHBOR` so they don't come back.
9. **Report** what was pushed and where: applied, waiting in the receiver's
   inbox, or as a pull request (give its URL).
