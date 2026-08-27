# Contributing

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test,dev]"
pre-commit install
```

`pre-commit install` only needs running once per clone — after that, `ruff
check --fix` and `ruff format` run automatically on every `git commit`
against whatever's staged, and will block the commit if they change
anything (fix reported, re-stage, commit again). It also installs a
`commit-msg` hook (see below) that rejects non-conventional commit messages.

## Commit messages

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Common types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`chore`, `ci`, `build`. Examples:

```
fix(tidal_client): stop leaking widgets on every match-tab refresh
feat(harvest): add fuzzy filter and sort to the playlist tree
ci: enforce conventional commits on pull requests
```

Enforced two ways:

- Locally, by the `commitizen` pre-commit hook (`commit-msg` stage) —
  installed automatically by `pre-commit install` above; a non-conforming
  message blocks the commit before it's created.
- In CI, `commit-lint` (`.github/workflows/build.yml`) runs `cz check`
  against every commit in a pull request's range, so messages made without
  the local hook installed (e.g. via a GUI client) still get caught before
  merge.

## Cutting a release

Version and tags are managed by `commitizen`, driven entirely by the commit
types above — no manual version editing:

```bash
cz bump          # picks the next version from commits since the last tag
                  # (feat -> minor, fix -> patch, "BREAKING CHANGE:" -> major),
                  # updates pyproject.toml's version, updates CHANGELOG.md,
                  # commits both, and tags the commit v<version>
git push --follow-tags
```

Pushing the tag is what triggers `.github/workflows/build.yml`'s `build` +
`release` jobs (see "Building a standalone executable" in the README) —
`cz bump` only prepares the commit and tag locally, it doesn't push.

`cz bump --dry-run` previews the next version without changing anything.
Note it needs at least one prior conventional commit to compute a bump from
— on a repo with no commit history yet, make a first `feat`/`fix` commit
before trying it.

## Before opening a PR

```bash
pytest                       # full test suite — no network or display needed
ruff check .
ruff format --check .
```

All three run in CI (`.github/workflows/build.yml`) and must pass before a
build proceeds. `pre-commit run --all-files` runs the same lint/format
checks plus a few hygiene hooks (trailing whitespace, YAML/TOML validity,
no accidental large files, `pip-audit` against installed dependencies) in
one go. CI additionally runs a Trivy scan of resolved dependency versions
and fails on any HIGH or CRITICAL severity vulnerability.

## Project layout

```
src/spotify_tidal_migrator/
  spotify_client.py         Spotify calls (harvest) — reverse-engineered
                              endpoints, see README's "Spotify tokens"
  tidal_client.py            Tidal session, matching, idempotent push
  matching.py                 ISRC/fuzzy track matching, no I/O
  models.py                    Dataclasses shared across the app
  store.py                      Local JSON playlist snapshot store
  paths.py                       Config/data directory locations
  utils.py                        Token extraction, user-id parsing
  gui/
    app.py                        Entry point (QApplication + MainWindow)
    main_window.py                 The three tabs: Harvest / Match / Push
    manual_match_dialog.py          Manual "Fix match…" search dialog
    workers.py                       QThread wrappers around blocking calls
tests/                       One file per module above, plus test_main_window.py
packaging/appimage/          AppImage build script + icon generator
```

## Testing conventions

- Every test is isolated from the real filesystem and network: `conftest.py`
  redirects all `paths` module constants into a per-test `tmp_path`, and
  Spotify/Tidal clients are exercised through mocked `sp`/`session` objects
  rather than real API calls.
- GUI code is tested by calling widget methods directly (e.g.
  `tab._on_playlists_fetched(...)`) rather than driving real clicks, and
  worker `QThread`s are tested by calling `.run()` directly instead of
  `.start()`, so they execute synchronously with no real threading involved.
- Qt runs under `QT_QPA_PLATFORM=offscreen` (set in `conftest.py`), so the
  suite needs no display — it runs fine in CI or headless.

If you add a new module, add a matching `tests/test_<module>.py` following
the same isolation approach — no test should need real network access, a
real Spotify/Tidal account, or a real display.

## Code style

Enforced by `ruff` (config in `pyproject.toml`) rather than written down
here — run `ruff format .` and `ruff check --fix .` and trust the result.
A few things ruff won't catch:

- No comments explaining *what* code does — name things so it's obvious.
  A comment is for a non-obvious *why* (a workaround, a subtle invariant).
- Don't add error handling for cases that can't happen; validate only at
  real boundaries (user input, external API responses).
- Prefer a few similar lines over a premature abstraction.

## Reporting issues

Open a GitHub issue with what you ran, what you expected, and what
happened instead. For a Spotify/Tidal auth failure, include the error text
but never the token itself.
