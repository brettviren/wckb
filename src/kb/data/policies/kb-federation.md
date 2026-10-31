## Neighbor stores and knowledge transfer

This store is linked to others. The table above lists them with what you may
do. The relation of each link sets how knowledge moves by default:

| link | push (here → there) | pull (there → here) |
|---|---|---|
| `up` (broader) | `select` | `all` |
| `down` (narrower) | `all` | `select` |
| `peer` | `select` | `select`, into the inbox for review |
| `twin` (a copy of this store) | `kb sync` | |

- **Reading.** Neighbors are references: `kb context ... --reach 1`,
  `kb search ... --reach 2`, `--in NAME`. Cite items from other stores with
  the store name.
- **References are store-independent.** An item may cite one that isn't
  here. `kb check` warns `dangling`, and `kb obtain ID` (or
  `kb obtain --dangling`) fetches it from the nearest store that has it.
- **Transfers move knowledge sets.** A set is shallow (just the named items)
  or deep (also everything they reference). `kb push --to N IDS` makes the
  set for you, and `--deep` follows references. A transfer is atomic, and
  it's refused if an item conflicts with what the receiver holds.
- **Landing.** A push lands per the edge: `apply` (straight in), `inbox` (a
  bundle the receiver reviews) or `pr` (a bundle proposed by pull request).
  Incoming bundles are reviewed with skill kb-review.
- **Writing.** You write to this store. Knowledge leaves only through
  `kb push`, as the outcome of a promotion review (skill kb-promote).
- **Autonomy.** For `push`, `pull`, `accept` and `merge`, follow the table:
  - `never`: give the human the command;
  - `ask`: get approval and pass it with `--approved "<their words>"`;
  - `auto`: act, then report.
- **Never relabel access** (`--access`) without the human saying so.
