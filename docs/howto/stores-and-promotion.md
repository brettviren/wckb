# Stores, neighbors and moving knowledge

Knowledge has a scope. Notes about one analysis belong with that analysis.
What you have learned across projects belongs to you. What the group should
know belongs to the group. `kb` gives each scope its own store, links stores
as neighbors, and moves knowledge between them in well-defined ways.

## A typical setup

```
~/kb/                       home store (yours)
~/work/sigproc/kb/          project store beside the working files
/data/kb/tpc/               the group's store (on its maintainer's machine)
https://…/kb/tpc.sqlite     a published snapshot of it, for everyone to read
```

```
kb init ~/kb --scope home
cd ~/work/sigproc && kb init kb --scope project --up ~/kb
cd ~/kb && kb edge add tpc --relation up --url https://example.org/kb/tpc.sqlite --land inbox
```

To make your home store the `up` of every project without repeating
`--up`, put it in your user config:

```toml
# ~/.config/kb/config.toml
user = "per-yourname"

[neighbors.home]
relation = "up"
path = "~/kb"
```

`kb edge list` shows the neighborhood, including neighbors of
neighbors.

## Scope labels and edges

The `--scope` label (`project`, `home`, `group`, ...) is descriptive. It
only picks defaults: access, which policies are used, autonomy, and where
policy is enacted. What governs behavior is the **edges**:

| relation | meaning | push (here → there) | pull (there → here) | pulled items land |
|---|---|---|---|---|
| `up` | broader | `select`: only what you name | `all`: every new or changed item | applied |
| `down` | narrower | `all` | `select` | applied |
| `peer` | sibling | `select` | `select` | in the inbox, for review |
| `twin` | a copy of this same store | `kb sync` | `kb sync` | the later change wins |

The principle: knowledge flowing to a wider audience is selected, while
knowledge flowing down from a broader store is accepted. Override any of it
per edge:

```
kb edge add tpc --relation up --path /data/kb/tpc --pull none        # read it live, never copy
kb edge add lab --relation peer --path ~/peers/lab --prefer theirs    # trust their status values
kb edge add tpc --relation up --path /data/kb/tpc --land inbox       # pushes wait for review there
```

| option | values |
|---|---|
| `--push`, `--pull` | `select`, `all`, `none` |
| `--accept` | `direct` (applied), `review` (into this store's inbox) |
| `--prefer` | `ours`, `theirs`, `newer` (see "the same item along two paths" below) |
| `--land` | how pushes arrive there: `apply` (written directly), `inbox` (a bundle waiting for review), `pr` (a bundle proposed by pull request; needs `--exchange PATH`, a clone of its exchange repository) |
| `--relabel` | an access level that more restricted items may be relabeled to when pushed there |

A neighbor is found by `--path` (its directory on this machine, needed to
write to it) or `--url` (a published snapshot, read-only). Declarations go
in this machine's `.kb/local.toml` unless you add `--shared`, which puts
them in the store's settings for everyone using it.

## Reading across stores (no copying)

```
kb context "induction plane noise" --reach 1     # here + up/peer neighbors
kb search deconvolution --reach 2                # two hops: home, then the group
kb ls --subject software/sigproc --in tpc
kb edge fetch                                # refresh snapshot neighbors
```

Results are labelled with their store. An item present in several stores is
shown once, with "also in ...". Neighbors are opened read-only: SQLite in
read-only mode, with scratch files in your cache.

## Knowledge sets: what a transfer moves

Every transfer between stores moves a **knowledge set**: a `ks-…` item that
selects items.

- **shallow**: just the members;
- **deep**: the members and everything they reference (cited items,
  evidence, what they supersede ...), recursively.

When a set contains another set, `--nested follow` (the default) uses the
inner set's own depth, and `--nested inherit` uses the outer one.

```
kb kset new obs-a obs-b top-c --deep --title "Noise findings for the group"
kb kset expand ks-…            # the tree it denotes here, and what it references that is missing
kb show ks-…                 # the same, for people
```

Usually you don't make sets by hand: `kb push` and `kb pull` make one from
the items you name. The set travels with its items, so the receiver sees
what was sent together.

## Push: sending selected knowledge

```
kb edge candidates home                          # what is worth sending, ranked, with reasons
kb push --to home obs-1kfv obs-9c2               # shallow: just these
kb push --to home top-… --deep                   # the topic and everything it cites
kb push --to tpc ks-… --access group --land inbox   # a named set, relabeled, for review there
kb push --to home obs-… --dry-run                # what would happen: new, changed, conflicts, dangling refs
```

How a push lands:

| land | what happens |
|---|---|
| `apply` | the items are applied to the receiver in **one transaction**. Its checks run first, and nothing lands if any item fails |
| `inbox` | the receiver gets a **bundle** in its inbox, and a person, perhaps helped by an LLM, accepts or rejects it ([review](review.md)) |
| `pr` | the bundle is committed on a branch of the receiver's exchange repository and proposed as a pull request, with a rendered description ([git-and-forges](git-and-forges.md)) |

Either way, each copy keeps its ID and gets an `origin`: the sender, the
`path` of stores it travelled, the set, and the approval. Both stores log
the transfer (`kb edge ledger`, `kb log`).

A shallow push of a topic leaves its citations **dangling** in the
receiver. That's allowed, because references are store-independent: the
receiver can `kb pull` them later. Use `--deep` when the receiver should
get everything at once.

**Access never widens by accident.** A `private` item can't go to a `group`
store until someone relabels it with `--access group`, or the edge was
declared with `--relabel group`.

**Corrections travel too.** Supersede an item you already sent, and the
correction becomes the top candidate. Sending it supersedes the old version
in the receiver as well.

## Pull and obtain: receiving knowledge

```
kb pull --from home                    # the edge's form: from an up store, every new or changed item
kb pull --from lab obs-… --deep        # a peer: name what you want; it lands in your inbox
kb pull obs-…                        # a referenced item: from the nearest store that has it
kb pull --dangling                   # every reference here to an item not here
```

## The same item along two paths

In a graph an item can arrive more than once, possibly changed on one path:
for example project → home → group, and later project → group directly. The
receiver compares:

| part | rule |
|---|---|
| what the item asserts (immutable fields and content) | must be identical. A difference is a **conflict**: the whole transfer is refused, and nothing lands |
| list fields (subject, tags, evidence, related) | united |
| single values (status, summary, license) | the incoming value wins if it comes from the same store it came from before (an update), if the receiving edge says `prefer = theirs`, or, with `prefer = newer`, if it was changed later. Otherwise the receiver keeps its own, and the transfer report notes the disagreement |
| editable items (topics, views, drafts) | replaced when they come along the same path. Otherwise a conflict |

## Twins: one store on several machines

If you work on a laptop and a desktop, keep **one** home store and copy it
to the other machine. The copies are twins (same store id), and `kb sync`
moves every new or changed item both ways. When both sides changed the same
item's mutable fields, the later change wins.

```
kb store snapshot /media/usb/kb.sqlite                  # on the laptop: a consistent copy
# on the desktop: mkdir ~/kb && cp /media/usb/kb.sqlite ~/kb/   (the twin)
kb edge add desk --relation twin --path /mnt/desktop/kb   # where this machine sees the twin
kb sync
```

A twin reachable only as a published snapshot (`--url`) is pulled from,
not written. Run `kb sync` on the other side too.

If instead you keep a separate home store on each machine, link them as
`peer`s and move knowledge with push and pull.

## When Claude moves knowledge

The store's autonomy (`kb policy autonomy`) decides, per neighbor:

| level | push / pull / accept / merge by Claude |
|---|---|
| `never` | Claude proposes, and you run the command |
| `ask` | Claude asks, then runs it with `--approved "<your words>"` (recorded in the history and in each copy's origin) |
| `auto` | Claude acts and tells you what it did |

Only a person can change autonomy: `kb` refuses when it detects it's run by
an agent.
