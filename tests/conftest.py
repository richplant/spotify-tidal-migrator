from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from spotify_tidal_migrator import paths


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Redirect all on-disk state under a per-test tmp dir so tests never
    touch the real user config/data directories."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(paths, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(paths, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "TIDAL_TOKEN_PATH", config_dir / "tidal_token.json")
    monkeypatch.setattr(paths, "PLAYLIST_STORE_DIR", data_dir / "playlists")
    monkeypatch.setattr(paths, "PUSH_MAPPINGS_PATH", data_dir / "push_mappings.json")
    yield


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
