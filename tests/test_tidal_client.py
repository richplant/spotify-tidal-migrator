import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import tidalapi

from spotify_tidal_migrator import paths, store
from spotify_tidal_migrator.models import (
    PlaylistMatchResult,
    PlaylistSnapshot,
    TrackInfo,
    TrackMatch,
)
from spotify_tidal_migrator.tidal_client import TidalClient


def make_snapshot(n_tracks=2):
    tracks = [
        TrackInfo(
            spotify_id=f"t{i}",
            title=f"Song {i}",
            artists=["Artist"],
            album="Album",
            duration_ms=200000,
            isrc=f"ISRC{i}",
        )
        for i in range(n_tracks)
    ]
    return PlaylistSnapshot(
        id="snap1", name="My Playlist", description="desc", owner="me", tracks=tracks
    )


def test_try_restore_returns_false_when_no_token_file():
    assert TidalClient().try_restore() is False


def test_try_restore_loads_valid_token(monkeypatch):
    paths.ensure_dirs()
    expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    paths.TIDAL_TOKEN_PATH.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "abc",
                "refresh_token": "def",
                "expiry_time": expiry.isoformat(),
            }
        )
    )
    client = TidalClient()
    monkeypatch.setattr(client.session, "load_oauth_session", MagicMock(return_value=True))
    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=True))

    assert client.try_restore() is True
    kwargs = client.session.load_oauth_session.call_args.kwargs
    assert kwargs["token_type"] == "Bearer"
    assert kwargs["access_token"] == "abc"
    assert kwargs["refresh_token"] == "def"
    assert kwargs["expiry_time"] == datetime.fromisoformat(expiry.isoformat())


def test_try_restore_returns_false_when_session_rejects_token(monkeypatch):
    paths.ensure_dirs()
    paths.TIDAL_TOKEN_PATH.write_text(
        json.dumps(
            {
                "token_type": "Bearer",
                "access_token": "abc",
                "refresh_token": None,
                "expiry_time": None,
            }
        )
    )
    client = TidalClient()
    monkeypatch.setattr(client.session, "load_oauth_session", MagicMock(return_value=False))
    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=False))

    assert client.try_restore() is False


def test_try_restore_returns_false_on_corrupt_token_file():
    paths.ensure_dirs()
    paths.TIDAL_TOKEN_PATH.write_text("not json")

    assert TidalClient().try_restore() is False


def test_try_restore_returns_false_on_token_file_missing_keys():
    paths.ensure_dirs()
    paths.TIDAL_TOKEN_PATH.write_text(json.dumps({"token_type": "Bearer"}))

    assert TidalClient().try_restore() is False


def test_login_blocking_persists_token_on_success(monkeypatch):
    client = TidalClient()

    link = MagicMock(verification_uri_complete="https://link.tidal.com/XYZ")
    future = MagicMock()
    monkeypatch.setattr(client.session, "login_oauth", MagicMock(return_value=(link, future)))
    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=True))
    client.session.token_type = "Bearer"
    client.session.access_token = "tok"
    client.session.refresh_token = "ref"
    client.session.expiry_time = datetime.now(timezone.utc)

    seen_urls = []
    result = client.login_blocking(seen_urls.append)

    assert result is True
    assert seen_urls == ["https://link.tidal.com/XYZ"]
    future.result.assert_called_once()
    saved = json.loads(paths.TIDAL_TOKEN_PATH.read_text())
    assert saved["access_token"] == "tok"
    assert saved["token_type"] == "Bearer"


def test_login_blocking_returns_false_and_does_not_persist_when_login_incomplete(monkeypatch):
    client = TidalClient()
    link = MagicMock(verification_uri_complete="https://link.tidal.com/XYZ")
    future = MagicMock()
    monkeypatch.setattr(client.session, "login_oauth", MagicMock(return_value=(link, future)))
    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=False))

    result = client.login_blocking(lambda url: None)

    assert result is False
    assert not paths.TIDAL_TOKEN_PATH.exists()


def test_is_connected_reflects_session_check_login(monkeypatch):
    client = TidalClient()
    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=True))
    assert client.is_connected is True

    monkeypatch.setattr(client.session, "check_login", MagicMock(return_value=False))
    assert client.is_connected is False


def test_match_snapshot_builds_result_and_reports_progress(monkeypatch):
    client = TidalClient()
    snapshot = make_snapshot(n_tracks=2)

    fake_match = MagicMock()
    fake_match.id = 999
    fake_match.name = "Matched Title"
    fake_match.artists = [SimpleNamespace(name="Someone")]

    def fake_match_track(session, track):
        return fake_match if track.spotify_id == "t0" else None

    monkeypatch.setattr("spotify_tidal_migrator.tidal_client.match_track", fake_match_track)

    progress_calls = []
    result = client.match_snapshot(snapshot, progress_cb=lambda *a: progress_calls.append(a))

    assert result.snapshot_id == "snap1"
    assert result.matched_count == 1
    assert result.total == 2
    assert result.matches[0].tidal_id == "999"
    assert result.matches[0].tidal_label == "Someone - Matched Title"
    assert result.matches[1].tidal_id is None
    assert result.owner == "me"

    assert len(progress_calls) == 2
    assert progress_calls[0][0:2] == (1, 2)
    assert progress_calls[0][3] is True
    assert progress_calls[1][0:2] == (2, 2)
    assert progress_calls[1][3] is False


def test_match_snapshot_works_without_progress_callback(monkeypatch):
    client = TidalClient()
    snapshot = make_snapshot(n_tracks=1)
    monkeypatch.setattr(
        "spotify_tidal_migrator.tidal_client.match_track", lambda session, track: None
    )

    result = client.match_snapshot(snapshot)

    assert result.matched_count == 0
    assert result.total == 1


def _mock_folder(folder_id="folder-1", folder_name="rich"):
    folder = MagicMock()
    folder.id = folder_id
    folder.name = folder_name
    return folder


def _mock_playlist(playlist_id="tidal-playlist-1", playlist_name=None):
    playlist = MagicMock()
    playlist.id = playlist_id
    if playlist_name is not None:
        playlist.name = playlist_name
    # Mirrors Tidal's real onDupes=SKIP behavior: adding returns whatever was added.
    playlist.add.side_effect = lambda ids: list(ids)
    return playlist


def test_push_matched_creates_folder_and_playlist_when_neither_exist():
    client = TidalClient()
    tracks = [
        TrackInfo(
            spotify_id=f"t{i}",
            title=f"S{i}",
            artists=["A"],
            album="Al",
            duration_ms=1000,
            isrc=None,
        )
        for i in range(3)
    ]
    matches = [
        TrackMatch(track=tracks[0], tidal_id="100", tidal_label="A - S0"),
        TrackMatch(track=tracks[1], tidal_id=None, tidal_label=None),
        TrackMatch(track=tracks[2], tidal_id="102", tidal_label="A - S2"),
    ]
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="desc", matches=matches, owner="rich"
    )

    new_folder = _mock_folder(folder_id="new-folder", folder_name="rich")
    new_playlist = _mock_playlist(playlist_id="tidal-playlist-1")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = []
    client.session.user.create_folder.return_value = new_folder
    new_folder.items.return_value = []
    client.session.user.create_playlist.return_value = new_playlist

    report = client.push_matched(match_result)

    client.session.user.create_folder.assert_called_once_with("rich")
    client.session.user.create_playlist.assert_called_once_with(
        "My Playlist", "desc", parent_id="new-folder"
    )
    new_playlist.set_playlist_public.assert_called_once()
    new_playlist.add.assert_called_once_with(["100", "102"])
    assert report.created is True
    assert report.added_count == 2
    assert report.playlist_name == "My Playlist"
    assert report.tidal_playlist_id == "tidal-playlist-1"
    assert report.matched == 2
    assert report.total == 3
    assert [t.spotify_id for t in report.unmatched] == ["t1"]


def test_push_matched_converts_html_description_to_plain_text_for_tidal():
    """result.description carries Spotify's raw HTML (rendered as rich text
    in the GUI) — Tidal's description field is plain text only, so
    push_matched must convert it, not pass the raw markup through."""
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1",
        name="My Playlist",
        description='Rock &amp; Roll feat. <a href="spotify:artist:1">Someone</a>',
        matches=[],
        owner="rich",
    )

    new_folder = _mock_folder(folder_id="new-folder", folder_name="rich")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = []
    client.session.user.create_folder.return_value = new_folder
    new_folder.items.return_value = []
    client.session.user.create_playlist.return_value = _mock_playlist()

    client.push_matched(match_result)

    client.session.user.create_playlist.assert_called_once_with(
        "My Playlist", "Rock & Roll feat. Someone", parent_id="new-folder"
    )


def test_push_matched_reuses_existing_folder_and_playlist_idempotently():
    client = TidalClient()
    tracks = [
        TrackInfo(
            spotify_id="t0", title="S0", artists=["A"], album="Al", duration_ms=1000, isrc=None
        )
    ]
    matches = [TrackMatch(track=tracks[0], tidal_id="100", tidal_label="A - S0")]
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="desc", matches=matches, owner="rich"
    )

    existing_folder = _mock_folder(folder_id="folder-1", folder_name="rich")
    existing_playlist = _mock_playlist(playlist_id="tidal-playlist-1", playlist_name="My Playlist")
    # Tidal already has this track -> onDupes=SKIP means nothing new gets added.
    existing_playlist.add.side_effect = None
    existing_playlist.add.return_value = []
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [existing_folder]
    existing_folder.items.return_value = [existing_playlist]

    report = client.push_matched(match_result)

    client.session.user.create_folder.assert_not_called()
    client.session.user.create_playlist.assert_not_called()
    existing_playlist.set_playlist_public.assert_not_called()
    existing_playlist.add.assert_called_once_with(["100"])
    assert report.created is False
    assert report.added_count == 0
    assert report.tidal_playlist_id == "tidal-playlist-1"


def test_push_matched_records_a_local_mapping_after_creating_a_playlist():
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="", matches=[], owner="rich"
    )
    new_folder = _mock_folder(folder_id="new-folder", folder_name="rich")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = []
    client.session.user.create_folder.return_value = new_folder
    new_folder.items.return_value = []
    client.session.user.create_playlist.return_value = _mock_playlist(
        playlist_id="tidal-playlist-1"
    )

    client.push_matched(match_result)

    assert store.get_push_mapping("snap1") == "tidal-playlist-1"


def test_push_matched_reuses_playlist_by_stored_mapping_without_searching_by_name():
    """Regression test: matching purely by name within the target folder
    risks silently merging into an unrelated, pre-existing Tidal playlist
    that happens to share a common name (e.g. "Chill"). A snapshot that was
    already pushed once must be found again by the id recorded from that
    push, not by re-searching for a name match."""
    client = TidalClient()
    store.save_push_mapping("snap1", "tidal-playlist-1")
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="", matches=[], owner="rich"
    )
    mapped_playlist = MagicMock(spec=tidalapi.playlist.UserPlaylist)
    mapped_playlist.id = "tidal-playlist-1"
    mapped_playlist.add.side_effect = lambda ids: list(ids)
    client.session.playlist = MagicMock(return_value=mapped_playlist)
    existing_folder = _mock_folder(folder_id="folder-1", folder_name="rich")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [existing_folder]

    report = client.push_matched(match_result)

    client.session.playlist.assert_called_once_with("tidal-playlist-1")
    # The folder itself is still looked up (needed either way), but its
    # contents are never searched by name -- the mapping already resolved
    # the exact playlist.
    existing_folder.items.assert_not_called()
    client.session.user.create_playlist.assert_not_called()
    mapped_playlist.set_playlist_public.assert_not_called()
    assert report.created is False
    assert report.tidal_playlist_id == "tidal-playlist-1"


def test_push_matched_falls_back_to_name_search_when_mapped_playlist_was_deleted():
    client = TidalClient()
    store.save_push_mapping("snap1", "deleted-playlist")
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="", matches=[], owner="rich"
    )
    client.session.playlist = MagicMock(side_effect=tidalapi.exceptions.ObjectNotFound)
    existing_folder = _mock_folder(folder_id="folder-1", folder_name="rich")
    existing_playlist = _mock_playlist(playlist_id="tidal-playlist-2", playlist_name="My Playlist")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [existing_folder]
    existing_folder.items.return_value = [existing_playlist]

    report = client.push_matched(match_result)

    assert report.tidal_playlist_id == "tidal-playlist-2"
    # Re-recorded against the playlist that was actually found this time.
    assert store.get_push_mapping("snap1") == "tidal-playlist-2"


def test_push_matched_folder_name_override_takes_precedence_over_owner():
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="", matches=[], owner="rich"
    )
    override_folder = _mock_folder(folder_id="folder-2", folder_name="Custom Folder")
    new_playlist = _mock_playlist(playlist_id="tidal-playlist-2")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [override_folder]
    override_folder.items.return_value = []
    client.session.user.create_playlist.return_value = new_playlist

    client.push_matched(match_result, folder_name="Custom Folder")

    client.session.user.create_folder.assert_not_called()
    client.session.user.create_playlist.assert_called_once_with(
        "My Playlist", "", parent_id="folder-2"
    )


def test_push_matched_paginates_folder_lookup_past_fifty():
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="Mix", description="", matches=[], owner="rich"
    )

    page1 = [_mock_folder(folder_id=f"f{i}", folder_name=f"other{i}") for i in range(50)]
    target_folder = _mock_folder(folder_id="target", folder_name="rich")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.side_effect = [page1, [target_folder]]
    target_folder.items.return_value = []
    client.session.user.create_playlist.return_value = _mock_playlist()

    client.push_matched(match_result)

    assert client.session.user.favorites.playlist_folders.call_count == 2
    client.session.user.create_folder.assert_not_called()


def test_push_matched_paginates_playlist_lookup_within_folder_past_fifty():
    """Regression test: Folder.items() shares its hard 50-item cap with
    playlist_folders() (same underlying endpoint) -- passing a higher limit
    404s/400s. Every call here must ask for <=50 items per page."""
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="My Playlist", description="", matches=[], owner="rich"
    )

    folder = _mock_folder(folder_id="folder-1", folder_name="rich")
    page1 = [_mock_playlist(playlist_id=f"p{i}", playlist_name=f"Other {i}") for i in range(50)]
    target_playlist = _mock_playlist(playlist_id="target", playlist_name="My Playlist")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [folder]
    folder.items.side_effect = [page1, [target_playlist]]

    client.push_matched(match_result)

    for call in folder.items.call_args_list:
        assert call.kwargs["limit"] <= 50
    assert folder.items.call_count == 2
    client.session.user.create_playlist.assert_not_called()


def test_push_matched_chunks_large_track_lists_into_batches_of_50():
    client = TidalClient()
    tracks = [
        TrackInfo(
            spotify_id=f"t{i}",
            title=f"S{i}",
            artists=["A"],
            album="Al",
            duration_ms=1000,
            isrc=None,
        )
        for i in range(120)
    ]
    matches = [TrackMatch(track=t, tidal_id=str(i), tidal_label="x") for i, t in enumerate(tracks)]
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="Big", description="", matches=matches, owner="rich"
    )

    folder = _mock_folder()
    playlist = _mock_playlist(playlist_id="tidal-playlist-2")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [folder]
    folder.items.return_value = []
    client.session.user.create_playlist.return_value = playlist

    report = client.push_matched(match_result)

    assert playlist.add.call_count == 3
    call_sizes = [len(c.args[0]) for c in playlist.add.call_args_list]
    assert call_sizes == [50, 50, 20]
    assert report.added_count == 120


def test_push_matched_with_no_matches_creates_playlist_but_does_not_call_add():
    client = TidalClient()
    match_result = PlaylistMatchResult(
        snapshot_id="snap1", name="Empty", description="", matches=[], owner="rich"
    )

    folder = _mock_folder()
    playlist = _mock_playlist(playlist_id="tidal-playlist-3")
    client.session.user = MagicMock()
    client.session.user.favorites.playlist_folders.return_value = [folder]
    folder.items.return_value = []
    client.session.user.create_playlist.return_value = playlist

    report = client.push_matched(match_result)

    playlist.add.assert_not_called()
    assert report.matched == 0
    assert report.total == 0
    assert report.created is True
    assert report.added_count == 0


def test_search_tracks_returns_track_results():
    client = TidalClient()
    fake_track = MagicMock()
    client.session.search = MagicMock(return_value={"tracks": [fake_track]})

    results = client.search_tracks("query", limit=10)

    assert results == [fake_track]
    args, kwargs = client.session.search.call_args
    assert args[0] == "query"
    assert kwargs["limit"] == 10


def test_search_tracks_returns_empty_list_when_no_tracks_key():
    client = TidalClient()
    client.session.search = MagicMock(return_value={})

    assert client.search_tracks("query") == []
