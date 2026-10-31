---
name: kb-correct
description: Correct or withdraw something in the group knowledge base (kb) and propagate the correction to every topic and view built on it. Use when the user says an item is wrong, outdated, re-uploaded, or should be retracted.
---

# Correct the KB and propagate

Primary items are never edited in place. Corrections are new items, and
staleness tells you what to update.

1. Find the item: `kb context ID`, or search for it. Confirm with the user
   what is wrong and on what authority (who says so, and what evidence).
2. Write the correction:
   - **Replace:**
     `kb supersede ID --title "..." --summary "..." --text "<corrected statement; say what was wrong>" --by WHO --how HOW --evidence <ref-or-URL> [--runs ...]`.
     The new item inherits subject, validity and the rest unless you
     override them.
   - **Withdraw** with no replacement: `kb retract ID --reason "..."`.
   - **New version of slides or a paper** (`kb verify` reports `changed`):
     `kb add URL --supersedes ID --kind document ...`.
   - **Only our confidence changed**, not the content: `kb set ID status=disputed`
     (or `verified` with evidence). That's allowed on sources.
3. **Propagate.** Run `kb stale`, then handle each item:
   - `rendition`: `kb extract ID --name NAME --force`.
   - `topic`: get its page (`kb content TOPIC > page.md`). Rewrite the affected passages to
     reflect the correction, cite the new item, and say what changed if
     readers may have relied on the old claim ("earlier reports placed this
     in run 1234 [obs-old]; corrected to 1243 [obs-new]"). Replace the old
     ID in `derived_from` with the new one (`kb set TOPIC --replace-ref OLD=NEW`),
     put the page back (`kb content TOPIC --file page.md`), then `kb stamp TOPIC`.
   - `view`: check its sources still make sense and update inputs the same
     way, then `kb build VIEW`. Publish only if the user asks.
4. Repeat `kb stale` until it's empty, or until what's left is explained to
   the user.
5. `kb check`, then `kb lint`. If the corrected item was pushed to other
   stores, it is now a top promotion candidate. Tell the user, so the
   correction reaches them too (kb-promote).
