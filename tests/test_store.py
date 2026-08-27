import pytest

from spotify_tidal_migrator import paths, store
from spotify_tidal_migrator.models import PlaylistSnapshot, TrackInfo


def make_snapshot(snapshot_id="s1", name="Mix"):
    return PlaylistSnapshot(
        id=snapshot_id,
        name=name,
        description="desc",
        owner="me",
        tracks=[
            TrackInfo(
                spotify_id="t1",
                title="Song",
                artists=["A"],
                album="Al",
                duration_ms=1000,
                isrc="X",
            )
        ],
    )


def test_list_snapshots_empty_when_store_dir_has_nothing():
    assert store.list_snapshots() == []


def test_save_and_list_snapshot_roundtrip():
    store.save_snapshot(make_snapshot())

    snapshots = store.list_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].name == "Mix"
    assert snapshots[0].tracks[0].title == "Song"


def test_save_snapshot_overwrites_existing_id():
    store.save_snapshot(make_snapshot(snapshot_id="s1", name="Old Name"))
    store.save_snapshot(make_snapshot(snapshot_id="s1", name="New Name"))

    snapshots = store.list_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0].name == "New Name"


def test_list_snapshots_ignores_corrupt_files():
    store.save_snapshot(make_snapshot())
    paths.ensure_dirs()
    (paths.PLAYLIST_STORE_DIR / "broken.json").write_text("not json")

    snapshots = store.list_snapshots()

    assert len(snapshots) == 1


def test_list_snapshots_ignores_files_missing_required_keys():
    store.save_snapshot(make_snapshot())
    paths.ensure_dirs()
    (paths.PLAYLIST_STORE_DIR / "incomplete.json").write_text('{"description": "no id or name"}')

    snapshots = store.list_snapshots()

    assert len(snapshots) == 1


def test_delete_snapshot_removes_file():
    store.save_snapshot(make_snapshot(snapshot_id="s1"))

    store.delete_snapshot("s1")

    assert store.list_snapshots() == []


def test_delete_snapshot_missing_file_does_not_raise():
    store.delete_snapshot("does-not-exist")


def test_save_snapshot_rejects_path_traversal_id(tmp_path, monkeypatch):
    """snapshot.id comes from parsing an undocumented, schema-unverified
    Spotify response -- it must never be trusted to build a filesystem path
    without validation, or a crafted id could write outside the store dir."""
    monkeypatch.setattr(paths, "PLAYLIST_STORE_DIR", tmp_path / "playlists")

    with pytest.raises(ValueError):
        store.save_snapshot(make_snapshot(snapshot_id="../../evil"))

    assert not (tmp_path / "evil.json").exists()


def test_delete_snapshot_rejects_path_traversal_id():
    with pytest.raises(ValueError):
        store.delete_snapshot("../../evil")
