# Reviewing incoming knowledge

Knowledge offered to a store arrives as **bundles**: a knowledge set and its
items in one self-checking file. A bundle is named by its own hash, so any
alteration is detected.

Bundles wait in the store's **inbox**. People, and LLMs as advisers, review
them. A person decides, unless the store's `accept` autonomy says
otherwise.

## Where bundles come from

| route | how |
|---|---|
| a push that lands in the inbox | `kb push --to tpc obs-… --land inbox` (or an edge declared `--land inbox`) |
| a pull from a peer | peers' default `accept = review` puts pulled items in your inbox |
| a file by any means (mail, a shared directory) | `kb bundle make ks-… -o DIR` on one side, `kb inbox receive FILE` on the other |
| an exchange repository, after a merged pull request | `kb inbox receive EXCHANGE_DIR --accept` (the pull request was the review) |

## What is waiting

```
kb inbox list                     # pending bundles: sender, set, reviews so far
kb inbox list --status all        # including accepted and rejected
```

## Reading a bundle

```
kb inbox show REF                # REF: a bundle-id prefix or the set's id
kb inbox show REF --format md    # compact markdown (also good for an LLM)
kb inbox show REF --format json  # the full view model, for programs
kb bundle show FILE               # the same, for a bundle file not (yet) in the inbox
```

The rendering is written for people:

- **Provenance:** who sent it, when, from which store, their note and
  approval.
- **What accepting would do:** how many items are new, changed, already
  here or conflicting; references it would leave unresolved; whether the
  checks pass.
- **Contents:** the set as a tree, with nested sets unfolded and missing
  leaves marked.
- **Each item, as a card:**
  - kind, status and access;
  - who asserted it, how and when, with person IDs resolved to names;
  - dates, run ranges, subjects;
  - for changed items, the field-by-field changes ("status: unverified →
    disputed");
  - links: DOI, arXiv, INSPIRE, Indico (with `kb config set links.indico
    https://indico.example.org`), the original's URL;
  - where the material is, in words ("held in the store", "online, needs
    sso", "only on host X");
  - an excerpt of the material itself: the note, the page, extracted text,
    slide titles.

Hashes are never shown. `kb bundle check FILE` gives the mechanical verdict
as JSON (exit 1 if it can't be applied cleanly).

## Recording reviews

Anyone can add a review: a verdict (`accept`, `reject`, `changes`,
`comment`) and the reasoning.

```
kb inbox review REF --verdict changes --text "obs-…: cite the elog entry; the rest is fine."
```

When an agent runs this, the review is recorded as an LLM's (`claude`,
kind `llm`), and the decision stays with the human. Ask Claude to "review
the inbox" and the `kb-review` skill renders each bundle, judges it item by
item, and records its review.

## Deciding

```
kb inbox accept REF --note "thanks"           # applies the bundle in one transaction
kb inbox accept REF --access group            # relabeling more restricted items, deliberately
kb inbox reject REF --reason "duplicates obs-…; please supersede that one instead"
```

Accepting applies the bundle exactly like a direct transfer: the receiving
store's checks run first, and nothing lands if anything fails. Conflicts
refuse the bundle. Decisions and reviews are in the history
(`kb log --action inbox-accept`, `kb log --action review`).

**Autonomy.** The `accept` level governs agents:

| scope | default |
|---|---|
| group | `never`: an LLM reviews, a person decides |
| home, project | `ask`: Claude accepts after you say so, passing `--approved "<your words>"` |

Change it with `kb policy autonomy accept=...`; only a person can.

## The pull-request route

A group can take contributions as pull requests instead. Its exchange
repository is a git repository of bundle files ([git-and-forges](git-and-forges.md)):

1. a member pushes with `--land pr`: the bundle is committed on a branch and
   a pull request opened, its description being the rendering above
   (against the group store, if the member can see a copy of it);
2. reviewers comment and approve on the forge, and CI checks the bundle
   ([ci](ci.md));
3. after merging, the group store imports it:
   `kb inbox receive EXCHANGE_DIR --accept`.
