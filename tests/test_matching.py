from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from spotify_tidal_migrator.matching import TidalSessionExpiredError, match_track
from spotify_tidal_migrator.models import TrackInfo


def make_track_info(isrc=None, artists=("Artist",), title="Title", duration_ms=200000):
    return TrackInfo(
        spotify_id="sp1",
        title=title,
        artists=list(artists),
        album="Album",
        duration_ms=duration_ms,
        isrc=isrc,
    )


def make_tidal_track(id_, name, artist_names, duration_s):
    return SimpleNamespace(
        id=id_,
        name=name,
        artists=[SimpleNamespace(name=n) for n in artist_names],
        duration=duration_s,
    )


def test_isrc_match_short_circuits_search():
    session = MagicMock()
    tidal_track = make_tidal_track(1, "Title", ["Artist"], 200)
    session.get_tracks_by_isrc.return_value = [tidal_track]

    result = match_track(session, make_track_info(isrc="USABC1234567"))

    assert result is tidal_track
    session.search.assert_not_called()


def test_isrc_lookup_error_falls_back_to_fuzzy_search():
    session = MagicMock()
    session.get_tracks_by_isrc.side_effect = Exception("not found")
    tidal_track = make_tidal_track(2, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [tidal_track]}

    result = match_track(session, make_track_info(isrc="USABC1234567"))

    assert result is tidal_track


def test_isrc_empty_result_falls_back_to_fuzzy_search():
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    tidal_track = make_tidal_track(3, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [tidal_track]}

    result = match_track(session, make_track_info(isrc="USABC1234567"))

    assert result is tidal_track


def test_no_isrc_goes_straight_to_fuzzy_search():
    session = MagicMock()
    tidal_track = make_tidal_track(4, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [tidal_track]}

    result = match_track(session, make_track_info(isrc=None))

    assert result is tidal_track
    session.get_tracks_by_isrc.assert_not_called()


def test_no_artists_returns_none_without_calling_search():
    session = MagicMock()

    result = match_track(session, make_track_info(isrc=None, artists=()))

    assert result is None
    session.search.assert_not_called()


def test_search_exception_returns_none():
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    session.search.side_effect = Exception("tidal down")

    result = match_track(session, make_track_info())

    assert result is None


def test_search_exception_with_expired_session_raises_instead_of_silently_matching_none():
    """Regression test: a bare except-and-return-None here used to make
    every remaining track in a batch silently report "no match" once the
    Tidal session expired mid-match (tokens are short-lived, and matching
    hundreds of tracks takes real time) -- indistinguishable from Tidal
    genuinely not having those tracks. Must raise clearly instead."""
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    session.search.side_effect = Exception("unauthorized")
    session.check_login.return_value = False

    with pytest.raises(TidalSessionExpiredError):
        match_track(session, make_track_info())


def test_isrc_lookup_error_with_expired_session_raises():
    session = MagicMock()
    session.get_tracks_by_isrc.side_effect = Exception("unauthorized")
    session.check_login.return_value = False

    with pytest.raises(TidalSessionExpiredError):
        match_track(session, make_track_info(isrc="USABC1234567"))


def test_no_candidates_returns_none():
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    session.search.return_value = {"tracks": []}

    result = match_track(session, make_track_info())

    assert result is None


def test_identical_text_matches_over_unrelated_candidate():
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    unrelated = make_tidal_track(1, "Totally Different Song Name", ["Nobody At All"], 90)
    exact = make_tidal_track(2, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [unrelated, exact]}

    result = match_track(
        session, make_track_info(title="Title", artists=("Artist",), duration_ms=200000)
    )

    assert result is exact


def test_wildly_different_text_returns_none():
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    unrelated = make_tidal_track(1, "Nothing Alike Whatsoever", ["Someone Else Entirely"], 90)
    session.search.return_value = {"tracks": [unrelated]}

    result = match_track(session, make_track_info(title="Title", artists=("Artist",)))

    assert result is None


def test_best_of_multiple_candidates_is_selected_by_score(monkeypatch):
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    low = make_tidal_track(1, "Low", ["Low"], 200)
    high = make_tidal_track(2, "High", ["High"], 200)
    session.search.return_value = {"tracks": [low, high]}

    monkeypatch.setattr(
        "spotify_tidal_migrator.matching.fuzz.token_sort_ratio",
        MagicMock(side_effect=[60, 90]),
    )

    result = match_track(session, make_track_info(duration_ms=200000))

    assert result is high


def test_score_below_threshold_is_rejected(monkeypatch):
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    candidate = make_tidal_track(1, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [candidate]}

    monkeypatch.setattr(
        "spotify_tidal_migrator.matching.fuzz.token_sort_ratio", MagicMock(return_value=69)
    )

    result = match_track(session, make_track_info(duration_ms=200000))

    assert result is None


def test_score_at_threshold_is_accepted(monkeypatch):
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    candidate = make_tidal_track(1, "Title", ["Artist"], 200)
    session.search.return_value = {"tracks": [candidate]}

    monkeypatch.setattr(
        "spotify_tidal_migrator.matching.fuzz.token_sort_ratio", MagicMock(return_value=70)
    )

    result = match_track(session, make_track_info(duration_ms=200000))

    assert result is candidate


def test_duration_mismatch_penalty_can_flip_acceptance(monkeypatch):
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    # Score of 80 alone would pass (>=70); a >5s duration gap costs 15, dropping it below.
    candidate = make_tidal_track(1, "Title", ["Artist"], 90)  # 90s vs 300s target
    session.search.return_value = {"tracks": [candidate]}

    monkeypatch.setattr(
        "spotify_tidal_migrator.matching.fuzz.token_sort_ratio", MagicMock(return_value=80)
    )

    result = match_track(session, make_track_info(duration_ms=300000))

    assert result is None


def test_duration_within_tolerance_does_not_penalize(monkeypatch):
    session = MagicMock()
    session.get_tracks_by_isrc.return_value = []
    candidate = make_tidal_track(1, "Title", ["Artist"], 201)  # 1s off target, within tolerance
    session.search.return_value = {"tracks": [candidate]}

    monkeypatch.setattr(
        "spotify_tidal_migrator.matching.fuzz.token_sort_ratio", MagicMock(return_value=72)
    )

    result = match_track(session, make_track_info(duration_ms=200000))

    assert result is candidate
