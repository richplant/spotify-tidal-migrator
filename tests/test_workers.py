from unittest.mock import MagicMock

from spotify_tidal_migrator import store
from spotify_tidal_migrator.gui.workers import (
    ConnectSpotifyWorker,
    ConnectTidalWorker,
    FetchPlaylistsWorker,
    ManualSearchWorker,
    MatchWorker,
    PrefetchPlaylistDetailsWorker,
    PushWorker,
    SaveSnapshotsWorker,
)
from spotify_tidal_migrator.models import (
    PlaylistDetails,
    PlaylistMatchResult,
    PlaylistSnapshot,
    PlaylistSummary,
    PushReport,
    TrackInfo,
)

# Each worker's run() is called directly (never .start()) so it executes
# synchronously on the test thread — no real QThread, no network, no browser.


def test_connect_spotify_worker_success(qapp):
    client = MagicMock()
    client.connect.return_value = {"display_name": "Rich", "id": "rich123"}
    worker = ConnectSpotifyWorker(client, "rich123")

    results = []
    worker.succeeded.connect(results.append)
    worker.run()

    assert results == ["Rich"]


def test_connect_spotify_worker_falls_back_to_id_when_no_display_name(qapp):
    client = MagicMock()
    client.connect.return_value = {"display_name": None, "id": "rich123"}
    worker = ConnectSpotifyWorker(client, "rich123")

    results = []
    worker.succeeded.connect(results.append)
    worker.run()

    assert results == ["rich123"]


def test_connect_spotify_worker_failure(qapp):
    client = MagicMock()
    client.connect.side_effect = RuntimeError("bad token")
    worker = ConnectSpotifyWorker(client, "rich123")

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["bad token"]


def test_fetch_playlists_worker_success(qapp):
    playlists = [
        PlaylistSummary(id="1", name="A", description="", owner="me", track_count=3, public=True)
    ]
    client = MagicMock()
    client.fetch_user_playlists.return_value = playlists
    worker = FetchPlaylistsWorker(client, "123059001")

    results = []
    worker.succeeded.connect(results.append)
    worker.run()

    assert results == [playlists]
    client.fetch_user_playlists.assert_called_once_with("123059001")


def test_fetch_playlists_worker_failure(qapp):
    client = MagicMock()
    client.fetch_user_playlists.side_effect = RuntimeError("network down")
    worker = FetchPlaylistsWorker(client, "123059001")

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["network down"]


def test_save_snapshots_worker_persists_to_store(qapp):
    playlist = PlaylistSummary(
        id="p1", name="My Mix", description="", owner="me", track_count=1, public=True
    )
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["Artist"], album="Album", duration_ms=1000, isrc="X"
    )
    client = MagicMock()
    client.get_playlist_details.return_value = PlaylistDetails(
        description="real description", tracks=[track]
    )
    worker = SaveSnapshotsWorker(client, [playlist])

    progress = []
    successes = []
    worker.progress.connect(progress.append)
    worker.succeeded.connect(successes.append)
    worker.run()

    assert successes == [["My Mix"]]
    assert any("My Mix" in msg for msg in progress)

    saved = store.list_snapshots()
    assert len(saved) == 1
    assert saved[0].name == "My Mix"
    assert saved[0].description == "real description"
    assert saved[0].tracks[0].isrc == "X"


def test_save_snapshots_worker_failure_leaves_nothing_saved(qapp):
    playlist = PlaylistSummary(
        id="p1", name="My Mix", description="", owner="me", track_count=1, public=True
    )
    client = MagicMock()
    client.get_playlist_details.side_effect = RuntimeError("rate limited")
    worker = SaveSnapshotsWorker(client, [playlist])

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["rate limited"]
    assert store.list_snapshots() == []


def test_prefetch_playlist_details_worker_emits_per_playlist(qapp):
    playlists = [
        PlaylistSummary(id="p1", name="A", description="", owner="me", track_count=1, public=True),
        PlaylistSummary(id="p2", name="B", description="", owner="me", track_count=2, public=True),
    ]
    details_by_id = {
        "p1": PlaylistDetails(description="d1", tracks=[]),
        "p2": PlaylistDetails(description="d2", tracks=[]),
    }
    client = MagicMock()
    client.get_playlist_details.side_effect = lambda pid, **kw: details_by_id[pid]
    worker = PrefetchPlaylistDetailsWorker(client, playlists, max_concurrency=2)

    ready = {}
    worker.playlist_ready.connect(lambda pid, details: ready.__setitem__(pid, details))
    worker.run()

    assert ready == details_by_id


def test_prefetch_playlist_details_worker_reports_per_playlist_failure(qapp):
    playlists = [
        PlaylistSummary(id="p1", name="A", description="", owner="me", track_count=1, public=True),
        PlaylistSummary(id="p2", name="B", description="", owner="me", track_count=2, public=True),
    ]

    def fake_get(pid, **kw):
        if pid == "p1":
            raise RuntimeError("boom")
        return PlaylistDetails(description="d2", tracks=[])

    client = MagicMock()
    client.get_playlist_details.side_effect = fake_get
    worker = PrefetchPlaylistDetailsWorker(client, playlists)

    ready = {}
    failures = {}
    worker.playlist_ready.connect(lambda pid, details: ready.__setitem__(pid, details))
    worker.failed_one.connect(lambda pid, msg: failures.__setitem__(pid, msg))
    worker.run()

    assert failures == {"p1": "boom"}
    assert "p2" in ready


def test_manual_search_worker_success(qapp):
    fake_track = object()
    client = MagicMock()
    client.search_tracks.return_value = [fake_track]
    worker = ManualSearchWorker(client, "artist title")

    successes = []
    worker.succeeded.connect(successes.append)
    worker.run()

    assert successes == [[fake_track]]
    client.search_tracks.assert_called_once_with("artist title")


def test_manual_search_worker_failure(qapp):
    client = MagicMock()
    client.search_tracks.side_effect = RuntimeError("search down")
    worker = ManualSearchWorker(client, "query")

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["search down"]


def test_connect_tidal_worker_restores_existing_session_without_login(qapp):
    client = MagicMock()
    client.try_restore.return_value = True
    worker = ConnectTidalWorker(client)

    successes = []
    worker.succeeded.connect(lambda: successes.append(True))
    worker.run()

    assert successes == [True]
    client.login_blocking.assert_not_called()


def test_connect_tidal_worker_logs_in_when_no_saved_session(qapp):
    client = MagicMock()
    client.try_restore.return_value = False

    def fake_login(on_url_ready):
        on_url_ready("https://link.tidal.com/ABCDE")
        return True

    client.login_blocking.side_effect = fake_login
    worker = ConnectTidalWorker(client)

    urls = []
    successes = []
    worker.url_ready.connect(urls.append)
    worker.succeeded.connect(lambda: successes.append(True))
    worker.run()

    assert urls == ["https://link.tidal.com/ABCDE"]
    assert successes == [True]


def test_connect_tidal_worker_login_not_completed(qapp):
    client = MagicMock()
    client.try_restore.return_value = False
    client.login_blocking.return_value = False
    worker = ConnectTidalWorker(client)

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["Login did not complete before the link expired."]


def test_connect_tidal_worker_exception(qapp):
    client = MagicMock()
    client.try_restore.side_effect = RuntimeError("boom")
    worker = ConnectTidalWorker(client)

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["boom"]


def test_match_worker_success(qapp):
    snapshot = PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[])
    client = MagicMock()
    client.match_snapshot.return_value = result
    worker = MatchWorker(client, [snapshot])

    successes = []
    worker.succeeded.connect(successes.append)
    worker.run()

    assert successes == [[result]]


def test_match_worker_emits_playlist_progress_and_playlist_matched_live(qapp):
    """These signals let the UI update as each playlist (and each track
    within it) is matched, instead of going silent until the whole batch
    of selected playlists finishes."""
    snapshot = PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[])

    def fake_match_snapshot(snap, cb):
        track = MagicMock()
        track.title = "Song"
        cb(1, 2, track, True)
        cb(2, 2, track, False)
        return result

    client = MagicMock()
    client.match_snapshot.side_effect = fake_match_snapshot
    worker = MatchWorker(client, [snapshot])

    progress_calls = []
    matched_results = []
    worker.playlist_progress.connect(lambda *a: progress_calls.append(a))
    worker.playlist_matched.connect(matched_results.append)
    worker.run()

    assert progress_calls == [("s1", 1, 2), ("s1", 2, 2)]
    assert matched_results == [result]


def test_match_worker_failure(qapp):
    snapshot = PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    client = MagicMock()
    client.match_snapshot.side_effect = RuntimeError("tidal down")
    worker = MatchWorker(client, [snapshot])

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["tidal down"]


def test_push_worker_success(qapp):
    match_result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[])
    report = PushReport(
        playlist_name="Mix", tidal_playlist_id="tp1", matched=0, total=0, unmatched=[]
    )
    client = MagicMock()
    client.push_matched.return_value = report
    worker = PushWorker(client, [match_result], folder_name="My Folder")

    successes = []
    worker.succeeded.connect(successes.append)
    worker.run()

    assert successes == [[report]]
    client.push_matched.assert_called_once_with(match_result, "My Folder")


def test_push_worker_failure(qapp):
    match_result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[])
    client = MagicMock()
    client.push_matched.side_effect = RuntimeError("create failed")
    worker = PushWorker(client, [match_result])

    errors = []
    worker.failed.connect(errors.append)
    worker.run()

    assert errors == ["create failed"]
