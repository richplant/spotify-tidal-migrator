from __future__ import annotations

import tidalapi
from rapidfuzz import fuzz

from .models import TrackInfo

FUZZY_ACCEPT_THRESHOLD = 70
DURATION_TOLERANCE_MS = 5000
DURATION_MISMATCH_PENALTY = 15
SEARCH_CANDIDATE_LIMIT = 10


class TidalSessionExpiredError(RuntimeError):
    """Raised when a Tidal API call fails because the session has expired or
    been revoked, so the caller can stop matching and report it clearly
    instead of every remaining track silently coming back as "no match"."""


def _track_label(artists: str, title: str) -> str:
    return f"{artists} - {title}".lower()


def _raise_if_session_expired(session: tidalapi.Session) -> None:
    # A per-track search/ISRC-lookup failure could be a genuine transient
    # blip (safe to swallow and just skip this one track) or the session
    # dying mid-batch (tokens are short-lived, and matching a large
    # playlist can take a while) -- in the latter case every remaining
    # track would otherwise silently report "no match", indistinguishable
    # from a real non-match. Only checked on the exception path, not the
    # happy path, so this doesn't add a network round trip per track.
    if not session.check_login():
        raise TidalSessionExpiredError(
            "Tidal session has expired or been revoked -- reconnect and try again."
        )


def _match_by_isrc(session: tidalapi.Session, isrc: str) -> tidalapi.media.Track | None:
    try:
        candidates = session.get_tracks_by_isrc(isrc)
    except Exception:
        _raise_if_session_expired(session)
        return None
    return candidates[0] if candidates else None


def _score_candidate(candidate: tidalapi.media.Track, track: TrackInfo, target_label: str) -> float:
    candidate_artists = ", ".join(a.name for a in (candidate.artists or []))
    candidate_label = _track_label(candidate_artists, candidate.name)
    score = fuzz.token_sort_ratio(target_label, candidate_label)

    if track.duration_ms and candidate.duration:
        diff_ms = abs(candidate.duration * 1000 - track.duration_ms)
        if diff_ms > DURATION_TOLERANCE_MS:
            score -= DURATION_MISMATCH_PENALTY

    return score


def _match_by_fuzzy_search(
    session: tidalapi.Session, track: TrackInfo
) -> tidalapi.media.Track | None:
    if not track.artists:
        return None
    query = f"{track.artists[0]} {track.title}"
    try:
        results = session.search(query, models=[tidalapi.media.Track], limit=SEARCH_CANDIDATE_LIMIT)
    except Exception:
        _raise_if_session_expired(session)
        return None

    candidates = results.get("tracks") or []
    if not candidates:
        return None

    target = _track_label(track.artist_display, track.title)
    best, best_score = None, 0.0
    for candidate in candidates:
        score = _score_candidate(candidate, track, target)
        if score > best_score:
            best, best_score = candidate, score

    return best if best is not None and best_score >= FUZZY_ACCEPT_THRESHOLD else None


def match_track(session: tidalapi.Session, track: TrackInfo) -> tidalapi.media.Track | None:
    """Best-effort match of a Spotify track to a Tidal track: ISRC first,
    then fuzzy artist+title search with a duration sanity check."""
    if track.isrc:
        match = _match_by_isrc(session, track.isrc)
        if match is not None:
            return match

    return _match_by_fuzzy_search(session, track)
