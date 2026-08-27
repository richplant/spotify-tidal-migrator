from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from .. import store
from ..models import (
    PlaylistDetails,
    PlaylistMatchResult,
    PlaylistSnapshot,
    PlaylistSummary,
    PushReport,
)
from ..spotify_client import SpotifyClient
from ..tidal_client import TidalClient


class ConnectSpotifyWorker(QThread):
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, client: SpotifyClient, user_id: str):
        super().__init__()
        self.client = client
        self.user_id = user_id

    def run(self) -> None:
        try:
            profile = self.client.connect(self.user_id)
            self.succeeded.emit(profile.get("display_name") or profile.get("id", "connected"))
        except Exception as exc:  # noqa: BLE001 - surface any auth/network failure to the GUI
            self.failed.emit(str(exc))


class FetchPlaylistsWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: SpotifyClient, user_id: str):
        super().__init__()
        self.client = client
        self.user_id = user_id

    def run(self) -> None:
        try:
            playlists: list[PlaylistSummary] = self.client.fetch_user_playlists(self.user_id)
            self.succeeded.emit(playlists)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SaveSnapshotsWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: SpotifyClient, playlists: list[PlaylistSummary]):
        super().__init__()
        self.client = client
        self.playlists = playlists

    def run(self) -> None:
        saved_names = []
        try:
            for p in self.playlists:
                self.progress.emit(f"Fetching tracks for “{p.name}”…")
                details = self.client.get_playlist_details(p.id)
                snapshot = PlaylistSnapshot(
                    id=p.id,
                    name=p.name,
                    description=details.description,
                    owner=p.owner,
                    tracks=details.tracks,
                )
                store.save_snapshot(snapshot)
                saved_names.append(p.name)
            self.succeeded.emit(saved_names)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class PrefetchPlaylistDetailsWorker(QThread):
    """Fetches every listed playlist's description + tracks in the
    background, with bounded concurrency (not one-at-a-time, not unbounded —
    see spotify_client.SpotifyClient.get_playlist_details), caching results
    on the client so import and later steps reuse them instead of
    re-fetching. Errors on individual playlists are non-fatal."""

    playlist_ready = Signal(str, object)  # (playlist_id, PlaylistDetails)
    failed_one = Signal(str, str)  # (playlist_id, message)

    def __init__(
        self,
        client: SpotifyClient,
        playlists: list[PlaylistSummary],
        max_concurrency: int = 4,
    ):
        super().__init__()
        self.client = client
        self.playlists = playlists
        self.max_concurrency = max_concurrency

    def run(self) -> None:
        with ThreadPoolExecutor(max_workers=self.max_concurrency) as executor:
            future_to_id = {
                executor.submit(self.client.get_playlist_details, p.id): p.id
                for p in self.playlists
            }
            for future in as_completed(future_to_id):
                playlist_id = future_to_id[future]
                try:
                    details: PlaylistDetails = future.result()
                except Exception as exc:  # noqa: BLE001
                    self.failed_one.emit(playlist_id, str(exc))
                else:
                    self.playlist_ready.emit(playlist_id, details)


class ConnectTidalWorker(QThread):
    url_ready = Signal(str)
    succeeded = Signal()
    failed = Signal(str)

    def __init__(self, client: TidalClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            if self.client.try_restore():
                self.succeeded.emit()
                return
            ok = self.client.login_blocking(lambda url: self.url_ready.emit(url))
            if ok:
                self.succeeded.emit()
            else:
                self.failed.emit("Login did not complete before the link expired.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class MatchWorker(QThread):
    progress = Signal(str)
    # Fired after every track within a playlist, so the UI can show live
    # "matching… i/total" progress rather than going silent until the whole
    # playlist (or worse, the whole batch) finishes.
    playlist_progress = Signal(str, int, int)  # snapshot_id, i, total
    # Fired once a playlist finishes, so its row updates immediately instead
    # of waiting for every selected playlist to be done.
    playlist_matched = Signal(object)  # PlaylistMatchResult
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: TidalClient, snapshots: list[PlaylistSnapshot]):
        super().__init__()
        self.client = client
        self.snapshots = snapshots

    def run(self) -> None:
        results: list[PlaylistMatchResult] = []
        try:
            for snap in self.snapshots:
                self.progress.emit(f"Matching “{snap.name}” against Tidal…")

                def cb(i: int, total: int, track, matched: bool, _snap=snap) -> None:
                    status = "matched" if matched else "no match"
                    self.progress.emit(f"  [{_snap.name}] {i}/{total} {track.title} — {status}")
                    self.playlist_progress.emit(_snap.id, i, total)

                result = self.client.match_snapshot(snap, cb)
                results.append(result)
                self.playlist_matched.emit(result)
                self.progress.emit(
                    f"“{snap.name}”: {result.matched_count}/{result.total} tracks matched."
                )
            self.succeeded.emit(results)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class PushWorker(QThread):
    progress = Signal(str)
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(
        self,
        client: TidalClient,
        match_results: list[PlaylistMatchResult],
        folder_name: str | None = None,
    ):
        super().__init__()
        self.client = client
        self.match_results = match_results
        self.folder_name = folder_name

    def run(self) -> None:
        reports: list[PushReport] = []
        try:
            for result in self.match_results:
                self.progress.emit(f"Pushing “{result.name}” to Tidal…")
                report = self.client.push_matched(result, self.folder_name)
                reports.append(report)
                verb = "created" if report.created else "updated"
                self.progress.emit(
                    f"“{result.name}” {verb}: {report.matched}/{report.total} matched, "
                    f"{report.added_count} new track(s) added."
                )
            self.succeeded.emit(reports)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class ManualSearchWorker(QThread):
    succeeded = Signal(list)
    failed = Signal(str)

    def __init__(self, client: TidalClient, query: str):
        super().__init__()
        self.client = client
        self.query = query

    def run(self) -> None:
        try:
            self.succeeded.emit(self.client.search_tracks(self.query))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
