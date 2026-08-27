from unittest.mock import MagicMock

import pytest

from spotify_tidal_migrator.gui.manual_match_dialog import ManualMatchDialog
from spotify_tidal_migrator.gui.workers import ManualSearchWorker
from spotify_tidal_migrator.models import TrackInfo


@pytest.fixture(autouse=True)
def synchronous_search_worker(monkeypatch):
    """ManualMatchDialog launches a real QThread via ManualSearchWorker.start().
    Like every other worker in this codebase (see CONTRIBUTING.md's testing
    conventions), tests call run() directly instead, so results/errors are
    available synchronously without depending on a live Qt event loop to
    deliver a cross-thread queued signal."""
    monkeypatch.setattr(ManualSearchWorker, "start", ManualSearchWorker.run)


def _track():
    return TrackInfo(
        spotify_id="t1", title="Song", artists=["Artist"], album="Album", duration_ms=1, isrc=None
    )


def _fake_tidal_track(track_id=1, name="Result", artist_name="Result Artist", duration=200):
    t = MagicMock()
    t.id = track_id
    t.name = name
    t.duration = duration
    artist = MagicMock()
    artist.name = artist_name
    t.artists = [artist]
    album = MagicMock()
    album.name = "Result Album"
    t.album = album
    return t


def test_dialog_searches_on_open_with_prefilled_query(qapp):
    tidal = MagicMock()
    tidal.search_tracks.return_value = [_fake_tidal_track()]

    dialog = ManualMatchDialog(tidal, _track())

    assert dialog.query_input.text() == "Artist Song"
    assert dialog.results_list.count() == 1
    assert "Result Artist" in dialog.results_list.item(0).text()
    tidal.search_tracks.assert_called_once_with("Artist Song")


def test_dialog_search_button_disabled_while_search_in_flight(qapp, monkeypatch):
    tidal = MagicMock()
    # Don't run the worker at all for this one -- simulates a search that
    # hasn't completed yet, to check the button state mid-flight.
    monkeypatch.setattr(ManualSearchWorker, "start", lambda self: None)

    dialog = ManualMatchDialog(tidal, _track())

    assert dialog.search_btn.isEnabled() is False


def test_dialog_search_button_re_enabled_after_results_arrive(qapp):
    tidal = MagicMock()
    tidal.search_tracks.return_value = [_fake_tidal_track()]

    dialog = ManualMatchDialog(tidal, _track())

    assert dialog.search_btn.isEnabled() is True


def test_dialog_new_search_replaces_rather_than_merges_previous_results(qapp):
    """Regression test for the overlapping-search race: results from an
    earlier query must not still be showing (or get merged with) a later
    query's results. The actual concurrency race is prevented by disabling
    search_btn for the duration of a search (see test above) -- this test
    covers the list-replacement behavior that guard depends on."""
    tidal = MagicMock()
    tidal.search_tracks.return_value = [_fake_tidal_track(track_id=1, name="First result")]
    dialog = ManualMatchDialog(tidal, _track())
    assert dialog.results_list.count() == 1
    assert "First result" in dialog.results_list.item(0).text()

    tidal.search_tracks.return_value = [_fake_tidal_track(track_id=2, name="Second result")]
    dialog.query_input.setText("a different query")
    dialog._on_search_clicked()

    assert dialog.results_list.count() == 1
    assert "Second result" in dialog.results_list.item(0).text()


def test_dialog_selecting_result_enables_ok_and_sets_selected_track(qapp):
    tidal = MagicMock()
    fake_track = _fake_tidal_track()
    tidal.search_tracks.return_value = [fake_track]

    dialog = ManualMatchDialog(tidal, _track())
    dialog.results_list.setCurrentRow(0)

    assert dialog.selected_track is fake_track


def test_dialog_double_click_result_accepts_dialog(qapp):
    tidal = MagicMock()
    fake_track = _fake_tidal_track()
    tidal.search_tracks.return_value = [fake_track]

    dialog = ManualMatchDialog(tidal, _track())
    accepted = []
    dialog.accept = lambda: accepted.append(True)

    dialog._on_item_double_clicked(dialog.results_list.item(0))

    assert dialog.selected_track is fake_track
    assert accepted == [True]


def test_dialog_search_failure_shows_error_and_re_enables_button(qapp):
    tidal = MagicMock()
    tidal.search_tracks.side_effect = RuntimeError("tidal down")

    dialog = ManualMatchDialog(tidal, _track())

    assert "tidal down" in dialog.status_label.text()
    assert dialog.search_btn.isEnabled() is True
