# 0002: Abstract files, versions and sources

- **Status:** accepted (2026-10-31). The parts marked *open* are tracked in
  [questions.md](../questions.md), section V.
- **Decides:** how kb refers to files and file sets independently of the
  sources that supply them; what a version is; how one well-defined file set
  is chosen when several sources, each with its own idea of versions, could
  supply it.
- **Code:** `kb/assets.py`, `kb asset ...`, the `asset` kind
  (`schemas/v1/asset.json`), and the `asset` rendition location.

## 1. The problem

Material reaches kb from sources that disagree about identity and versions:

| source | identifies a file by | versions |
|---|---|---|
| a filesystem tree | path (names change; content changes in place) | none, unless a convention like `v1/`, `v2/` |
| DataLad | path in a dataset, at a commit; annex keys carry the sha256 | commits and tags |
| DocDB | document number and version number; files by name *per version* | native; each version has its own, possibly different, file set |
| Indico | event, contribution, folder, attachment id | none visible to users (believed to keep old files internally; to verify, V1) |
| a URL | the URL | none; content may change behind it |
| the store itself | sha256 | none needed: content-addressed |

A name, a path, a URL or a source-specific ID can all change while the
content stays the same, or stay the same while the content changes. A
knowledge item that cites "the slides" needs a reference that survives all
of that, and that still pins down exactly which bytes it meant.

## 2. Decisions

1. **References to files are indirect.** An **asset** is a knowledge item
   (`as-…`) standing for one file or a file set. Its kb ID is the identity,
   which is generous: it survives renames, moves, new sources and new
   content. Names, paths, URLs and source IDs are *locators*, never
   identity.

2. **A version is identified by its content.** Each version has an abstract
   **label** (`v1`, `v2`, `2026-10-28` ...), a human handle, and a
   **digest**:
   - for a file asset, the file's sha256;
   - for a file set, the sha256 of its manifest of (relative path, sha256)
     pairs (`match = exact`), or of the multiset of sha256s
     (`match = content`, so renames are not new versions).

   Once recorded, a version's digest never changes. The store's guard
   trigger enforces it, as it does for rendition hashes. A label can be
   registered before its content is seen; the first sighting fills the
   digest in, once. Versions and bindings can be added; registered versions
   cannot be removed or changed.

3. **Zero or more bindings connect an asset to sources.** A binding names a
   source type, a **locator** (the source's concrete identifiers: a named
   filesystem root and path, a URL, a DataLad dataset and path, an Indico
   event and attachment, a DocDB document) and a **versioning** mode:

   | mode | meaning | example |
   |---|---|---|
   | `none` | the source has only its current content | a plain directory, a URL, Indico materials |
   | `native` | the source's version IDs are the asset's labels | DocDB versions, DataLad tags |
   | `pattern` | versions are found by a path pattern with `{version}` | `v{version}/`, `slides-{version}.pdf` |
   | `map` | an explicit table from native IDs to labels | `final` → `v3` |

   Filesystem roots are *named* (`root = "eos-group"`), and each machine maps
   the name to a path in the `filesystems` setting, so one binding works
   wherever the tree is mounted.

4. **Within a source, versioning decides; content checks it.** A versioned
   source's offers carry a label from the binding. An unversioned source
   offers only its current content, which is related to versions *by
   content*: it is the version whose digest it matches, or it is new.
   - One source offering different content under one label is a
     **conflict** (intra-source ambiguity).
   - A source offering, under an existing label, content that differs from
     that version's digest is a **conflict** (someone rewrote `v1/` in
     place).
   - Conflicts are reported and never registered.

5. **Across sources, content decides.** To resolve (asset, version):
   - **Target.** A label, `latest` (the last registered), `digest:SHA`, or
     `current` (whatever the highest-priority source offers now, registered
     or not).
   - **Order.** Bindings are tried in priority order. The default is the
     store itself, then filesystems, DataLad, DocDB, URLs, Indico: cheap
     and local before remote.
   - **Digest known.** The first offer whose content has that digest wins.
     Offers with the right label but other content are *divergent*: noted
     and never used. Offers under other labels are checked last, since the
     same bytes may be filed under another name.
   - **Only a label known.** All offers under that label must agree, or
     resolution fails until one of them is registered.

   The result is one file set, verified by hash, plus notes about anything
   divergent.

6. **Registration turns observations into versions.**
   - `kb asset scan` asks every source what it offers and classes each offer
     as `known` (which version), `new`, `conflict` or `unreachable`.
   - `--register` records new content as versions. A versioned source
     supplies the label; otherwise the label is the next `vN`.
   - Every sighting (binding, native ID, time) is kept on the version, so
     "where has v2 been seen" is answerable.
   - Content from an unversioned source is **kept** in the store by default
     (`keep = auto`), because the source will overwrite it: Indico when a
     presenter re-uploads, a directory when a file is replaced. The store
     then becomes a source of that version (the `kb` binding), so it stays
     resolvable.

7. **Items cite assets, and still pin bytes.** A rendition can be supplied
   by an asset: the location `{"type": "asset", "asset": "as-…",
   "version": "v2", "path": "slides.pdf"}`. The rendition's own sha256
   still pins exactly which bytes it means. The asset is how to find them.

8. **Sources plug in.** An asset source driver answers one question: for
   this asset and binding, what do you offer? The answer is a list of
   candidates: native version ID, mapped label, and files with a lazy
   fetch and hash. `kb`, `fs` and `url` are implemented. `annex` (DataLad),
   `indico` and `docdb` are declared, with their locator shapes, and report
   "planned".

## 3. Your scenario, worked through

An Indico material file set is unversioned in Indico. You slurp it into
`v1/`, `v2/` subdirectories on disk, and also keep a DataLad dataset that
you overwrite and save. One asset has three bindings:

| binding | type | versioning |
|---|---|---|
| `indico` | indico (url today) | none |
| `copies` | fs, `{version}` pattern | pattern |
| `dl` | annex | native: commits or tags mapped to labels; planned |

- `kb asset scan --register` labels `v1` and `v2` from `copies`. Indico's
  current content matches `v2` by digest, so it is recorded as a sighting
  of v2, not a new version. DataLad commits would map through `map` or tag
  names to the same labels, and their annex keys give the sha256 without a
  download.
- `kb asset resolve as-…@v2` takes the first source, in priority order,
  whose bytes have v2's digest. If `copies/v2/` was damaged, it is reported
  divergent and another source supplies v2.
- When Indico's material changes again, a scan reports `new`. Registering
  makes it `v3`, kept in the store unless you say otherwise.

This is tested in `tests/test_assets.py::test_many_sources_one_version`,
with Indico simulated by URLs.

## 4. What remains open

See [questions.md](../questions.md) section V. In brief:

- Indico's real versioning (V1) and the DocDB API (V2);
- the order of `latest` when labels come from several sources (V3);
- more generous matching (V4): sizes and names when content can't be
  fetched cheaply;
- partial file sets (V5);
- the cost of hashing large trees (V6);
- label collisions across sources (V7);
- whether an observation can cite one file of a set (V8);
- DataLad commits versus labels (V9);
- garbage collection of kept content (V10).
