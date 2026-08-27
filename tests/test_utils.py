import pytest

from spotify_tidal_migrator.utils import (
    extract_bearer_token,
    extract_client_token,
    html_to_plain_text,
    parse_spotify_user_id,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("123059001", "123059001"),
        ("https://open.spotify.com/user/123059001", "123059001"),
        ("https://open.spotify.com/user/123059001?si=abc123", "123059001"),
        ("https://open.spotify.com/user/123059001/", "123059001"),
        ("  123059001  ", "123059001"),
        ("open.spotify.com/user/123059001", "123059001"),
        ("http://open.spotify.com/user/some_user-name", "some_user-name"),
    ],
)
def test_parse_spotify_user_id(text, expected):
    assert parse_spotify_user_id(text) == expected


def test_parse_spotify_user_id_empty_string_returns_empty():
    assert parse_spotify_user_id("") == ""

    assert parse_spotify_user_id("   ") == ""


RAW_HEADERS = """
POST /pathfinder/v2/query HTTP/3
Host: api-partner.spotify.com
client-token: AAEAtokenvalue==
authorization: Bearer BQAaccesstokenvalue
Connection: keep-alive
"""

FETCH_JS = """
await fetch("https://api-partner.spotify.com/pathfinder/v2/query", {
    "headers": {
        "client-token": "AAEAtokenvalue==",
        "authorization": "Bearer BQAaccesstokenvalue"
    },
    "method": "POST"
});
"""

CURL = """
curl 'https://api-partner.spotify.com/pathfinder/v2/query' \\
  -H 'client-token: AAEAtokenvalue==' \\
  -H 'authorization: Bearer BQAaccesstokenvalue'
"""


@pytest.mark.parametrize("pasted", [RAW_HEADERS, FETCH_JS, CURL])
def test_extract_bearer_token_across_paste_formats(pasted):
    assert extract_bearer_token(pasted) == "BQAaccesstokenvalue"


@pytest.mark.parametrize("pasted", [RAW_HEADERS, FETCH_JS, CURL])
def test_extract_client_token_across_paste_formats(pasted):
    assert extract_client_token(pasted) == "AAEAtokenvalue=="


def test_extract_tokens_returns_none_when_missing():
    assert extract_bearer_token("no relevant headers here") is None
    assert extract_client_token("no relevant headers here") is None


def test_html_to_plain_text_unescapes_entities_and_strips_tags():
    raw = 'Rock &amp; Roll mix feat. <a href="spotify:artist:123">Some Artist</a> &#39;n friends'
    assert html_to_plain_text(raw) == "Rock & Roll mix feat. Some Artist 'n friends"


def test_html_to_plain_text_converts_line_breaks():
    raw = "Side A<br>Side B<br/>Side C"
    assert html_to_plain_text(raw) == "Side A\nSide B\nSide C"


def test_html_to_plain_text_handles_plain_text_unchanged():
    assert html_to_plain_text("just plain text") == "just plain text"
