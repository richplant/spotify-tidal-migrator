from unittest.mock import MagicMock

from spotify_tidal_migrator.spotify_client import SpotifyClient


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field(field_no: int, wire_type: int, payload: bytes) -> bytes:
    return _varint((field_no << 3) | wire_type) + payload


def _str_field(field_no: int, value: str) -> bytes:
    encoded = value.encode("utf-8")
    return _field(field_no, 2, _varint(len(encoded)) + encoded)


def _varint_field(field_no: int, value: int) -> bytes:
    return _field(field_no, 0, _varint(value))


def _encode_playlist_entry(
    uri: str, name: str, follower_count: int, owner_name: str, owner_uri: str
) -> bytes:
    # Field 4 is the playlist's *follower* count (verified against real
    # captured responses -- see the comment in _parse_profile_playlists),
    # not a track count. Included here for realism; nothing reads it.
    entry = (
        _str_field(1, uri)
        + _str_field(2, name)
        + _varint_field(4, follower_count)
        + _str_field(5, owner_name)
        + _str_field(6, owner_uri)
    )
    return _field(1, 2, _varint(len(entry)) + entry)


def _mock_response(*, content: bytes = b"", json_data=None, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


def test_connect_returns_owner_name_from_first_playlist_page():
    client = SpotifyClient("tok", "client-tok")
    protobuf = _encode_playlist_entry(
        "spotify:playlist:p1", "Chill", 10, "Rich", "spotify:user:rich123"
    )
    client._session = MagicMock()
    client._session.get.return_value = _mock_response(content=protobuf)

    profile = client.connect("rich123")

    assert profile == {"display_name": "Rich", "id": "rich123"}
    client._session.get.assert_called_once()
    _, kwargs = client._session.get.call_args
    assert kwargs["params"]["offset"] == 0
    assert kwargs["params"]["limit"] == 1


def test_connect_falls_back_to_user_id_when_no_playlists():
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.get.return_value = _mock_response(content=b"")

    profile = client.connect("rich123")

    assert profile == {"display_name": "rich123", "id": "rich123"}


def test_fetch_user_playlists_paginates_and_maps_fields():
    client = SpotifyClient("tok", "client-tok")
    page1 = _encode_playlist_entry(
        "spotify:playlist:p1", "Chill", 10, "Rich", "spotify:user:rich123"
    )
    page2 = _encode_playlist_entry("spotify:playlist:p2", "Focus", 5, "", "spotify:user:rich123")
    client._session = MagicMock()
    client._session.get.side_effect = [
        _mock_response(content=page1 * 200),  # full page -> triggers another fetch
        _mock_response(content=page2),  # short page -> stops pagination
    ]

    playlists = client.fetch_user_playlists("rich123")

    assert len(playlists) == 201
    assert playlists[0].id == "p1"
    assert playlists[0].name == "Chill"
    assert playlists[0].owner == "Rich"
    # Not sourced from this listing response (see _parse_profile_playlists) --
    # HarvestTab fills in the real count later from the per-playlist fetch.
    assert playlists[0].track_count == 0
    assert playlists[0].public is True
    assert playlists[-1].id == "p2"
    assert playlists[-1].owner == "rich123"  # falls back to owner uri when display name is empty


def test_fetch_user_playlists_single_short_page_stops_pagination():
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.get.return_value = _mock_response(content=b"")

    playlists = client.fetch_user_playlists("rich123")

    assert playlists == []
    client._session.get.assert_called_once()


def _track_item(track_id: str, name: str, artists: list[str], album: str, duration_ms: int):
    return {
        "itemV2": {
            "data": {
                "mediaType": "AUDIO",
                "uri": f"spotify:track:{track_id}",
                "name": name,
                "artists": {"items": [{"profile": {"name": a}} for a in artists]},
                "albumOfTrack": {"name": album},
                "trackDuration": {"totalMilliseconds": duration_ms},
            }
        }
    }


def _playlist_response(items, description=None):
    playlist = {"content": {"items": items}}
    if description is not None:
        playlist["description"] = description
    return _mock_response(json_data={"data": {"playlistV2": playlist}})


def test_get_playlist_details_skips_non_audio_items_and_paginates():
    page1_items = [
        _track_item(f"t{i}", f"Song {i}", ["Artist A"], "Album A", 200000) for i in range(100)
    ]
    episode_item = {
        "itemV2": {
            "data": {"mediaType": "PODCAST_EPISODE", "uri": "spotify:episode:e1", "name": "Ep"}
        }
    }
    page2_items = [
        _track_item("t100", "Song 100", ["Artist B", "Artist C"], "Album B", 180000),
        episode_item,
        {"itemV2": {"data": None}},
    ]

    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.post.side_effect = [
        _playlist_response(page1_items, description="A great mix"),
        _playlist_response(page2_items),
    ]

    details = client.get_playlist_details("playlist1")

    assert details.description == "A great mix"
    assert len(details.tracks) == 101
    assert details.tracks[0].spotify_id == "t0"
    assert details.tracks[0].isrc is None
    assert details.tracks[-1].spotify_id == "t100"
    assert details.tracks[-1].artists == ["Artist B", "Artist C"]
    assert details.tracks[-1].album == "Album B"
    assert client._session.post.call_count == 2
    first_call_body = client._session.post.call_args_list[0].kwargs["json"]
    assert first_call_body["operationName"] == "fetchPlaylist"
    assert first_call_body["variables"]["uri"] == "spotify:playlist:playlist1"


def test_get_playlist_details_preserves_raw_html_in_description():
    """Spotify descriptions come back as real HTML (entities, <a href="..."> tags
    for tagged artists/tracks) — kept as-is here and rendered as rich text in
    the GUI, only converted to plain text at consumers that need it (Tidal push)."""
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    raw = 'Rock &amp; Roll mix feat. <a href="spotify:artist:123">Some Artist</a> &#39;n friends'
    client._session.post.return_value = _playlist_response([], description=raw)

    details = client.get_playlist_details("playlist1")

    assert details.description == raw


def test_get_playlist_details_defaults_description_when_absent():
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.post.return_value = _playlist_response([])

    details = client.get_playlist_details("playlist1")

    assert details.description == ""
    assert details.tracks == []


def test_get_playlist_details_caches_and_skips_network_on_repeat_call():
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.post.return_value = _playlist_response(
        [_track_item("t0", "Song", ["A"], "Al", 1000)], description="desc"
    )

    first = client.get_playlist_details("playlist1")
    second = client.get_playlist_details("playlist1")

    assert first is second
    client._session.post.assert_called_once()


def test_get_playlist_details_force_refresh_bypasses_cache():
    client = SpotifyClient("tok", "client-tok")
    client._session = MagicMock()
    client._session.post.return_value = _playlist_response([], description="desc")

    client.get_playlist_details("playlist1")
    client.get_playlist_details("playlist1", force_refresh=True)

    assert client._session.post.call_count == 2
