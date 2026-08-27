from __future__ import annotations

from urllib.parse import quote

import requests

from .models import PlaylistDetails, PlaylistSummary, TrackInfo
from .utils import bounded_pages

# Spotify's web player no longer drives playlist browsing through the public
# api.spotify.com Web API — as of 2026 it reads a profile's public playlists
# from spclient.wg.spotify.com (protobuf) and a playlist's tracks from
# api-partner.spotify.com's GraphQL "pathfinder" API, both of which require
# *two* credentials alongside each other: the OAuth-ish bearer token *and* a
# separate `client-token` (minted by the web player via its own signed
# handshake with clienttoken.spotify.com). Both are pasted from DevTools the
# same way, and both are short-lived (~1hr) like the old single token was.
_PROFILE_PLAYLISTS_URL = (
    "https://spclient.wg.spotify.com/user-profile-view/v3/profile/{user_id}/playlists"
)
_PATHFINDER_URL = "https://api-partner.spotify.com/pathfinder/v2/query"

# Persisted-query hash for the "fetchPlaylist" GraphQL operation, captured
# from a live open.spotify.com session. Spotify can rotate these when it
# changes the underlying query — if playlist-track fetching starts failing
# with a "PersistedQueryNotFound" style error, this needs to be re-captured
# from DevTools (Network tab, filter "pathfinder", open a playlist, copy the
# request body's extensions.persistedQuery.sha256Hash).
_FETCH_PLAYLIST_HASH = "86dde7b9d9356e2369414647cf6950cfed96e778e129cfdfc99aea6c1613b3b0"

# Only affects track availability/relinking, not whether playlists are found.
_MARKET = "US"

# Without this, requests has no default timeout at all -- a stalled/slow
# server would hang the calling worker thread indefinitely, leaving its
# triggering button disabled forever with no error surfaced.
_REQUEST_TIMEOUT_S = 30

_COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en",
    "app-platform": "WebPlayer",
    "spotify-app-version": "1.2.99.219.g5a940353-development",
    "Origin": "https://open.spotify.com",
    "Referer": "https://open.spotify.com/",
}


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _iter_protobuf_fields(buf: bytes):
    """Generic protobuf wire-format walker — yields (field_number, raw_value).

    No .proto schema is published for this endpoint, so fields are addressed
    by number (inferred from a captured response) rather than by name.
    """
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        field_no, wire_type = tag >> 3, tag & 0x7
        if wire_type == 0:
            value, pos = _read_varint(buf, pos)
        elif wire_type == 2:
            length, pos = _read_varint(buf, pos)
            value = buf[pos : pos + length]
            pos += length
        elif wire_type == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        elif wire_type == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        else:
            raise ValueError(f"unsupported protobuf wire type {wire_type}")
        yield field_no, value


def _parse_profile_playlists(raw: bytes) -> list[dict]:
    # Field 4 here was originally assumed to be a track count (untested
    # guess from the field's position). Verified against real captured
    # responses: it's the playlist's *follower* count, not its track count
    # -- e.g. a playlist literally named "100 Undeniable Classics from the
    # 1970s" had field 4 = 346, and a personal account's own playlists had
    # it entirely absent (proto3 omits zero-valued fields), which makes
    # sense for near-zero followers but not for playlists someone actually
    # uses. This listing response has no reliable track count at all --
    # see HarvestTab, which fills the real count in asynchronously from
    # the per-playlist track fetch instead of trusting anything from here.
    entries = []
    for field_no, value in _iter_protobuf_fields(raw):
        if field_no != 1:
            continue
        fields = dict(_iter_protobuf_fields(value))
        # One entry missing an expected field (or containing bytes that
        # aren't valid UTF-8) shouldn't take down every other playlist in
        # the page with it -- this schema is inferred, not guaranteed.
        try:
            entries.append(
                {
                    "uri": fields[1].decode("utf-8"),
                    "name": fields[2].decode("utf-8"),
                    "owner_name": fields.get(5, b"").decode("utf-8"),
                    "owner_uri": fields.get(6, b"").decode("utf-8"),
                }
            )
        except (KeyError, UnicodeDecodeError):
            continue
    return entries


def _is_playable_track(track: dict | None) -> bool:
    if not track or track.get("mediaType") != "AUDIO":
        return False
    uri = track.get("uri", "")
    return uri.startswith("spotify:track:") and bool(track.get("name"))


def _parse_track_item(item: dict) -> TrackInfo | None:
    track = (item.get("itemV2") or {}).get("data")
    if not _is_playable_track(track):
        return None
    uri = track["uri"]
    return TrackInfo(
        spotify_id=uri.removeprefix("spotify:track:"),
        title=track["name"],
        artists=[a["profile"]["name"] for a in (track.get("artists") or {}).get("items", [])],
        album=(track.get("albumOfTrack") or {}).get("name", ""),
        duration_ms=(track.get("trackDuration") or {}).get("totalMilliseconds", 0),
        # Not present in this GraphQL response's field set.
        isrc=None,
    )


class SpotifyClient:
    """Talks to the same internal endpoints open.spotify.com's own web player
    uses, authenticated with a bearer token + client-token pair copied out of
    DevTools (see README). Unofficial and outside Spotify's documented API
    surface, so treat it as best-effort.
    """

    def __init__(self, access_token: str, client_token: str):
        self.access_token = access_token
        self.client_token = client_token
        self._session = requests.Session()
        self._session.headers.update(_COMMON_HEADERS)
        self._session.headers["authorization"] = f"Bearer {access_token}"
        self._session.headers["client-token"] = client_token
        self._details_cache: dict[str, PlaylistDetails] = {}

    @property
    def is_connected(self) -> bool:
        return bool(self.access_token and self.client_token)

    def connect(self, user_id: str) -> dict:
        """Validates the credentials against the given profile and returns a
        dict with that profile's display name (or the user_id if they have no
        public playlists to read a name from)."""
        page = self._fetch_profile_playlists_page(user_id, offset=0, limit=1)
        display_name = page[0]["owner_name"] if page else user_id
        return {"display_name": display_name, "id": user_id}

    def _fetch_profile_playlists_page(self, user_id: str, offset: int, limit: int) -> list[dict]:
        # user_id can come straight from user-pasted text (see
        # utils.parse_spotify_user_id's unvalidated fallback path), so it's
        # explicitly URL-encoded here rather than trusted to already be a
        # clean path segment.
        url = _PROFILE_PLAYLISTS_URL.format(user_id=quote(user_id, safe=""))
        resp = self._session.get(
            url,
            params={"offset": offset, "limit": limit, "market": _MARKET},
            headers={"Accept": "application/x-protobuf"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        return _parse_profile_playlists(resp.content)

    def fetch_user_playlists(self, user_id: str) -> list[PlaylistSummary]:
        summaries: list[PlaylistSummary] = []
        offset, limit = 0, 200
        for _ in bounded_pages("Playlist listing"):
            page = self._fetch_profile_playlists_page(user_id, offset, limit)
            for entry in page:
                owner = entry["owner_name"] or entry["owner_uri"].removeprefix("spotify:user:")
                summaries.append(
                    PlaylistSummary(
                        id=entry["uri"].removeprefix("spotify:playlist:"),
                        name=entry["name"],
                        description="",
                        owner=owner,
                        # This listing response has no reliable track count
                        # (see _parse_profile_playlists) -- HarvestTab
                        # corrects this once the real count arrives from
                        # its per-playlist background track fetch.
                        track_count=0,
                        public=True,
                    )
                )
            if len(page) < limit:
                break
            offset += limit
        return summaries

    def get_playlist_details(
        self, playlist_id: str, *, force_refresh: bool = False
    ) -> PlaylistDetails:
        """Fetches a playlist's description and full track list together (one
        GraphQL query covers both), caching the result per playlist_id so
        repeated calls — e.g. a background prefetch followed by import — don't
        re-hit the network. Safe to call concurrently from multiple threads
        for different playlist_ids: each writes a distinct cache key."""
        if not force_refresh and playlist_id in self._details_cache:
            return self._details_cache[playlist_id]

        details = self._fetch_playlist_details(playlist_id)
        self._details_cache[playlist_id] = details
        return details

    def _fetch_playlist_details(self, playlist_id: str) -> PlaylistDetails:
        playlist_uri = f"spotify:playlist:{playlist_id}"
        limit = 100
        offset = 0
        description: str | None = None
        tracks: list[TrackInfo] = []
        for _ in bounded_pages(f"Playlist track fetch for {playlist_uri}"):
            playlist = self._fetch_playlist_page(playlist_uri, offset, limit)
            if description is None:
                # Confirmed present on a live response — comes back as real
                # HTML (entities, and <a href="..."> tags for tagged
                # artists/tracks). Kept as-is here and rendered as rich text
                # in the GUI (see HarvestTab); only converted to plain text
                # at the one place that can't render HTML: pushing to Tidal
                # (see utils.html_to_plain_text, used in TidalClient.push_matched).
                description = playlist.get("description") or ""
            items = playlist["content"]["items"]
            tracks.extend(filter(None, (_parse_track_item(item) for item in items)))
            if len(items) < limit:
                break
            offset += limit

        return PlaylistDetails(description=description, tracks=tracks)

    def _fetch_playlist_page(self, playlist_uri: str, offset: int, limit: int) -> dict:
        body = {
            "variables": {
                "uri": playlist_uri,
                "offset": offset,
                "limit": limit,
                "enableWatchFeedEntrypoint": False,
            },
            "operationName": "fetchPlaylist",
            "extensions": {"persistedQuery": {"version": 1, "sha256Hash": _FETCH_PLAYLIST_HASH}},
        }
        resp = self._session.post(
            _PATHFINDER_URL,
            json=body,
            headers={"Accept": "application/json"},
            timeout=_REQUEST_TIMEOUT_S,
        )
        resp.raise_for_status()
        return resp.json()["data"]["playlistV2"]
