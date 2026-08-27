from __future__ import annotations

import html
import re
from collections.abc import Iterator

_USER_URL_RE = re.compile(r"open\.spotify\.com/user/([A-Za-z0-9_-]+)")

_BLOCK_BREAK_RE = re.compile(r"<\s*(br|/p|/div)\s*/?\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

# Matches a bearer token / client-token however DevTools happened to hand it
# over: raw "Name: value" header text, a "copy as fetch" JS object
# ("authorization": "Bearer …"), or "copy as cURL" (-H 'authorization: …').
# The value is whatever comes after up to the next quote, comma, or newline.
_BEARER_TOKEN_RE = re.compile(
    r"""authorization["']?\s*[:=]\s*["']?Bearer\s+([^"'\r\n,]+)""", re.IGNORECASE
)
_CLIENT_TOKEN_RE = re.compile(r"""client-token["']?\s*[:=]\s*["']?([^"'\r\n,]+)""", re.IGNORECASE)


def parse_spotify_user_id(text: str) -> str:
    """Accepts either a raw Spotify user id or a profile URL
    (e.g. https://open.spotify.com/user/123059001?si=...) and returns the id."""
    text = text.strip()
    match = _USER_URL_RE.search(text)
    if match:
        return match.group(1)
    return text.split("?")[0].rstrip("/")


def extract_bearer_token(pasted_request: str) -> str | None:
    """Pulls the access token out of a pasted request (raw headers, "copy as
    fetch", or "copy as cURL") that contains an authorization header."""
    match = _BEARER_TOKEN_RE.search(pasted_request)
    return match.group(1).strip() if match else None


def extract_client_token(pasted_request: str) -> str | None:
    """Pulls the client-token out of a pasted request the same way."""
    match = _CLIENT_TOKEN_RE.search(pasted_request)
    return match.group(1).strip() if match else None


def bounded_pages(context: str, max_pages: int = 1000) -> Iterator[int]:
    """Yields 0, 1, 2, ... for use as a pagination loop's `for` clause, then
    raises RuntimeError once max_pages is exhausted -- a safety bound so a
    server that kept returning full pages forever (bug, or the underlying
    data shifting mid-pagination) can't hang the caller indefinitely."""
    yield from range(max_pages)
    raise RuntimeError(f"{context} did not terminate after {max_pages} pages")


def html_to_plain_text(raw: str) -> str:
    """Converts Spotify's HTML-formatted playlist descriptions (entities,
    <a href="..."> tags for tagged artists/tracks, <br>/<p> line breaks) into
    plain text, for contexts that can't render HTML — e.g. Tidal's playlist
    description field. The GUI renders the raw HTML directly instead of
    calling this; it's only for text-only consumers."""
    text = _BLOCK_BREAK_RE.sub("\n", raw)
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text).strip()
