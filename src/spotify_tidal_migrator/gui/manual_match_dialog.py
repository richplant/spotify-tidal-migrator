from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ..models import TrackInfo
from ..tidal_client import TidalClient
from .workers import ManualSearchWorker


def _track_label(track) -> str:
    artists = ", ".join(a.name for a in (track.artists or []))
    duration = f"{track.duration // 60}:{track.duration % 60:02d}" if track.duration else "?:??"
    album = track.album.name if track.album else ""
    return f"{artists} — {track.name} ({album}, {duration})"


class ManualMatchDialog(QDialog):
    """Lets the user search Tidal directly and pick the correct match by
    hand, for when the automatic ISRC/fuzzy match is wrong or missing."""

    def __init__(self, tidal: TidalClient, track: TrackInfo, parent=None):
        super().__init__(parent)
        self.tidal = tidal
        self.track = track
        self.selected_track = None
        self._worker: ManualSearchWorker | None = None

        self.setWindowTitle(f"Manual match — {track.artist_display} - {track.title}")
        self.resize(560, 420)

        self.query_input = QLineEdit(f"{track.artist_display} {track.title}")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._on_search_clicked)

        search_row = QHBoxLayout()
        search_row.addWidget(self.query_input, stretch=1)
        search_row.addWidget(self.search_btn)

        self.status_label = QLabel("Searching…")
        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_list.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(search_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.results_list, stretch=1)
        layout.addWidget(self.button_box)

        self._run_search(self.query_input.text())

    def _on_search_clicked(self) -> None:
        self._run_search(self.query_input.text())

    def _run_search(self, query: str) -> None:
        query = query.strip()
        if not query:
            return
        self.results_list.clear()
        self.selected_track = None
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.status_label.setText("Searching…")
        # Disabled for the duration so a second click can't start an
        # overlapping search — if it did, whichever worker's signal fires
        # last would win regardless of which query is newer, merging or
        # overwriting results unpredictably.
        self.search_btn.setEnabled(False)

        self._worker = ManualSearchWorker(self.tidal, query)
        self._worker.succeeded.connect(self._on_search_succeeded)
        self._worker.failed.connect(self._on_search_failed)
        self._worker.start()

    def _on_search_succeeded(self, tracks: list) -> None:
        self.search_btn.setEnabled(True)
        self.status_label.setText(
            f"{len(tracks)} result(s)." if tracks else "No results — try adjusting the search."
        )
        for t in tracks:
            item = QListWidgetItem(_track_label(t))
            item.setData(Qt.ItemDataRole.UserRole, t)
            self.results_list.addItem(item)

    def _on_search_failed(self, message: str) -> None:
        self.search_btn.setEnabled(True)
        self.status_label.setText(f"Search error: {message}")

    def _on_selection_changed(self) -> None:
        items = self.results_list.selectedItems()
        self.selected_track = items[0].data(Qt.ItemDataRole.UserRole) if items else None
        self.button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.selected_track is not None
        )

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        self.selected_track = item.data(Qt.ItemDataRole.UserRole)
        self.accept()
