# Spotify → Tidal Playlist Migrator

A desktop GUI (PySide6) with a three-step pipeline:

1. **Harvest** — paste captured Spotify credentials (see below), pick a
   playlist owner, then sort/filter/preview and import the playlists you
   want.
2. **Match** — log into Tidal, then match each imported playlist's tracks
   against Tidal (ISRC first, fuzzy artist/title search as a fallback) and
   review the match rate *before* anything is written to Tidal. Expand any
   playlist to see its tracks and fix a wrong or missing match by hand.
3. **Push** — commit whichever matched playlists you select to Tidal, inside
   a folder named after the harvested Spotify user (or a name you choose).
   Push never re-runs matching, and it's idempotent: pushing an
   already-pushed playlist again only adds newly-matched tracks, never
   duplicates or a second playlist.

Imported playlists are cached locally between steps so the app survives a
restart, but this is just plumbing — the UI doesn't expose it as a feature.

## Usage

### Setup

```bash
cd spotify-tidal-migrator
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Spotify tokens

There's no free way to register a Spotify Developer app any more — as of
Spotify's February 2026 Developer Mode changes, the app owner account must
hold an active Premium subscription, for every grant type including Client
Credentials. This tool sidesteps that entirely by using the same two
credentials Spotify's own web player holds for whichever account is logged
into your browser, rather than credentials issued to a registered app.

As of mid-2026 the web player no longer reads playlist data through the
public `api.spotify.com` Web API — it reads a profile's public playlists
from an internal `spclient.wg.spotify.com` endpoint, and a playlist's tracks
via `api-partner.spotify.com`'s GraphQL API. Both require a bearer token
*and* a separate `client-token` alongside it:

1. Open [open.spotify.com](https://open.spotify.com) in your browser, logged
   into any Spotify account (Free is fine), and open someone's profile page.
2. Open DevTools → Network tab, filter for `pathfinder`, reload.
3. Right-click one of the `pathfinder/v2/query` requests → Copy → **Copy as
   fetch** (or "Copy Request Headers" — either works).
4. Paste the whole thing into the Harvest tab's single paste box. It
   auto-extracts both the `authorization: Bearer …` token and the
   `client-token` header from whatever you pasted (raw headers, "copy as
   fetch", or "copy as cURL" all parse fine) and shows a ✓ once both are
   found — no need to pick out the values by hand.

Both are valid for roughly an hour and aren't refreshable, so they only
cover one sitting — harvest whatever you need, then re-copy fresh values
next time. This rides on Spotify's official web player's own session rather
than a registered app, which is the same approach several other open-source
Spotify tools (e.g. spotDL) moved to after the same policy change — it's
unofficial and outside Spotify's documented API surface, so treat it as
best-effort. In particular, the playlist-tracks request uses a Spotify
"persisted query" hash that can change when Spotify updates its web player;
if fetching playlists tracks starts failing, that hash (in
`spotify_client.py`) needs re-capturing from a live session the same way.

### Tidal

No setup needed — click **Connect to Tidal** on the Push tab, approve the
login link in your browser, and the session is cached locally for next time.

### Run

```bash
spotify-tidal-migrator
```

or

```bash
python -m spotify_tidal_migrator.gui.app
```

### Walkthrough

1. **Harvest** — paste your captured request, enter a Spotify user ID or
   profile URL (e.g. `https://open.spotify.com/user/123059001`), click
   **Connect & Fetch Playlists**. Descriptions and a track preview fill in
   for each playlist in the background as they're fetched; click a column
   header to sort, or use the filter box to fuzzy-search by name/owner/
   description. Tick the playlists you want, click **Import Selected**.
2. **Match** — click **Connect to Tidal** (opens a login link in your
   browser). Tick the imported playlists, click **Match Selected Against
   Tidal**. Expand a playlist to see its individual tracks and *Match
   Status*; for anything unmatched (or matched wrong), click **Fix match…**
   to search Tidal yourself and pick the right one.
3. **Push** — switch to this tab (it auto-refreshes from Match), tick the
   playlists you're happy with, optionally type a Tidal folder name (blank
   defaults to each playlist's harvested owner), click **Push Selected**.
   Creates a *public* Tidal playlist per selection (or reuses one with the
   same name in that folder, adding only newly-matched tracks).

## Building a standalone executable

```bash
pip install -e ".[build]"
pyinstaller --noconfirm --clean --name spotify-tidal-migrator --windowed pyinstaller_entry.py
```

Produces `dist/spotify-tidal-migrator/` (an `.app` bundle on macOS) — no Python
install required to run it. PyInstaller doesn't cross-compile, so this only
builds for the OS you run it on; `.github/workflows/build.yml` runs it on
Linux, Windows, and macOS runners when you push a tag like `v0.1.0` (or
trigger the workflow manually) and uploads each as a build artifact. A
tag push additionally zips all three and attaches them to a GitHub Release.
Plain pushes to `main` only run tests, lint, the security audit, and the
commit-message check — not this multi-OS build, since its artifacts have
nowhere useful to go outside a tagged release.

On Linux, the distributable is instead a single-file AppImage:

```bash
./packaging/appimage/build.sh
```

Produces `dist/spotify-tidal-migrator-x86_64.AppImage` — wraps the same
PyInstaller onedir bundle in an AppDir (`AppRun`, `.desktop` file, icon) and
runs it through [`appimagetool`](https://github.com/AppImage/appimagetool)
(auto-downloaded to `build/tools/` on first run). The result is a single
executable file requiring no install step; it needs FUSE to run normally,
but also works without it via
`APPIMAGE_EXTRACT_AND_RUN=1 ./spotify-tidal-migrator-x86_64.AppImage`
(e.g. inside containers).

## Development

```bash
pip install -e ".[test,dev]"
pre-commit install   # once, so lint/format run automatically on every commit
```

```bash
pytest                # run the test suite (107 tests, no network/display needed)
ruff check .           # lint
ruff format .          # format
pre-commit run --all-files   # everything pre-commit would run on commit
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full workflow.

## Where things are stored

- Tidal token: `~/.config/spotify-tidal-migrator/`
- Harvested playlist snapshots: `~/.local/share/spotify-tidal-migrator/playlists/`

## Notes

- Only playlists Spotify reports as public on the target profile are listed.
  Spotify's API has occasionally been unreliable about the `public` flag —
  if an expected playlist is missing, double check it's actually public.
- Track matching: exact ISRC lookup first, then a fuzzy artist+title search
  with a duration sanity check. In practice the ISRC path currently never
  fires — Spotify's `fetchPlaylist` response (see above) doesn't include
  ISRCs, so every track goes through fuzzy matching. Unmatched tracks are
  reported per playlist in the log during the Match step, and are not
  silently dropped — Push only adds tracks that were actually matched (or
  that you manually fixed on the Match tab).
- Both services' terms of service restrict automated/unofficial API use;
  this tool is intended for personal library migration, not bulk scraping.
- Pushed playlists land inside a Tidal playlist *folder*, created if it
  doesn't already exist. It's named after the harvested Spotify user by
  default, or the name you type into the Push tab's folder field.
