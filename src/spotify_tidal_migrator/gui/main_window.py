from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from rapidfuzz import fuzz

from .. import store
from ..models import (
    PlaylistDetails,
    PlaylistMatchResult,
    PlaylistSnapshot,
    PlaylistSummary,
)
from ..spotify_client import SpotifyClient
from ..tidal_client import TidalClient
from ..utils import (
    extract_bearer_token,
    extract_client_token,
    html_to_plain_text,
    parse_spotify_user_id,
)
from .manual_match_dialog import ManualMatchDialog
from .workers import (
    ConnectSpotifyWorker,
    ConnectTidalWorker,
    FetchPlaylistsWorker,
    MatchWorker,
    PrefetchPlaylistDetailsWorker,
    PushWorker,
    SaveSnapshotsWorker,
)

FILTER_MATCH_THRESHOLD = 50


# --- Shared table/tree helpers ---
#
# Selection is native Qt ExtendedSelection: click to select, ctrl-click to
# add, shift-click for a range — no hand-rolled checkbox column to manage.
# Each row's underlying model object (PlaylistSummary, PlaylistSnapshot,
# PlaylistMatchResult) lives on column 0's UserRole data.


def _apply_column_sizing(header: QHeaderView, column_count: int) -> None:
    # Every column is user-resizable (Interactive) rather than auto-
    # stretched or permanently content-locked -- a narrower window reveals
    # a horizontal scrollbar instead of squashing cell contents (e.g. the
    # Fix Match button). There's no data yet at construction time to size
    # against; see _autofit_columns, called once real rows exist.
    header.setStretchLastSection(False)
    for col in range(column_count):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)


def _autofit_columns(
    view: QTableWidget | QTreeWidget, *, skip: frozenset[int] = frozenset()
) -> None:
    # A one-off fit-to-content pass, run right after (re)populating rows --
    # columns start sized to their actual data, then stay Interactive so
    # the user can freely resize them afterward without being fought by a
    # continuous auto-fit (which Qt's own ResizeToContents mode would do,
    # and which also disables user dragging entirely).
    for col in range(view.columnCount()):
        if col not in skip:
            view.resizeColumnToContents(col)


def _make_table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().setVisible(False)
    _apply_column_sizing(table.horizontalHeader(), len(headers))
    return table


def _table_item(text: str, data=None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if data is not None:
        item.setData(Qt.ItemDataRole.UserRole, data)
    return item


def _selected_table_items(table: QTableWidget) -> list:
    return [
        table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        for index in table.selectionModel().selectedRows()
    ]


def _make_tree(headers: list[str]) -> QTreeWidget:
    tree = QTreeWidget()
    tree.setColumnCount(len(headers))
    tree.setHeaderLabels(headers)
    tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    tree.setUniformRowHeights(False)
    _apply_column_sizing(tree.header(), len(headers))
    return tree


def _tree_item(texts: list[str], data=None) -> QTreeWidgetItem:
    item = QTreeWidgetItem(texts)
    if data is not None:
        item.setData(0, Qt.ItemDataRole.UserRole, data)
    return item


def _selected_top_level_items(tree: QTreeWidget) -> list:
    return [
        item.data(0, Qt.ItemDataRole.UserRole)
        for item in tree.selectedItems()
        if item.parent() is None
    ]


def _select_all_top_level(tree: QTreeWidget) -> None:
    for i in range(tree.topLevelItemCount()):
        tree.topLevelItem(i).setSelected(True)


def _enable_sorting_without_resorting(view: QTableWidget | QTreeWidget) -> None:
    # setSortingEnabled(True) alone immediately applies Qt's default sort
    # indicator (descending, column 0), silently reordering freshly
    # populated rows out of fetch/insertion order. The indicator must be
    # cleared *before* enabling sorting -- clearing it after still lets that
    # first resort happen. This keeps insertion order until the user
    # actually clicks a header, while leaving header-click sorting enabled.
    header = view.horizontalHeader() if isinstance(view, QTableWidget) else view.header()
    header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
    view.setSortingEnabled(True)


def _build_tree_select_row(tree: QTreeWidget) -> QHBoxLayout:
    """ "Select All"/"Select None" for a tree — shared by Harvest and Match,
    which both need it to leave child rows (tracks) out of "all"."""
    select_all_btn = QPushButton("Select All")
    select_all_btn.clicked.connect(lambda: _select_all_top_level(tree))
    select_none_btn = QPushButton("Select None")
    select_none_btn.clicked.connect(tree.clearSelection)
    row = QHBoxLayout()
    row.addWidget(select_all_btn)
    row.addWidget(select_none_btn)
    row.addStretch(1)
    return row


def _should_hide_playlist_item(
    *, has_children: bool, name_matches: bool, unmatched_only: bool, any_unmatched_visible: bool
) -> bool:
    if not name_matches:
        return True
    if not unmatched_only:
        return False
    # Nothing matched yet, so there's nothing to show if the user only
    # wants to see unmatched tracks.
    return not has_children or not any_unmatched_visible


def _release_item_widget(tree: QTreeWidget, item: QTreeWidgetItem, column: int) -> None:
    # setItemWidget() doesn't delete a widget it replaces, and taking an
    # item away from its parent doesn't delete its widget either — both
    # must be released explicitly or every rebuild leaks one widget.
    old_widget = tree.itemWidget(item, column)
    if old_widget is not None:
        tree.removeItemWidget(item, column)
        old_widget.deleteLater()


def _make_status_widget(status: str, matched: bool) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(2, 0, 2, 0)
    label = QLabel(status)
    if not matched:
        # A plain "no match" text label was easy to miss scanning a long
        # track list — a solid badge stands out at a glance instead.
        label.setStyleSheet(
            "QLabel {"
            " background-color: #d9534f;"
            " color: white;"
            " border-radius: 8px;"
            " padding: 1px 8px;"
            " font-weight: 600;"
            "}"
        )
    row.addWidget(label)
    row.addStretch(1)
    return widget


def _make_fix_button_widget() -> tuple[QWidget, QPushButton]:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(2, 0, 2, 0)
    btn = QPushButton("Fix match…")
    # Fixed policy plus its own column (never a stretch/shared column) so
    # the button always renders at its natural size -- a narrower window
    # reveals a horizontal scrollbar instead of squashing it.
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    row.addWidget(btn)
    row.addStretch(1)
    return widget, btn


class HarvestTab(QWidget):
    _COL_NAME = 0
    _COL_OWNER = 1
    _COL_TRACKS = 2
    _COL_DESCRIPTION = 3

    def __init__(self, log: QTextEdit, on_imported):
        super().__init__()
        self.log = log
        self.on_imported = on_imported
        self.spotify: SpotifyClient | None = None
        self.playlists: list[PlaylistSummary] = []
        self._items_by_playlist_id: dict[str, QTreeWidgetItem] = {}
        self._details_by_id: dict[str, PlaylistDetails] = {}
        self._worker = None
        self._prefetch_worker: PrefetchPlaylistDetailsWorker | None = None
        self._parsed_token: str | None = None
        self._parsed_client_token: str | None = None

        self.tree = _make_tree(["Name", "Owner", "Tracks", "Description"])
        self.status_label = QLabel("Not connected.")

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_help_label())
        layout.addWidget(self._build_headers_input())
        layout.addWidget(self.token_status_label)
        layout.addLayout(self._build_connect_row())
        layout.addWidget(self.status_label)
        layout.addWidget(self._build_filter_input())
        layout.addLayout(_build_tree_select_row(self.tree))
        layout.addWidget(self.tree, stretch=1)
        layout.addWidget(self._build_import_button())

    def _build_help_label(self) -> QLabel:
        label = QLabel(
            "<b>No Spotify Developer app needed</b> — this reuses the credentials your "
            "browser's own Spotify session already has.<br>"
            "1. Open a Spotify profile page at open.spotify.com, logged in.<br>"
            "2. DevTools → Network, filter for “pathfinder”, click one of those requests.<br>"
            "3. Right-click it → Copy → <b>Copy as fetch</b> (or “Copy Request Headers”), and "
            "paste the whole thing below. Valid for about an hour."
        )
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setWordWrap(True)
        return label

    def _build_headers_input(self) -> QTextEdit:
        self.headers_input = QTextEdit()
        self.headers_input.setPlaceholderText(
            "Paste the copied request here (as fetch, as cURL, or raw headers) — "
            "the access token and client-token are pulled out automatically."
        )
        self.headers_input.setMaximumHeight(90)
        self.headers_input.textChanged.connect(self._on_headers_changed)
        self.token_status_label = QLabel("Waiting for a pasted request…")
        return self.headers_input

    def _build_connect_row(self) -> QHBoxLayout:
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText(
            "Spotify user ID or profile URL, e.g. https://open.spotify.com/user/123059001"
        )
        self.fetch_btn = QPushButton("Connect && Fetch Playlists")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        row = QHBoxLayout()
        row.addWidget(self.user_input, stretch=1)
        row.addWidget(self.fetch_btn)
        return row

    def _build_filter_input(self) -> QLineEdit:
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText(
            "Filter playlists (fuzzy match on name/owner/description)…"
        )
        self.filter_input.textChanged.connect(self._on_filter_changed)
        return self.filter_input

    def _build_import_button(self) -> QPushButton:
        self.import_btn = QPushButton("Import Selected →")
        self.import_btn.clicked.connect(self._on_import_clicked)
        return self.import_btn

    def _on_headers_changed(self) -> None:
        text = self.headers_input.toPlainText()
        self._parsed_token = extract_bearer_token(text)
        self._parsed_client_token = extract_client_token(text)
        if not text.strip():
            self.token_status_label.setText("Waiting for a pasted request…")
        elif self._parsed_token and self._parsed_client_token:
            self.token_status_label.setText("✓ Found access token and client-token.")
        else:
            missing = [
                label
                for label, value in (
                    ("access token", self._parsed_token),
                    ("client-token", self._parsed_client_token),
                )
                if not value
            ]
            self.token_status_label.setText(
                f"⚠ Couldn't find: {', '.join(missing)}. Make sure you copied the whole "
                "request, not just the URL."
            )

    def _on_fetch_clicked(self) -> None:
        token = self._parsed_token
        client_token = self._parsed_client_token
        if not token or not client_token:
            QMessageBox.information(
                self,
                "Tokens needed",
                "Paste a captured request that contains both the access token and the "
                "client-token first.",
            )
            return

        user_id = parse_spotify_user_id(self.user_input.text())
        if not user_id:
            QMessageBox.warning(self, "Missing user", "Enter a Spotify user ID or profile URL.")
            return

        self.spotify = SpotifyClient(token, client_token)
        self.fetch_btn.setEnabled(False)
        self.status_label.setText("Validating token…")
        self.log.append("Validating Spotify token…")

        self._worker = ConnectSpotifyWorker(self.spotify, user_id)
        self._worker.succeeded.connect(lambda name: self._on_spotify_connected(name, user_id))
        self._worker.failed.connect(self._on_spotify_failed)
        self._worker.start()

    def _on_spotify_connected(self, display_name: str, user_id: str) -> None:
        self.status_label.setText(f"Connected to Spotify as {display_name}. Fetching playlists…")
        self.log.append(f"Connected to Spotify as {display_name}.")
        self._worker = FetchPlaylistsWorker(self.spotify, user_id)
        self._worker.succeeded.connect(self._on_playlists_fetched)
        self._worker.failed.connect(self._on_spotify_failed)
        self._worker.start()

    def _on_spotify_failed(self, message: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.status_label.setText("Not connected.")
        self.log.append(f"Spotify error: {message}")
        QMessageBox.critical(self, "Spotify error", message)

    def _on_playlists_fetched(self, playlists: list[PlaylistSummary]) -> None:
        # fetch_btn stays disabled through the background detail prefetch
        # below (re-enabled once it finishes) -- otherwise clicking Fetch
        # again mid-prefetch starts a second, fully overlapping prefetch
        # worker with no cancellation of the first, doubling outbound
        # request volume against an undocumented endpoint that has already
        # 429'd this app once before.
        self.playlists = playlists
        self._items_by_playlist_id = {}
        self._details_by_id = {}
        self.status_label.setText(f"Found {len(playlists)} public playlist(s).")
        self.log.append(f"Found {len(playlists)} public playlist(s).")

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for p in playlists:
            # Tracks starts blank -- the playlist-listing response has no
            # reliable track count (see SpotifyClient._parse_profile_playlists);
            # it's filled in below once the real count arrives from the
            # per-playlist background track fetch.
            item = _tree_item([p.name, p.owner, "", ""], data=p)
            self.tree.addTopLevelItem(item)
            self._items_by_playlist_id[p.id] = item
        _autofit_columns(self.tree)
        _enable_sorting_without_resorting(self.tree)

        if playlists:
            self.log.append("Fetching descriptions and track previews in the background…")
        self._prefetch_worker = PrefetchPlaylistDetailsWorker(self.spotify, playlists)
        self._prefetch_worker.playlist_ready.connect(self._on_playlist_details_ready)
        self._prefetch_worker.failed_one.connect(self._on_playlist_details_failed)
        self._prefetch_worker.finished.connect(self._on_prefetch_finished)
        self._prefetch_worker.start()

    def _on_prefetch_finished(self) -> None:
        self.fetch_btn.setEnabled(True)

    def _on_playlist_details_ready(self, playlist_id: str, details: PlaylistDetails) -> None:
        self._details_by_id[playlist_id] = details
        item = self._items_by_playlist_id.get(playlist_id)
        if item is None:
            return
        # Int (not str) on DisplayRole so the default sort comparator is
        # numeric ("10" < "9" lexicographically would otherwise apply).
        item.setData(self._COL_TRACKS, Qt.ItemDataRole.DisplayRole, len(details.tracks))
        _release_item_widget(self.tree, item, self._COL_DESCRIPTION)
        if details.description:
            # Spotify descriptions are real HTML (entities, <a href="...">
            # tags for tagged artists/tracks) — render it as such rather
            # than dumping raw markup as plain text.
            label = QLabel(details.description)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setWordWrap(True)
            label.setOpenExternalLinks(True)
            self.tree.setItemWidget(item, self._COL_DESCRIPTION, label)
        item.takeChildren()
        for t in details.tracks:
            item.addChild(QTreeWidgetItem([t.title, t.artist_display, "", t.album]))
        self._on_filter_changed(self.filter_input.text())

    def _on_playlist_details_failed(self, playlist_id: str, message: str) -> None:
        item = self._items_by_playlist_id.get(playlist_id)
        name = item.text(self._COL_NAME) if item else playlist_id
        self.log.append(f"Couldn't preview “{name}”: {message}")

    def _on_filter_changed(self, text: str) -> None:
        query = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            p: PlaylistSummary = item.data(0, Qt.ItemDataRole.UserRole)
            details = self._details_by_id.get(p.id)
            description = html_to_plain_text(details.description) if details else ""
            haystack = f"{p.name} {p.owner} {description}".lower()
            hide = bool(query) and fuzz.partial_ratio(query, haystack) < FILTER_MATCH_THRESHOLD
            item.setHidden(hide)

    def _on_import_clicked(self) -> None:
        selected = _selected_top_level_items(self.tree)
        if not selected:
            QMessageBox.information(
                self, "Nothing selected", "Select at least one playlist to import."
            )
            return
        self.import_btn.setEnabled(False)
        self.log.append(f"Importing {len(selected)} playlist(s)…")

        self._worker = SaveSnapshotsWorker(self.spotify, selected)
        self._worker.progress.connect(self.log.append)
        self._worker.succeeded.connect(self._on_import_succeeded)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def _on_import_succeeded(self, names: list[str]) -> None:
        self.import_btn.setEnabled(True)
        self.log.append(f"Imported: {', '.join(names)}")
        self.on_imported()

    def _on_import_failed(self, message: str) -> None:
        self.import_btn.setEnabled(True)
        self.log.append(f"Import error: {message}")
        QMessageBox.critical(self, "Import error", message)


class MatchTab(QWidget):
    """Central tab: owns the Tidal connection and turns imported playlists
    into reviewed track-by-track matches before anything is written to Tidal."""

    _COL_NAME = 0
    _COL_OWNER = 1
    _COL_TRACKS = 2
    _COL_ARTIST = 3
    _COL_STATUS = 4
    _COL_ACTION = 5

    def __init__(self, log: QTextEdit, tidal: TidalClient, on_matched):
        super().__init__()
        self.log = log
        self.tidal = tidal
        self.on_matched = on_matched
        self.snapshots: list[PlaylistSnapshot] = []
        self.results: dict[str, PlaylistMatchResult] = {}
        self._items_by_snapshot_id: dict[str, QTreeWidgetItem] = {}
        self._worker = None

        self.tree = _make_tree(["Name", "Owner", "Tracks", "Artist", "Match Status", "Fix Match"])
        # "Fix Match" (the header label) sizes narrower than the actual
        # button, which would otherwise start the column too tight to show
        # it in full -- widen it to the button's real size up front.
        sample_widget, _sample_btn = _make_fix_button_widget()
        self.tree.header().resizeSection(self._COL_ACTION, sample_widget.sizeHint().width())
        sample_widget.deleteLater()
        _enable_sorting_without_resorting(self.tree)
        self.status_label = QLabel("Not connected.")
        self.link_label = QLabel()
        self.link_label.setOpenExternalLinks(True)

        self.connect_btn = QPushButton("Connect to Tidal")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        connect_row = QHBoxLayout()
        connect_row.addWidget(self.connect_btn)
        connect_row.addWidget(self.status_label, stretch=1)

        refresh_btn = QPushButton("Refresh Imported Playlists")
        refresh_btn.clicked.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.addLayout(connect_row)
        layout.addWidget(self.link_label)
        layout.addWidget(refresh_btn)
        layout.addLayout(self._build_filter_row())
        layout.addLayout(_build_tree_select_row(self.tree))
        layout.addWidget(self.tree, stretch=1)
        layout.addLayout(self._build_action_row())

        self.refresh()

    def _build_filter_row(self) -> QHBoxLayout:
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter playlists (fuzzy match on name)…")
        self.filter_input.textChanged.connect(self._apply_filter)
        self.unmatched_only_checkbox = QCheckBox("Show only unmatched tracks")
        self.unmatched_only_checkbox.toggled.connect(self._apply_filter)
        row = QHBoxLayout()
        row.addWidget(self.filter_input, stretch=1)
        row.addWidget(self.unmatched_only_checkbox)
        return row

    def _build_action_row(self) -> QHBoxLayout:
        self.match_btn = QPushButton("Match Selected Against Tidal")
        self.match_btn.clicked.connect(self._on_match_clicked)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._on_remove_clicked)
        row = QHBoxLayout()
        row.addWidget(self.match_btn, stretch=1)
        row.addWidget(self.remove_btn)
        return row

    def refresh(self) -> None:
        self.snapshots = store.list_snapshots()
        self._items_by_snapshot_id = {}
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for snap in self.snapshots:
            result = self.results.get(snap.id)
            status = (
                f"{result.matched_count}/{result.total} matched" if result else "not matched yet"
            )
            item = _tree_item([snap.name, snap.owner, "", "", status, ""], data=snap)
            item.setData(self._COL_TRACKS, Qt.ItemDataRole.DisplayRole, len(snap.tracks))
            self.tree.addTopLevelItem(item)
            self._items_by_snapshot_id[snap.id] = item
            if result:
                self._populate_track_children(item, result)
                if result.matched_count < result.total:
                    item.setExpanded(True)
        # The Fix Match column is sized once in __init__ to fit its button
        # exactly (see there) and left out here so re-fitting to its
        # (blank) model text on every refresh doesn't collapse it back down.
        _autofit_columns(self.tree, skip=frozenset({self._COL_ACTION}))
        _enable_sorting_without_resorting(self.tree)
        self._apply_filter()

    def _filter_children(self, item: QTreeWidgetItem, unmatched_only: bool) -> bool:
        """Hides/shows this playlist's track rows per the unmatched-only
        toggle; returns whether any of them ended up visible."""
        any_visible = False
        for c in range(item.childCount()):
            child = item.child(c)
            is_unmatched = child.data(self._COL_STATUS, Qt.ItemDataRole.DisplayRole) == 0
            hide_child = unmatched_only and not is_unmatched
            child.setHidden(hide_child)
            any_visible = any_visible or not hide_child
        return any_visible

    def _apply_filter(self) -> None:
        query = self.filter_input.text().strip().lower()
        unmatched_only = self.unmatched_only_checkbox.isChecked()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            snap: PlaylistSnapshot = item.data(0, Qt.ItemDataRole.UserRole)
            name_matches = (
                not query or fuzz.partial_ratio(query, snap.name.lower()) >= FILTER_MATCH_THRESHOLD
            )
            any_unmatched_visible = self._filter_children(item, unmatched_only)

            hide_item = _should_hide_playlist_item(
                has_children=item.childCount() > 0,
                name_matches=name_matches,
                unmatched_only=unmatched_only,
                any_unmatched_visible=any_unmatched_visible,
            )
            item.setHidden(hide_item)
            if not hide_item and unmatched_only:
                item.setExpanded(True)

    def _populate_track_children(
        self, parent_item: QTreeWidgetItem, result: PlaylistMatchResult
    ) -> None:
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            _release_item_widget(self.tree, child, self._COL_STATUS)
            _release_item_widget(self.tree, child, self._COL_ACTION)
        parent_item.takeChildren()
        for match in result.matches:
            matched = match.tidal_id is not None
            status = match.tidal_label if matched else "No match"
            child = QTreeWidgetItem([match.track.title, "", "", match.track.artist_display, "", ""])
            # The Match Status cell is a widget (badge), so this data is
            # never actually painted — it's purely a sort key, so sorting
            # by that column groups unmatched tracks first regardless of
            # what the badge widget on top of it looks like.
            child.setData(self._COL_STATUS, Qt.ItemDataRole.DisplayRole, 1 if matched else 0)
            parent_item.addChild(child)
            self._attach_fix_button(child, result, match, status)

    def _attach_fix_button(
        self, child_item: QTreeWidgetItem, result: PlaylistMatchResult, match, status: str
    ) -> None:
        _release_item_widget(self.tree, child_item, self._COL_STATUS)
        _release_item_widget(self.tree, child_item, self._COL_ACTION)
        status_widget = _make_status_widget(status, matched=match.tidal_id is not None)
        self.tree.setItemWidget(child_item, self._COL_STATUS, status_widget)

        button_widget, btn = _make_fix_button_widget()
        btn.clicked.connect(
            lambda checked=False, m=match, ci=child_item, r=result: self._on_fix_match_clicked(
                r, m, ci
            )
        )
        self.tree.setItemWidget(child_item, self._COL_ACTION, button_widget)

    def _on_fix_match_clicked(
        self, result: PlaylistMatchResult, match, child_item: QTreeWidgetItem
    ) -> None:
        if not self.tidal.is_connected:
            QMessageBox.information(self, "Not connected", "Connect to Tidal first.")
            return
        dialog = ManualMatchDialog(self.tidal, match.track, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.selected_track is None:
            return

        track = dialog.selected_track
        artists = ", ".join(a.name for a in (track.artists or []))
        match.tidal_id = str(track.id)
        match.tidal_label = f"{artists} - {track.name}"

        child_item.setData(self._COL_STATUS, Qt.ItemDataRole.DisplayRole, 1)
        self._attach_fix_button(child_item, result, match, match.tidal_label)
        parent_item = child_item.parent()
        parent_item.setText(self._COL_STATUS, f"{result.matched_count}/{result.total} matched")
        self._apply_filter()

    def _on_connect_clicked(self) -> None:
        self.connect_btn.setEnabled(False)
        self.status_label.setText("Connecting…")
        self._worker = ConnectTidalWorker(self.tidal)
        self._worker.url_ready.connect(self._on_url_ready)
        self._worker.succeeded.connect(self._on_tidal_connected)
        self._worker.failed.connect(self._on_tidal_failed)
        self._worker.start()

    def _on_url_ready(self, url: str) -> None:
        self.status_label.setText("Waiting for you to approve login in the browser…")
        self.link_label.setText(f'If a browser tab did not open: <a href="{url}">{url}</a>')
        QDesktopServices.openUrl(QUrl(url))

    def _on_tidal_connected(self) -> None:
        self.connect_btn.setEnabled(True)
        self.status_label.setText("Connected to Tidal.")
        self.link_label.setText("")
        self.log.append("Connected to Tidal.")

    def _on_tidal_failed(self, message: str) -> None:
        self.connect_btn.setEnabled(True)
        self.status_label.setText("Not connected.")
        self.log.append(f"Tidal error: {message}")
        QMessageBox.critical(self, "Tidal error", message)

    def _on_match_clicked(self) -> None:
        if not self.tidal.is_connected:
            QMessageBox.information(self, "Not connected", "Connect to Tidal first.")
            return
        selected = _selected_top_level_items(self.tree)
        if not selected:
            QMessageBox.information(
                self, "Nothing selected", "Select at least one playlist to match."
            )
            return
        self.match_btn.setEnabled(False)
        self.log.append(f"Matching {len(selected)} playlist(s) against Tidal…")

        self._worker = MatchWorker(self.tidal, selected)
        self._worker.progress.connect(self.log.append)
        self._worker.playlist_progress.connect(self._on_playlist_progress)
        self._worker.playlist_matched.connect(self._on_playlist_matched)
        self._worker.succeeded.connect(self._on_match_succeeded)
        self._worker.failed.connect(self._on_match_failed)
        self._worker.start()

    def _on_playlist_progress(self, snapshot_id: str, i: int, total: int) -> None:
        item = self._items_by_snapshot_id.get(snapshot_id)
        if item is not None:
            item.setText(self._COL_STATUS, f"Matching… {i}/{total}")

    def _on_playlist_matched(self, result: PlaylistMatchResult) -> None:
        # Updates that one playlist's row/children as soon as its matching
        # finishes, rather than waiting for every selected playlist to be
        # done — a multi-playlist match run used to sit silent until the
        # very end.
        self.results[result.snapshot_id] = result
        item = self._items_by_snapshot_id.get(result.snapshot_id)
        if item is None:
            return
        item.setText(self._COL_STATUS, f"{result.matched_count}/{result.total} matched")
        # Matches refresh()'s population guard: inserting children while
        # sorting is live can trigger a resort per insert.
        self.tree.setSortingEnabled(False)
        self._populate_track_children(item, result)
        self.tree.setSortingEnabled(True)
        if result.matched_count < result.total:
            item.setExpanded(True)
        self._apply_filter()

    def _on_match_succeeded(self, results: list[PlaylistMatchResult]) -> None:
        self.match_btn.setEnabled(True)
        for result in results:
            for m in result.matches:
                if m.tidal_id is None:
                    self.log.append(f"    unmatched: {m.track.artist_display} — {m.track.title}")
        self.on_matched()

    def _on_match_failed(self, message: str) -> None:
        self.match_btn.setEnabled(True)
        self.log.append(f"Match error: {message}")
        QMessageBox.critical(self, "Match error", message)

    def _on_remove_clicked(self) -> None:
        selected = _selected_top_level_items(self.tree)
        if not selected:
            QMessageBox.information(
                self, "Nothing selected", "Select at least one playlist to remove."
            )
            return
        names = ", ".join(f"“{s.name}”" for s in selected)
        reply = QMessageBox.question(
            self,
            "Remove imported playlists",
            f"Remove {len(selected)} imported playlist(s) ({names})?\n\n"
            "This only deletes the local copy — nothing on Spotify or Tidal is "
            "affected. You'll need to re-import from the Harvest tab to bring "
            "them back.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        for snap in selected:
            store.delete_snapshot(snap.id)
            self.results.pop(snap.id, None)
        self.log.append(f"Removed: {names}")
        self.refresh()
        self.on_matched()  # keep Push tab in sync with any results just dropped


class PushTab(QWidget):
    """Push only ever commits playlists that already have a match result —
    it does no matching or Tidal searching of its own."""

    _COL_NAME = 0
    _COL_OWNER = 1
    _COL_MATCHED = 2
    _COL_TOTAL = 3

    def __init__(self, log: QTextEdit, tidal: TidalClient, get_results):
        super().__init__()
        self.log = log
        self.tidal = tidal
        self.get_results = get_results
        self.results: list[PlaylistMatchResult] = []
        self._worker = None

        self.table = _make_table(["Name", "Owner", "Matched", "Total"])
        _enable_sorting_without_resorting(self.table)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_top_row())
        layout.addWidget(self._build_folder_input())
        layout.addWidget(self._build_filter_input())
        layout.addLayout(self._build_select_row())
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self._build_push_button())

        self.refresh()

    def _build_top_row(self) -> QHBoxLayout:
        info = QLabel("Playlists with completed matching from the Match tab appear here.")
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(info, stretch=1)
        row.addWidget(refresh_btn)
        return row

    def _build_folder_input(self) -> QLineEdit:
        self.folder_input = QLineEdit()
        self.folder_input.setPlaceholderText(
            "Tidal folder (blank = each playlist's harvested owner)"
        )
        return self.folder_input

    def _build_filter_input(self) -> QLineEdit:
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter playlists (fuzzy match on name)…")
        self.filter_input.textChanged.connect(self._apply_filter)
        return self.filter_input

    def _build_select_row(self) -> QHBoxLayout:
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self.table.selectAll)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(self.table.clearSelection)
        row = QHBoxLayout()
        row.addWidget(select_all_btn)
        row.addWidget(select_none_btn)
        row.addStretch(1)
        return row

    def _build_push_button(self) -> QPushButton:
        self.push_btn = QPushButton("Push Selected to Tidal (as public playlists)")
        self.push_btn.clicked.connect(self._on_push_clicked)
        return self.push_btn

    def refresh(self) -> None:
        self.results = self.get_results()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            self.table.setItem(row, self._COL_NAME, _table_item(result.name, data=result))
            self.table.setItem(row, self._COL_OWNER, _table_item(result.owner))
            matched_item = QTableWidgetItem()
            matched_item.setData(Qt.ItemDataRole.DisplayRole, result.matched_count)
            self.table.setItem(row, self._COL_MATCHED, matched_item)
            total_item = QTableWidgetItem()
            total_item.setData(Qt.ItemDataRole.DisplayRole, result.total)
            self.table.setItem(row, self._COL_TOTAL, total_item)
        _autofit_columns(self.table)
        _enable_sorting_without_resorting(self.table)
        self._apply_filter()
        if self.results:
            self.folder_input.setPlaceholderText(
                f"Tidal folder (blank = each playlist's harvested owner, "
                f"e.g. “{self.results[0].owner}”)"
            )

    def _apply_filter(self) -> None:
        query = self.filter_input.text().strip().lower()
        for row in range(self.table.rowCount()):
            result: PlaylistMatchResult = self.table.item(row, self._COL_NAME).data(
                Qt.ItemDataRole.UserRole
            )
            hide = bool(query) and (
                fuzz.partial_ratio(query, result.name.lower()) < FILTER_MATCH_THRESHOLD
            )
            self.table.setRowHidden(row, hide)

    def _on_push_clicked(self) -> None:
        if not self.tidal.is_connected:
            QMessageBox.information(
                self, "Not connected", "Connect to Tidal on the Match tab first."
            )
            return
        selected = _selected_table_items(self.table)
        if not selected:
            QMessageBox.information(
                self, "Nothing selected", "Select at least one playlist to push."
            )
            return
        folder_name = self.folder_input.text().strip() or None
        self.push_btn.setEnabled(False)
        self.log.append(f"Pushing {len(selected)} playlist(s) to Tidal…")

        self._worker = PushWorker(self.tidal, selected, folder_name)
        self._worker.progress.connect(self.log.append)
        self._worker.succeeded.connect(self._on_push_succeeded)
        self._worker.failed.connect(self._on_push_failed)
        self._worker.start()

    def _on_push_succeeded(self, reports) -> None:
        self.push_btn.setEnabled(True)
        for report in reports:
            verb = "created" if report.created else "updated"
            self.log.append(
                f"“{report.playlist_name}” {verb}: {report.matched}/{report.total} matched, "
                f"{report.added_count} new track(s)."
            )
        QMessageBox.information(
            self, "Push complete", "Finished pushing selected playlists to Tidal."
        )

    def _on_push_failed(self, message: str) -> None:
        self.push_btn.setEnabled(True)
        self.log.append(f"Push error: {message}")
        QMessageBox.critical(self, "Push error", message)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Spotify → Tidal Playlist Migrator")
        self.resize(920, 640)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)

        self.tidal = TidalClient()

        self.harvest_tab = HarvestTab(self.log, on_imported=self._on_imported)
        self.match_tab = MatchTab(self.log, self.tidal, on_matched=self._on_matched)
        self.push_tab = PushTab(
            self.log, self.tidal, get_results=lambda: list(self.match_tab.results.values())
        )

        self.tabs = QTabWidget()
        self.tabs.addTab(self.harvest_tab, "1. Harvest")
        self.tabs.addTab(self.match_tab, "2. Match")
        self.tabs.addTab(self.push_tab, "3. Push")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.tabs, stretch=1)
        layout.addWidget(QLabel("Log"))
        layout.addWidget(self.log)
        self.setCentralWidget(central)

    def _on_imported(self) -> None:
        self.match_tab.refresh()

    def _on_matched(self) -> None:
        self.push_tab.refresh()

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.push_tab:
            self.push_tab.refresh()
