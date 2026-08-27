from __future__ import annotations

import contextlib
import json
import os
import stat
from collections.abc import Callable
from datetime import datetime

import tidalapi

from . import paths, store
from .matching import match_track
from .models import PlaylistMatchResult, PlaylistSnapshot, PushReport, TrackInfo, TrackMatch
from .utils import bounded_pages, html_to_plain_text

MatchProgressCallback = Callable[[int, int, TrackInfo, bool], None]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class TidalClient:
    def __init__(self):
        self.session = tidalapi.Session()

    @property
    def is_connected(self) -> bool:
        return bool(self.session.check_login())

    def try_restore(self) -> bool:
        """Attempt to restore a session from a previously saved token."""
        if not paths.TIDAL_TOKEN_PATH.exists():
            return False
        try:
            data = json.loads(paths.TIDAL_TOKEN_PATH.read_text())
            ok = self.session.load_oauth_session(
                token_type=data["token_type"],
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                expiry_time=(
                    datetime.fromisoformat(data["expiry_time"]) if data.get("expiry_time") else None
                ),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return False
        return bool(ok and self.session.check_login())

    def login_blocking(self, on_url_ready: Callable[[str], None]) -> bool:
        """Blocking: run from a worker thread, not the GUI thread.

        Starts the OAuth device-link flow, hands the verification URL to
        `on_url_ready` (so the caller can display/open it), then blocks on
        the returned future until the user completes login or it expires.
        """
        login, future = self.session.login_oauth()
        on_url_ready(login.verification_uri_complete)
        future.result()
        if self.session.check_login():
            self._persist_token()
            return True
        return False

    def _persist_token(self) -> None:
        paths.ensure_dirs()
        data = {
            "token_type": self.session.token_type,
            "access_token": self.session.access_token,
            "refresh_token": self.session.refresh_token,
            "expiry_time": (
                self.session.expiry_time.isoformat() if self.session.expiry_time else None
            ),
        }
        paths.TIDAL_TOKEN_PATH.write_text(json.dumps(data))
        # Best-effort: restrict to owner-only, since this file holds a
        # live OAuth refresh token in plaintext. No-op on platforms where
        # os.chmod doesn't apply (e.g. Windows ACLs aren't POSIX modes).
        with contextlib.suppress(OSError):
            os.chmod(paths.TIDAL_TOKEN_PATH, stat.S_IRUSR | stat.S_IWUSR)

    def _match_one(self, track: TrackInfo) -> TrackMatch:
        tidal_track = match_track(self.session, track)
        if tidal_track is None:
            return TrackMatch(track=track, tidal_id=None, tidal_label=None)
        artists = ", ".join(a.name for a in (tidal_track.artists or []))
        return TrackMatch(
            track=track,
            tidal_id=str(tidal_track.id),
            tidal_label=f"{artists} - {tidal_track.name}",
        )

    def match_snapshot(
        self, snapshot: PlaylistSnapshot, progress_cb: MatchProgressCallback | None = None
    ) -> PlaylistMatchResult:
        """Search Tidal for a match for every track in the snapshot. Does not
        create or modify anything on Tidal — this is a dry-run for review."""
        total = len(snapshot.tracks)
        matches: list[TrackMatch] = []
        for i, track in enumerate(snapshot.tracks, start=1):
            match = self._match_one(track)
            matches.append(match)
            if progress_cb:
                progress_cb(i, total, track, match.tidal_id is not None)

        return PlaylistMatchResult(
            snapshot_id=snapshot.id,
            name=snapshot.name,
            description=snapshot.description,
            matches=matches,
            owner=snapshot.owner,
        )

    def search_tracks(self, query: str, limit: int = 25) -> list[tidalapi.media.Track]:
        """Searches Tidal for tracks matching a free-text query — used for
        manual match overrides when the automatic ISRC/fuzzy match is wrong
        or missing."""
        results = self.session.search(query, models=[tidalapi.media.Track], limit=limit)
        return results.get("tracks") or []

    def _find_or_create_folder(self, name: str) -> tidalapi.playlist.Folder:
        offset, limit = 0, 50  # 50 is a hard server cap for this endpoint
        for _ in bounded_pages("Folder listing"):
            page = self.session.user.favorites.playlist_folders(offset=offset, limit=limit)
            for folder in page:
                if folder.name == name:
                    return folder
            if len(page) < limit:
                break
            offset += limit
        return self.session.user.create_folder(name)

    def _find_playlist_in_folder(
        self, folder: tidalapi.playlist.Folder, name: str
    ) -> tidalapi.playlist.UserPlaylist | None:
        # Folder.items() hits the same my-collection/playlists/folders
        # endpoint as playlist_folders() above (just includeOnly=PLAYLIST
        # instead of FOLDER) and shares its hard 50-item server-side cap —
        # a higher limit here 400s.
        offset, limit = 0, 50
        for _ in bounded_pages("Playlist listing in folder"):
            page = folder.items(offset=offset, limit=limit)
            for item in page:
                if item.name == name:
                    return item
            if len(page) < limit:
                break
            offset += limit
        return None

    def _find_by_stored_mapping(self, snapshot_id: str) -> tidalapi.playlist.UserPlaylist | None:
        # A previous push for this exact snapshot recorded which Tidal
        # playlist it created (see push_matched) -- looking it up by id
        # here is unambiguous, unlike matching purely by name (see
        # _find_playlist_in_folder), which risks silently merging into an
        # unrelated, pre-existing Tidal playlist that happens to share a
        # common name (e.g. "Chill").
        tidal_id = store.get_push_mapping(snapshot_id)
        if tidal_id is None:
            return None
        try:
            playlist = self.session.playlist(tidal_id)
        except tidalapi.exceptions.ObjectNotFound:
            return None  # deleted on Tidal's side since the last push
        return playlist if isinstance(playlist, tidalapi.playlist.UserPlaylist) else None

    def _get_or_create_playlist(
        self, folder: tidalapi.playlist.Folder, result: PlaylistMatchResult
    ) -> tuple[tidalapi.playlist.UserPlaylist, bool]:
        existing = self._find_by_stored_mapping(
            result.snapshot_id
        ) or self._find_playlist_in_folder(folder, result.name)
        if existing is not None:
            store.save_push_mapping(result.snapshot_id, str(existing.id))
            return existing, False

        # result.description carries Spotify's raw HTML (rendered as rich
        # text in the GUI); Tidal's description field is plain text only,
        # so convert it here rather than at the source.
        playlist = self.session.user.create_playlist(
            result.name, html_to_plain_text(result.description or ""), parent_id=folder.id
        )
        playlist.set_playlist_public()
        store.save_push_mapping(result.snapshot_id, str(playlist.id))
        return playlist, True

    def push_matched(
        self, result: PlaylistMatchResult, folder_name: str | None = None
    ) -> PushReport:
        """Create or update a Tidal playlist from an already-computed match
        result, inside a folder named after the harvested user (or
        `folder_name`, if given, to override that). Idempotent: re-pushing an
        unchanged result adds nothing new — Tidal's own add-tracks endpoint
        already skips duplicates, so pushing twice in a row is safe."""
        folder = self._find_or_create_folder(folder_name or result.owner)
        playlist, created = self._get_or_create_playlist(folder, result)

        matched_ids = [m.tidal_id for m in result.matches if m.tidal_id is not None]
        added_count = sum(len(playlist.add(chunk)) for chunk in _chunks(matched_ids, 50))

        return PushReport(
            playlist_name=result.name,
            tidal_playlist_id=playlist.id,
            matched=len(matched_ids),
            total=result.total,
            unmatched=[m.track for m in result.matches if m.tidal_id is None],
            created=created,
            added_count=added_count,
        )
