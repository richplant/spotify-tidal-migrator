from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP_NAME = "spotify-tidal-migrator"

CONFIG_DIR = Path(user_config_dir(APP_NAME))
DATA_DIR = Path(user_data_dir(APP_NAME))

TIDAL_TOKEN_PATH = CONFIG_DIR / "tidal_token.json"
PLAYLIST_STORE_DIR = DATA_DIR / "playlists"
# Remembers which Tidal playlist a given Spotify snapshot was already
# pushed to, so re-pushing looks it up by id instead of by name -- matching
# purely by name risks silently merging into an unrelated, pre-existing
# Tidal playlist that just happens to share a common name (e.g. "Chill").
PUSH_MAPPINGS_PATH = DATA_DIR / "push_mappings.json"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PLAYLIST_STORE_DIR.mkdir(parents=True, exist_ok=True)
