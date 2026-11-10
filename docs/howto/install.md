To get the full set of features, it is recommended to install
`kb`{.verbatim} from the wckb package with all optional dependencies and
runtime support with:

``` example
$ uv tool install \
    --with-executables-from git-annex,git-annex-remote-indico \
    'git+https://github.com/brettviren/wckb[pdf,pptx,root,indico]'
```

What the extras add:

-   `pdf`{.verbatim}, `pptx`{.verbatim}, `root`{.verbatim}: transforms
    that need a Python library (PDF, slides, ROOT files).
-   `indico`{.verbatim}:
    [git-annex-remote-indico](https://github.com/brettviren/git-annex-remote-indico),
    so Indico sites can be held as DataLad datasets. Leave it out if you
    don\'t use Indico.

Tools `kb`{.verbatim} uses when it finds them on `$PATH`{.verbatim}
(`kb rendition transforms`{.verbatim} says which it found):
`pdftotext`{.verbatim} (poppler), `pandoc`{.verbatim} (text from HTML,
DOCX, LaTeX \...), `tesseract`{.verbatim} (OCR) and
[monolith](https://github.com/Y2Z/monolith) (a self-contained copy of
each web page added, the rendition `page`{.verbatim}; install it with
`cargo install monolith`{.verbatim} or your package manager).

`git-annex`{.verbatim} and `git-annex-remote-indico`{.verbatim} are
named explicitly so their commands are found in `$PATH`{.verbatim}. The
latter is needed to use an Indico site as a source for knowledge
material.

Note, you need a new enough `git-annex`{.verbatim}. DataLad needs at
least 10.20230126, and `kb`{.verbatim} asks for 10.20250605 or newer.
The older version provided by Debian 13 is not sufficient. If you have
installed `git-annex`{.verbatim} in another way assure the new version
is found, eg using `which git-annex`{.verbatim} and
`git annex version`{.verbatim}.

``` example
$ kb --version
kb, version 0.1.0
```

Note, while in early development, version will stay at 0.1.0. Until
releases begin to be made, updating to latest development is done with:

``` example
$ uv tool upgrade wckb --reinstall
```

# Working on `kb`{.verbatim} itself

``` example
$ git clone https://github.com/brettviren/wckb
$ cd wckb
$ uv sync            # the dev group includes all the extras, indico too
$ uv run pytest
```

`uv run`{.verbatim} puts the environment\'s `git-annex`{.verbatim} first
on `PATH`{.verbatim}, so tests and `uv run kb`{.verbatim} use it.

Or, install an editable version

``` example
$ uv tool install  --force -e \
    --with-executables-from git-annex,git-annex-remote-indico \
    '.[pdf,pptx,root,indico]'
```
