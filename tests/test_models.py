from spotify_tidal_migrator.models import (
    PlaylistMatchResult,
    PlaylistSnapshot,
    TrackInfo,
    TrackMatch,
)


def make_track(spotify_id="t1", isrc="ISRC1"):
    return TrackInfo(
        spotify_id=spotify_id,
        title="Song",
        artists=["A", "B"],
        album="Al",
        duration_ms=1000,
        isrc=isrc,
    )


def test_track_info_artist_display_joins_with_comma():
    track = make_track()
    assert track.artist_display == "A, B"


def test_track_info_artist_display_single_artist():
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["Solo"], album="Al", duration_ms=1, isrc=None
    )
    assert track.artist_display == "Solo"


def test_playlist_snapshot_json_roundtrip_preserves_tracks_and_metadata():
    snapshot = PlaylistSnapshot(
        id="s1", name="Mix", description="desc", owner="me", tracks=[make_track()]
    )

    restored = PlaylistSnapshot.from_json(snapshot.to_json())

    assert restored.id == snapshot.id
    assert restored.name == snapshot.name
    assert restored.description == snapshot.description
    assert restored.owner == snapshot.owner
    assert restored.fetched_at == snapshot.fetched_at
    assert restored.tracks[0] == snapshot.tracks[0]


def test_playlist_snapshot_from_json_defaults_missing_optional_fields():
    restored = PlaylistSnapshot.from_json({"id": "s1", "name": "Mix"})
    assert restored.description == ""
    assert restored.owner == ""
    assert restored.tracks == []


def test_playlist_match_result_counts_matched_and_total():
    matches = [
        TrackMatch(track=make_track("t1"), tidal_id="1", tidal_label="A - Song"),
        TrackMatch(track=make_track("t2"), tidal_id=None, tidal_label=None),
        TrackMatch(track=make_track("t3"), tidal_id="3", tidal_label="A - Song"),
    ]
    result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=matches)

    assert result.matched_count == 2
    assert result.total == 3


def test_playlist_match_result_empty_matches():
    result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[])
    assert result.matched_count == 0
    assert result.total == 0
