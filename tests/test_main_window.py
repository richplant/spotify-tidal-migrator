from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QTextEdit

from spotify_tidal_migrator import store
from spotify_tidal_migrator.gui.main_window import (
    HarvestTab,
    MainWindow,
    MatchTab,
    PushTab,
    _make_table,
    _make_tree,
    _select_all_top_level,
    _selected_table_items,
    _selected_top_level_items,
    _table_item,
    _tree_item,
)
from spotify_tidal_migrator.models import (
    PlaylistMatchResult,
    PlaylistSnapshot,
    PlaylistSummary,
    TrackInfo,
    TrackMatch,
)
from spotify_tidal_migrator.tidal_client import TidalClient

# Selection is native Qt ExtendedSelection (click / ctrl-click / shift-click)
# rather than a hand-rolled checkbox column — these tests exercise the shared
# table/tree helpers that make row data addressable regardless of sort order.


def test_make_table_has_exactly_the_given_headers(qapp):
    table = _make_table(["Name", "Owner"])
    assert table.columnCount() == 2
    assert table.horizontalHeaderItem(0).text() == "Name"
    assert table.horizontalHeaderItem(1).text() == "Owner"


def test_table_item_carries_optional_data(qapp):
    item = _table_item("Display text", data="payload")
    assert item.text() == "Display text"
    assert item.data(Qt.ItemDataRole.UserRole) == "payload"


def test_selected_table_items_returns_data_for_selected_rows_only(qapp):
    table = _make_table(["Name"])
    table.setRowCount(3)
    table.setItem(0, 0, _table_item("A", data="a"))
    table.setItem(1, 0, _table_item("B", data="b"))
    table.setItem(2, 0, _table_item("C", data="c"))

    table.item(0, 0).setSelected(True)
    table.item(2, 0).setSelected(True)

    assert set(_selected_table_items(table)) == {"a", "c"}


def test_selected_table_items_empty_when_nothing_selected(qapp):
    table = _make_table(["Name"])
    table.setRowCount(2)
    table.setItem(0, 0, _table_item("A", data="a"))
    table.setItem(1, 0, _table_item("B", data="b"))

    assert _selected_table_items(table) == []


def test_make_tree_has_exactly_the_given_headers(qapp):
    tree = _make_tree(["Name", "Owner"])
    assert tree.columnCount() == 2
    assert tree.headerItem().text(0) == "Name"
    assert tree.headerItem().text(1) == "Owner"


def test_tree_item_carries_optional_data(qapp):
    item = _tree_item(["Name text"], data={"id": 1})
    assert item.text(0) == "Name text"
    assert item.data(0, Qt.ItemDataRole.UserRole) == {"id": 1}


def test_selected_top_level_items_ignores_child_rows(qapp):
    tree = _make_tree(["Name"])
    a = _tree_item(["A"], data="a")
    b = _tree_item(["B"], data="b")
    tree.addTopLevelItem(a)
    tree.addTopLevelItem(b)
    a.addChild(_tree_item(["child of a"]))
    a.setSelected(True)
    a.child(0).setSelected(True)

    assert _selected_top_level_items(tree) == ["a"]


def test_select_all_top_level_selects_only_top_level_items(qapp):
    tree = _make_tree(["Name"])
    tree.addTopLevelItem(_tree_item(["A"], data="a"))
    tree.addTopLevelItem(_tree_item(["B"], data="b"))

    _select_all_top_level(tree)

    assert sorted(_selected_top_level_items(tree)) == ["a", "b"]


def _harvest_tab(monkeypatch, log=None) -> HarvestTab:
    tab = HarvestTab(log or QTextEdit(), on_imported=lambda: None)
    # Production code always has a real client by the time playlists are
    # fetched (via the Connect -> Fetch chain); stub one in for tests that
    # skip straight to _on_playlists_fetched, so the background prefetch
    # worker it kicks off has something to call.
    tab.spotify = MagicMock()
    # Don't actually start the real background QThread: an exception raised
    # inside a slot invoked from a genuine cross-thread signal (e.g. from a
    # MagicMock().get_playlist_details() call) aborts the whole process
    # rather than raising a catchable Python exception in the test.
    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.PrefetchPlaylistDetailsWorker.start",
        lambda self: None,
    )
    return tab


def test_harvest_tab_populates_tree_from_fetched_playlists(qapp, monkeypatch):
    tab = _harvest_tab(monkeypatch)
    playlists = [
        PlaylistSummary(
            id="1", name="Chill", description="lofi", owner="rich", track_count=42, public=True
        ),
        PlaylistSummary(
            id="2", name="Focus", description="", owner="rich", track_count=7, public=True
        ),
    ]

    tab._on_playlists_fetched(playlists)

    assert tab.tree.topLevelItemCount() == 2
    assert tab.tree.topLevelItem(0).text(HarvestTab._COL_NAME) == "Chill"
    # Blank, not the (unreliable, always-0) PlaylistSummary.track_count --
    # the listing response has no real track count; see
    # test_harvest_tab_playlist_details_ready_fills_description_and_children
    # for where the real count gets filled in.
    assert tab.tree.topLevelItem(0).text(HarvestTab._COL_TRACKS) == ""
    assert tab.tree.topLevelItem(1).text(HarvestTab._COL_NAME) == "Focus"


def test_harvest_tab_playlist_details_ready_fills_description_and_children(qapp, monkeypatch):
    from spotify_tidal_migrator.models import PlaylistDetails

    tab = _harvest_tab(monkeypatch)
    playlists = [
        PlaylistSummary(
            id="1", name="Chill", description="", owner="rich", track_count=2, public=True
        )
    ]
    tab._on_playlists_fetched(playlists)

    track = TrackInfo(
        spotify_id="t1",
        title="Song",
        artists=["Artist"],
        album="Album",
        duration_ms=1000,
        isrc=None,
    )
    tab._on_playlist_details_ready(
        "1", PlaylistDetails(description="A great <b>mix</b>", tracks=[track])
    )

    item = tab._items_by_playlist_id["1"]
    # Description renders as rich text (real Spotify descriptions carry HTML
    # like tagged-artist <a href> links) rather than plain item text.
    description_label = tab.tree.itemWidget(item, HarvestTab._COL_DESCRIPTION)
    assert description_label.text() == "A great <b>mix</b>"
    assert description_label.textFormat() == Qt.TextFormat.RichText
    assert item.childCount() == 1
    assert item.child(0).text(0) == "Song"
    # The real track count (from the per-playlist fetch), not the
    # unreliable listing-response placeholder (PlaylistSummary.track_count
    # was 2 above, but there's only 1 real track).
    assert item.text(HarvestTab._COL_TRACKS) == "1"


def test_harvest_tab_fetch_button_stays_disabled_until_prefetch_finishes(qapp, monkeypatch):
    """Regression test: fetch_btn used to re-enable as soon as the playlist
    *listing* came back, while the background per-playlist detail prefetch
    was still running -- clicking Fetch again mid-prefetch started a second,
    fully overlapping PrefetchPlaylistDetailsWorker with no cancellation of
    the first, doubling outbound request volume against an undocumented
    endpoint that has already been rate-limited once before."""
    tab = _harvest_tab(monkeypatch)
    playlists = [
        PlaylistSummary(
            id="1", name="Chill", description="", owner="rich", track_count=0, public=True
        )
    ]
    # _on_fetch_clicked (the real entry point) disables this before the
    # Connect -> Fetch -> prefetch chain even starts; set it explicitly
    # here since this test jumps straight to _on_playlists_fetched.
    tab.fetch_btn.setEnabled(False)

    tab._on_playlists_fetched(playlists)

    assert tab.fetch_btn.isEnabled() is False

    tab._prefetch_worker.finished.emit()

    assert tab.fetch_btn.isEnabled() is True


def test_harvest_tab_import_uses_selected_tree_items(qapp, monkeypatch):
    tab = _harvest_tab(monkeypatch)
    playlists = [
        PlaylistSummary(
            id="1", name="Chill", description="", owner="rich", track_count=1, public=True
        ),
        PlaylistSummary(
            id="2", name="Focus", description="", owner="rich", track_count=1, public=True
        ),
    ]
    tab._on_playlists_fetched(playlists)
    tab.tree.topLevelItem(1).setSelected(True)

    started = []
    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.SaveSnapshotsWorker",
        lambda client, selected: started.append(selected) or MagicMock(),
    )
    tab._on_import_clicked()

    assert len(started) == 1
    assert [p.id for p in started[0]] == ["2"]


def test_harvest_tab_fetch_requires_a_token(qapp, monkeypatch):
    log = QTextEdit()
    tab = HarvestTab(log, on_imported=lambda: None)
    tab.user_input.setText("123059001")
    # headers_input left empty

    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.QMessageBox.information", lambda *a, **k: None
    )
    tab._on_fetch_clicked()

    assert tab.spotify is None  # never got far enough to construct a client


def test_match_tab_shows_not_matched_yet_before_matching(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)

    assert tab.tree.topLevelItemCount() == 1
    assert tab.tree.topLevelItem(0).text(MatchTab._COL_STATUS) == "not matched yet"
    assert tab.tree.topLevelItem(0).text(MatchTab._COL_OWNER) == "me"


def test_match_tab_refresh_shows_match_counts_and_track_children(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)

    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    tab.results["s1"] = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[
            TrackMatch(track=track, tidal_id="1", tidal_label="A - Song"),
            TrackMatch(track=track, tidal_id=None, tidal_label=None),
        ],
    )
    tab.refresh()

    item = tab.tree.topLevelItem(0)
    assert item.text(MatchTab._COL_STATUS) == "1/2 matched"
    assert item.childCount() == 2
    assert item.child(0).text(0) == "Song"


def test_match_tab_remove_deletes_selected_snapshots_and_refreshes(qapp, monkeypatch):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    store.save_snapshot(
        PlaylistSnapshot(id="s2", name="Keep Me", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)
    assert tab.tree.topLevelItemCount() == 2

    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    tab._items_by_snapshot_id["s1"].setSelected(True)

    tab._on_remove_clicked()

    remaining_ids = {s.id for s in store.list_snapshots()}
    assert remaining_ids == {"s2"}
    assert tab.tree.topLevelItemCount() == 1
    assert tab.tree.topLevelItem(0).text(MatchTab._COL_NAME) == "Keep Me"


def test_match_tab_remove_also_drops_any_match_result_for_it(qapp, monkeypatch):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    notified = []
    tab = MatchTab(log, TidalClient(), on_matched=lambda: notified.append(True))
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    tab.results["s1"] = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
    )
    tab.refresh()

    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    tab._items_by_snapshot_id["s1"].setSelected(True)

    tab._on_remove_clicked()

    assert "s1" not in tab.results
    assert notified == [True]  # push tab was told to resync


def test_match_tab_remove_declined_confirmation_keeps_everything(qapp, monkeypatch):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)
    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.No,
    )
    tab._items_by_snapshot_id["s1"].setSelected(True)

    tab._on_remove_clicked()

    assert {s.id for s in store.list_snapshots()} == {"s1"}
    assert tab.tree.topLevelItemCount() == 1


def test_match_tab_remove_requires_a_selection(qapp, monkeypatch):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)

    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.QMessageBox.information", lambda *a, **k: None
    )
    question = MagicMock()
    monkeypatch.setattr("spotify_tidal_migrator.gui.main_window.QMessageBox.question", question)

    tab._on_remove_clicked()

    question.assert_not_called()
    assert {s.id for s in store.list_snapshots()} == {"s1"}


def test_match_tab_fix_match_updates_in_place_without_full_refresh(qapp, monkeypatch):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)
    tab.tidal.session.check_login = lambda: True

    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    result = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id=None, tidal_label=None)],
    )
    tab.results["s1"] = result
    tab.refresh()

    parent_item = tab.tree.topLevelItem(0)
    parent_item.setExpanded(True)
    child_item = parent_item.child(0)

    from PySide6.QtWidgets import QDialog

    fake_tidal_track = MagicMock()
    fake_tidal_track.id = 999
    fake_tidal_track.name = "Matched Song"
    fake_artist = MagicMock()
    fake_artist.name = "Someone"
    fake_tidal_track.artists = [fake_artist]

    fake_dialog = MagicMock()
    fake_dialog.exec.return_value = QDialog.DialogCode.Accepted
    fake_dialog.selected_track = fake_tidal_track
    monkeypatch.setattr(
        "spotify_tidal_migrator.gui.main_window.ManualMatchDialog",
        lambda *a, **k: fake_dialog,
    )

    tab._on_fix_match_clicked(result, result.matches[0], child_item)

    assert result.matches[0].tidal_id == "999"
    assert result.matches[0].tidal_label == "Someone - Matched Song"
    assert parent_item.text(MatchTab._COL_STATUS) == "1/1 matched"
    # Still the same tree items -- no full refresh() rebuild happened.
    assert tab.tree.topLevelItem(0) is parent_item
    assert parent_item.child(0) is child_item


def test_match_tab_re_fixing_same_track_releases_old_widgets(qapp):
    """Regression test: setItemWidget() and takeChildren() don't delete a
    widget they displace on their own in Qt/PySide6 (verified empirically in
    a standalone reproduction) — _attach_fix_button must release the old
    widgets itself or repeatedly fixing the same track's match leaks one
    QWidget per fix, per column. Checks the release actually happens
    (removeItemWidget + deleteLater called) rather than depending on real
    event-loop deletion timing, which is fragile — and previously
    segfaulted — inside a test suite sharing one session-scoped QApplication
    across hundreds of tests."""
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    log = QTextEdit()
    tab = MatchTab(log, TidalClient(), on_matched=lambda: None)

    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    match = TrackMatch(track=track, tidal_id=None, tidal_label=None)
    result = PlaylistMatchResult(snapshot_id="s1", name="Mix", description="", matches=[match])
    tab.results["s1"] = result
    tab.refresh()

    parent_item = tab.tree.topLevelItem(0)
    child_item = parent_item.child(0)

    old_status_widget = tab.tree.itemWidget(child_item, MatchTab._COL_STATUS)
    old_status_widget.deleteLater = MagicMock(wraps=old_status_widget.deleteLater)
    old_button_widget = tab.tree.itemWidget(child_item, MatchTab._COL_ACTION)
    old_button_widget.deleteLater = MagicMock(wraps=old_button_widget.deleteLater)

    tab._attach_fix_button(child_item, result, match, "re-fixed")

    old_status_widget.deleteLater.assert_called_once()
    old_button_widget.deleteLater.assert_called_once()
    assert tab.tree.itemWidget(child_item, MatchTab._COL_STATUS) is not old_status_widget
    assert tab.tree.itemWidget(child_item, MatchTab._COL_ACTION) is not old_button_widget


def test_fix_match_button_has_fixed_size_policy_and_its_own_column(qapp):
    """The Fix Match button lives in its own column (never sharing one with
    the Match Status badge) and is never allowed to resize -- a narrow
    window should scroll horizontally rather than squash the button."""
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    result = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id=None, tidal_label=None)],
    )
    tab.results["s1"] = result
    tab.refresh()

    from spotify_tidal_migrator.gui.main_window import _make_fix_button_widget

    _widget, btn = _make_fix_button_widget()

    from PySide6.QtWidgets import QSizePolicy

    assert btn.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Fixed
    assert btn.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Fixed

    child_item = tab.tree.topLevelItem(0).child(0)
    assert tab.tree.itemWidget(child_item, MatchTab._COL_STATUS) is not None
    assert tab.tree.itemWidget(child_item, MatchTab._COL_ACTION) is not None


def test_tree_and_table_headers_are_user_resizable(qapp):
    """Columns must stay Interactive (draggable) rather than Stretch or
    ResizeToContents-locked, so the user can widen a column that's too
    narrow for its content instead of it being forced to distort."""
    from PySide6.QtWidgets import QHeaderView

    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)
    header = tab.tree.header()
    for col in range(header.count()):
        assert header.sectionResizeMode(col) == QHeaderView.ResizeMode.Interactive
    assert header.stretchLastSection() is False


def test_match_tab_columns_auto_fit_actual_data_not_just_header_text(qapp):
    """Columns must size to fit real row content on refresh(), not just the
    (usually much shorter) header label -- otherwise a long playlist name
    starts clipped even though the column is technically resizable."""
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Short", description="", owner="me", tracks=[])
    )
    store.save_snapshot(
        PlaylistSnapshot(
            id="s2",
            name="A Really Quite Long Playlist Name That Dwarfs Its Header",
            description="",
            owner="me",
            tracks=[],
        )
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)

    # Wide enough for the long name, not just the "Name" header label.
    assert tab.tree.header().sectionSize(MatchTab._COL_NAME) > 150


def test_match_tab_refresh_keeps_fix_match_column_wide_enough_for_its_button(qapp):
    """Fix Match holds no model text (it's a widget-only column), so a
    naive auto-fit pass would collapse it back to near-zero on every
    refresh() -- it must stay excluded and keep its button-sized width."""
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)

    from spotify_tidal_migrator.gui.main_window import _make_fix_button_widget

    sample_widget, _btn = _make_fix_button_widget()
    min_expected = sample_widget.sizeHint().width()

    tab.refresh()

    assert tab.tree.header().sectionSize(MatchTab._COL_ACTION) >= min_expected


def test_match_tab_filter_hides_playlists_not_matching_name(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Chill Vibes", description="", owner="me", tracks=[])
    )
    store.save_snapshot(
        PlaylistSnapshot(id="s2", name="Workout Mix", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)

    tab.filter_input.setText("workout")

    items = {tab.tree.topLevelItem(i) for i in range(tab.tree.topLevelItemCount())}
    hidden = {item.text(MatchTab._COL_NAME): item.isHidden() for item in items}
    assert hidden == {"Chill Vibes": True, "Workout Mix": False}


def test_match_tab_show_only_unmatched_hides_matched_tracks_and_fully_matched_playlists(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Fully Matched", description="", owner="me", tracks=[])
    )
    store.save_snapshot(
        PlaylistSnapshot(id="s2", name="Partly Matched", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    tab.results["s1"] = PlaylistMatchResult(
        snapshot_id="s1",
        name="Fully Matched",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
    )
    tab.results["s2"] = PlaylistMatchResult(
        snapshot_id="s2",
        name="Partly Matched",
        description="",
        matches=[
            TrackMatch(track=track, tidal_id="1", tidal_label="A - Song"),
            TrackMatch(track=track, tidal_id=None, tidal_label=None),
        ],
    )
    tab.refresh()

    tab.unmatched_only_checkbox.setChecked(True)

    fully_matched_item = tab._items_by_snapshot_id["s1"]
    partly_matched_item = tab._items_by_snapshot_id["s2"]
    assert fully_matched_item.isHidden() is True
    assert partly_matched_item.isHidden() is False
    assert partly_matched_item.isExpanded() is True
    # The matched child stays in the tree but is hidden; the unmatched one shows.
    assert partly_matched_item.child(0).isHidden() is True
    assert partly_matched_item.child(1).isHidden() is False


def test_match_tab_playlist_matched_updates_live_without_collapsing_other_rows(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    store.save_snapshot(
        PlaylistSnapshot(id="s2", name="Other", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)

    other_item = tab._items_by_snapshot_id["s2"]
    other_item.setExpanded(True)  # simulate the user having this one open

    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    result = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
    )

    tab._on_playlist_matched(result)

    assert tab.results["s1"] is result
    assert tab._items_by_snapshot_id["s1"].text(MatchTab._COL_STATUS) == "1/1 matched"
    # Live update didn't rebuild the whole tree -- unrelated rows are untouched.
    assert tab._items_by_snapshot_id["s2"] is other_item
    assert other_item.isExpanded() is True


def test_match_tab_playlist_progress_shows_live_status(qapp):
    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    tab = MatchTab(QTextEdit(), TidalClient(), on_matched=lambda: None)

    tab._on_playlist_progress("s1", 3, 10)

    assert tab._items_by_snapshot_id["s1"].text(MatchTab._COL_STATUS) == "Matching… 3/10"


def test_push_tab_filter_hides_playlists_not_matching_name(qapp):
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    results = [
        PlaylistMatchResult(
            snapshot_id="s1",
            name="Chill Vibes",
            description="",
            matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
        ),
        PlaylistMatchResult(
            snapshot_id="s2",
            name="Workout Mix",
            description="",
            matches=[],
        ),
    ]
    tab = PushTab(QTextEdit(), TidalClient(), get_results=lambda: results)

    tab.filter_input.setText("chill")

    assert tab.table.isRowHidden(0) is False
    assert tab.table.isRowHidden(1) is True


def test_push_tab_select_all_and_selection_survives_sorting(qapp):
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    results = [
        PlaylistMatchResult(snapshot_id="s1", name="B Playlist", description="", matches=[]),
        PlaylistMatchResult(
            snapshot_id="s2",
            name="A Playlist",
            description="",
            matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
        ),
    ]
    tab = PushTab(QTextEdit(), TidalClient(), get_results=lambda: results)

    tab.table.sortItems(PushTab._COL_NAME, Qt.SortOrder.AscendingOrder)
    tab.table.selectAll()

    selected_names = {r.name for r in _selected_table_items(tab.table)}
    assert selected_names == {"A Playlist", "B Playlist"}


def test_main_window_builds_with_three_tabs_in_order(qapp):
    window = MainWindow()
    titles = [window.tabs.tabText(i) for i in range(window.tabs.count())]
    assert titles == ["1. Harvest", "2. Match", "3. Push"]


def test_match_tab_and_push_tab_share_one_tidal_client(qapp):
    window = MainWindow()
    assert window.match_tab.tidal is window.tidal
    assert window.push_tab.tidal is window.tidal


def test_push_tab_reads_results_live_from_match_tab(qapp):
    window = MainWindow()
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    result = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
        owner="rich",
    )
    window.match_tab.results["s1"] = result

    window.push_tab.refresh()

    assert window.push_tab.results == [result]
    assert window.push_tab.table.rowCount() == 1
    assert window.push_tab.table.item(0, PushTab._COL_NAME).text() == "Mix"
    assert window.push_tab.table.item(0, PushTab._COL_OWNER).text() == "rich"
    assert window.push_tab.table.item(0, PushTab._COL_MATCHED).text() == "1"
    assert "rich" in window.push_tab.folder_input.placeholderText()


def test_push_tab_passes_folder_input_to_push_worker(qapp, monkeypatch):
    window = MainWindow()
    window.tidal.session.check_login = lambda: True
    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    result = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
        owner="rich",
    )
    window.match_tab.results["s1"] = result
    window.push_tab.refresh()
    window.push_tab.table.selectRow(0)
    window.push_tab.folder_input.setText("Custom Folder")

    captured = {}
    mock_worker = MagicMock()

    def fake_push_worker(client, selected, folder_name):
        captured["args"] = (client, selected, folder_name)
        return mock_worker

    monkeypatch.setattr("spotify_tidal_migrator.gui.main_window.PushWorker", fake_push_worker)

    window.push_tab._on_push_clicked()

    assert captured["args"][1] == [result]
    assert captured["args"][2] == "Custom Folder"


def test_switching_to_push_tab_refreshes_it_from_match_tab(qapp):
    window = MainWindow()
    window.tabs.setCurrentWidget(window.harvest_tab)
    assert window.push_tab.table.rowCount() == 0

    track = TrackInfo(
        spotify_id="t1", title="Song", artists=["A"], album="Al", duration_ms=1, isrc=None
    )
    window.match_tab.results["s1"] = PlaylistMatchResult(
        snapshot_id="s1",
        name="Mix",
        description="",
        matches=[TrackMatch(track=track, tidal_id="1", tidal_label="A - Song")],
    )

    window.tabs.setCurrentWidget(window.push_tab)

    assert window.push_tab.table.rowCount() == 1


def test_importing_in_harvest_tab_refreshes_match_tab(qapp):
    window = MainWindow()
    assert window.match_tab.tree.topLevelItemCount() == 0

    store.save_snapshot(
        PlaylistSnapshot(id="s1", name="Mix", description="", owner="me", tracks=[])
    )
    window.harvest_tab.on_imported()

    assert window.match_tab.tree.topLevelItemCount() == 1
