# Working with Claude

Most items will be written by Claude on your behalf. Your part is to
decide what's worth keeping, and what goes to a wider audience.

## What is installed where

`kb init` enacts the store's policy ([policy](policy.md)):

- a generated block in `CLAUDE.md` with the store context, the autonomy
  table and the selected instructions;
- skills in `.claude/skills/`;
- hooks in `.claude/settings.json`.

For a project store in `./kb`, these go into the project directory, where
you run Claude.

| piece | effect |
|---|---|
| `kb-routine` instructions | Claude consults the store before answering about the project, and files established facts as it works (`capture` autonomy) |
| recall hook | on every prompt, a quick keyword search; up to five relevant items are added to Claude's context |
| session hook | at session start, one line: item count, promotion candidates, stale items, policy drift |
| check hook | when Claude edits files in a `kb item checkout` directory, the checkout is validated; problems go straight back to Claude |
| skills | consult, capture, ingest, correct, view, curate, promote, review (inbox), review-prs (bundle pull requests): procedures loaded only when needed |

To use your home store from *every* session, enact its policy user-wide:
`kb -C ~/kb policy enact --target user`. Read about the context cost in
questions C5 first.

## Consulting the KB from another project

Ask normally. "Was anything odd about run 1243?" or "what drift velocity
did the 2025 production use? Check the KB." The `kb-consult` skill runs
`kb context` and `kb search`, then answers with item IDs as citations, so
you can check them (`kb show ID`).

## Capturing knowledge from a session

At a natural stopping point, say "capture this session to the KB". The
`kb-capture` skill:

1. lists candidate facts and asks which to keep (expect to drop some),
   unless `capture` autonomy is `auto`, in which case it files them and
   tells you in one line;
2. writes each item with `kb new` / `kb add`, attributed and with validity,
   into the store you're working in (usually the project store);
3. runs `kb check` and `kb lint`, and reports any overlap with existing
   items;
4. tags items that deserve a wider audience with `promote`.

Nothing leaves the store until a promotion review
([stores-and-promotion](stores-and-promotion.md)). `kb log` shows what was
filed.

## What the check hook does

Items normally change only through `kb` commands, and the store itself
refuses changes to what a source asserts. Occasionally files are the
natural way to work, for example on a view's scripts. Then Claude uses
`kb item checkout ID DIR`, edits the files, and runs `kb item checkin DIR`.

While it edits files in such a directory, `kb hook check` dry-runs the
checkin. Problems, such as changing a frozen field or breaking the schema,
go back to Claude in the same turn.

## Good prompts

- "Ingest these slides from Indico <URL>; I have a copy at ~/Downloads/x.pdf."
  The `kb-ingest` skill handles the SSO case with `--url`.
- "The noise was actually in run 1243 (elog 5521). Correct the KB." The
  `kb-correct` skill supersedes the item and revises the topics.
- "Make a one-page PDF on TPC noise for Thursday." This uses `kb-view`.
- "Curate the KB." This uses `kb-curate`, which handles overlaps, stale
  topics and old unverified claims.
- "Review the KB and push the good bits to home." This uses `kb-promote`,
  which shows a ranked list and pushes what you approve
  ([stores-and-promotion](stores-and-promotion.md)).
- "What arrived in the group store's inbox?" This uses `kb-review`, which
  renders each bundle, records its own review (labelled as an LLM's), and
  leaves the decision to you unless `accept` autonomy allows otherwise
  ([review](review.md)).

## Limits to keep in mind

- Claude records what it was told, and with whose authority. If you state
  something from memory, it will be recorded as asserted by you, `unverified`.
  That's correct, not a nuisance.
- An LLM description of a plot (`fidelity: llm`) is never accepted as
  evidence for `verified`.
