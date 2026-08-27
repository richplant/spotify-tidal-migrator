from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class PlaylistSummary:
    id: str
    name: str
    description: str
    owner: str
    # Placeholder (always 0 from SpotifyClient.fetch_user_playlists) --
    # the playlist-listing response has no reliable track count. Callers
    # that need the real count should use PlaylistDetails.tracks instead.
    track_count: int
    public: bool | None


@dataclass
class TrackInfo:
    spotify_id: str
    title: str
    artists: list[str]
    album: str
    duration_ms: int
    isrc: str | None

    @property
    def artist_display(self) -> str:
        return ", ".join(self.artists)


@dataclass
class PlaylistDetails:
    description: str
    tracks: list[TrackInfo]


@dataclass
class PlaylistSnapshot:
    id: str
    name: str
    description: str
    owner: str
    tracks: list[TrackInfo] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(data: dict) -> PlaylistSnapshot:
        tracks = [TrackInfo(**t) for t in data.get("tracks", [])]
        return PlaylistSnapshot(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            owner=data.get("owner", ""),
            tracks=tracks,
            fetched_at=data.get("fetched_at", ""),
        )


@dataclass
class TrackMatch:
    track: TrackInfo
    tidal_id: str | None
    tidal_label: str | None


@dataclass
class PlaylistMatchResult:
    snapshot_id: str
    name: str
    description: str
    matches: list[TrackMatch]
    owner: str = ""

    @property
    def matched_count(self) -> int:
        return sum(1 for m in self.matches if m.tidal_id is not None)

    @property
    def total(self) -> int:
        return len(self.matches)


@dataclass
class PushReport:
    playlist_name: str
    tidal_playlist_id: str
    matched: int
    total: int
    unmatched: list[TrackInfo]
    created: bool = False
    added_count: int = 0
