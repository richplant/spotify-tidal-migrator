from __future__ import annotations

import json
import re
from pathlib import Path

from . import paths
from .models import PlaylistSnapshot

# Real Spotify playlist ids are base62 (letters + digits) -- this is
# stricter than that on purpose. snapshot.id ultimately comes from parsing
# an undocumented, schema-unverified Spotify response (see
# spotify_client._parse_profile_playlists), so it's validated before ever
# reaching a filesystem path rather than trusted to already be safe;
# without this, a "../../whatever" id would write or delete outside
# PLAYLIST_STORE_DIR.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _snapshot_path(snapshot_id: str) -> Path:
    if not _SAFE_ID_RE.fullmatch(snapshot_id):
        raise ValueError(f"Refusing to use unsafe snapshot id as a filename: {snapshot_id!r}")
    return paths.PLAYLIST_STORE_DIR / f"{snapshot_id}.json"


def save_snapshot(snapshot: PlaylistSnapshot) -> None:
    paths.ensure_dirs()
    path = _snapshot_path(snapshot.id)
    path.write_text(json.dumps(snapshot.to_json(), indent=2))


def list_snapshots() -> list[PlaylistSnapshot]:
    paths.ensure_dirs()
    snapshots = []
    for path in sorted(paths.PLAYLIST_STORE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
            snapshots.append(PlaylistSnapshot.from_json(data))
        except (json.JSONDecodeError, KeyError):
            continue
    return snapshots


def delete_snapshot(snapshot_id: str) -> None:
    path = _snapshot_path(snapshot_id)
    path.unlink(missing_ok=True)


def _load_push_mappings() -> dict[str, str]:
    if not paths.PUSH_MAPPINGS_PATH.exists():
        return {}
    try:
        data = json.loads(paths.PUSH_MAPPINGS_PATH.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def get_push_mapping(snapshot_id: str) -> str | None:
    """Returns the Tidal playlist id a previous push for this snapshot
    created, if any -- lets a re-push look the playlist up by id instead of
    by name (see paths.PUSH_MAPPINGS_PATH)."""
    return _load_push_mappings().get(snapshot_id)


def save_push_mapping(snapshot_id: str, tidal_playlist_id: str) -> None:
    paths.ensure_dirs()
    mappings = _load_push_mappings()
    mappings[snapshot_id] = tidal_playlist_id
    paths.PUSH_MAPPINGS_PATH.write_text(json.dumps(mappings, indent=2))
