"""Choosing which RELEASE of an album to grab, instead of letting Commissary guess.

The track picker can't serve albums. Its candidates are individual files, so
picking one for an album would import a single track named after the album.
Albums instead need the ``AlbumResult`` list every plugin already returns from
``search()`` — the half the track picker discards — and a way to carry that
choice into the album-bundle flow that already exists.

The load-bearing decisions pinned here:

* A pin travels as an opaque candidate-store TOKEN, never a download URL, so
  indexer API keys stay server-side (P0-03) and a forged token simply fails.
* A pinned release beats the configured download source. ``download_source.mode``
  answers "which source may claim a whole album unattended?", which stops being
  the right question once a person has named the release they want.
* A stale token FAILS rather than falling back to a heuristic pick — grabbing a
  release the user didn't choose is worse than telling them the choice expired.
"""

from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOWNLOADS_JS = (_ROOT / "webui" / "static" / "downloads.js").read_text(encoding="utf-8")
_SEARCH_JS = (_ROOT / "webui" / "static" / "search.js").read_text(encoding="utf-8")
_CSS = (_ROOT / "webui" / "static" / "style.css").read_text(encoding="utf-8")


# ── the pin is normalised, not trusted ───────────────────────────────────────
@pytest.fixture(scope="module")
def normalize():
    from web_server import _normalize_pinned_release
    return _normalize_pinned_release


def test_a_prowlarr_pin_needs_a_token(normalize):
    assert normalize({"source": "torrent", "token": "abc", "title": "X"}) == {
        "source": "torrent", "token": "abc", "title": "X"}
    assert normalize({"source": "torrent", "title": "X"}) is None


def test_a_download_url_is_never_accepted(normalize):
    """The browser may hold a token, never the indexer URL it stands for —
    those carry API keys and signed params."""
    out = normalize({"source": "torrent", "token": "abc",
                     "download_url": "https://indexer/dl?apikey=SECRET"})
    assert out is not None
    assert "download_url" not in out
    assert "SECRET" not in repr(out)


def test_a_soulseek_pin_needs_a_peer_and_a_folder(normalize):
    assert normalize({"source": "soulseek", "username": "bob",
                      "folder_path": "Music/Album"})["username"] == "bob"
    assert normalize({"source": "soulseek", "username": "bob"}) is None
    assert normalize({"source": "soulseek", "folder_path": "x"}) is None


def test_an_unknown_source_is_refused(normalize):
    """Only sources with an album-bundle flow can be pinned to a release.
    Anything else degrades to 'choose automatically' — today's behaviour."""
    for bad in ({"source": "youtube", "token": "t"},
                {"source": "", "token": "t"},
                {"token": "t"}, None, "torrent", 42, []):
        assert normalize(bad) is None


def test_the_title_cannot_be_used_to_smuggle_a_payload(normalize):
    out = normalize({"source": "usenet", "token": "t", "title": "A" * 5000})
    assert len(out["title"]) <= 300


# ── a pin outranks the configured source ─────────────────────────────────────
def test_a_pin_resolves_to_a_source_and_plugin_kwargs():
    from core.downloads.master import _pinned_album_bundle

    src, kwargs = _pinned_album_bundle({"source": "torrent", "token": "tok",
                                        "title": "Artist - Album [FLAC]"})
    assert src == "torrent"
    assert kwargs == {"preferred_release": {"token": "tok",
                                            "title": "Artist - Album [FLAC]"}}


def test_a_soulseek_pin_sends_no_track_list():
    """Given a username + folder_path the Soulseek album flow browses the
    folder itself, so the whole track list never has to survive a round trip
    through the browser."""
    from core.downloads.master import _pinned_album_bundle

    src, kwargs = _pinned_album_bundle({"source": "soulseek", "username": "bob",
                                        "folder_path": "Music/Album"})
    assert src == "soulseek"
    assert kwargs == {"preferred_source": {"username": "bob",
                                           "folder_path": "Music/Album"}}
    assert "preferred_tracks" not in kwargs


def test_a_malformed_pin_degrades_to_choosing_automatically():
    """It must never wedge the batch — falling back to the configured source
    is exactly what happens today with no pin at all."""
    from core.downloads.master import _pinned_album_bundle

    for bad in (None, {}, {"source": "torrent"}, {"source": "youtube", "token": "t"},
                {"source": "soulseek", "username": "bob"}, "nonsense"):
        assert _pinned_album_bundle(bad) == ("", None)


def test_a_user_pick_bypasses_the_mode_gate():
    """The mode gate stops an UNATTENDED batch claiming a whole release from a
    source the user didn't nominate. Once they've personally chosen a release
    from that source, the gate is backwards."""
    from core.downloads.album_bundle_dispatch import is_eligible

    common = dict(is_album=True, album_name="Album", artist_name="Artist")
    assert is_eligible(mode="qobuz", **common) is False
    assert is_eligible(mode="qobuz", user_picked=True, **common) is True


def test_a_user_pick_does_not_bypass_the_other_requirements():
    """Only the MODE check relaxes. Staging and per-track matching still need
    an album and both names."""
    from core.downloads.album_bundle_dispatch import is_eligible

    assert is_eligible(mode="torrent", is_album=False, album_name="A",
                       artist_name="B", user_picked=True) is False
    assert is_eligible(mode="torrent", is_album=True, album_name="",
                       artist_name="B", user_picked=True) is False
    assert is_eligible(mode="torrent", is_album=True, album_name="A",
                       artist_name="", user_picked=True) is False


def test_dispatch_reads_the_pin_from_plugin_kwargs():
    """try_dispatch decides user_picked from the kwargs it's already given,
    so no caller has to remember to pass a second, redundant flag."""
    from core.downloads import album_bundle_dispatch as abd

    calls = {}

    class _Plugin:
        def download_album_to_staging(self, album, artist, staging, cb, **kw):
            calls.update(kw)
            return {"success": True, "files": ["/x/1.flac"]}

    class _State:
        def update_fields(self, batch_id, fields): pass
        def mark_failed(self, batch_id, error): pass

    abd.try_dispatch(
        batch_id="b1", is_album=True,
        album_context={"name": "Album"}, artist_context={"name": "Artist"},
        # A mode that would normally be refused outright.
        config_get=lambda k, d=None: "qobuz" if k == "download_source.mode" else d,
        plugin_resolver=lambda name: _Plugin(),
        state=_State(),
        source_override="torrent",
        plugin_kwargs={"preferred_release": {"token": "tok", "title": "T"}},
    )
    assert calls == {"preferred_release": {"token": "tok", "title": "T"}}


# ── the plugins honour the pin ───────────────────────────────────────────────
def _plugin_source(name: str) -> str:
    return (_ROOT / "core" / "download_plugins" / f"{name}.py").read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Strip ``#`` comments so an assertion can't be satisfied — or defeated —
    by prose. The comment on the stale-token branch explains why there is no
    `fallback` flag, and naming it there made the "no fallback here" assertion
    fail against its own documentation."""
    out = []
    for line in src.split("\n"):
        hash_at = line.find("#")
        # Only strip a '#' that starts a comment, not one inside a string.
        if hash_at != -1 and line[:hash_at].count("'") % 2 == 0 \
                and line[:hash_at].count('"') % 2 == 0:
            line = line[:hash_at]
        out.append(line)
    return "\n".join(out)


def test_the_comment_stripper_strips_comments_not_strings():
    """Pin the helper above — a broken stripper would silently weaken every
    assertion that uses it."""
    assert _code_only("x = 1  # fallback").strip() == "x = 1"
    assert _code_only("y = '# not a comment'").strip() == "y = '# not a comment'"
    assert _code_only("# whole line").strip() == ""


@pytest.mark.parametrize("plugin", ["torrent", "usenet"])
def test_a_pinned_release_skips_the_search_and_the_heuristic(plugin):
    src = _plugin_source(plugin)
    body = src.split("def download_album_to_staging(", 1)[1]
    assert "preferred_release" in body
    # The whole point: neither the Prowlarr query nor pick_best_album_release
    # runs when the user already answered the question they exist to guess at.
    pinned_branch = body.split("if preferred_release:", 1)[1].split("else:", 1)[0]
    assert "pick_best_album_release" not in pinned_branch
    assert "self._prowlarr.search" not in pinned_branch
    assert "get_candidate_store().resolve" in pinned_branch


@pytest.mark.parametrize("plugin", ["torrent", "usenet"])
def test_a_stale_pin_fails_instead_of_grabbing_something_else(plugin):
    """No `fallback` flag on this path. Falling back would silently download a
    release the user did not choose, which is worse than saying it expired."""
    src = _code_only(_plugin_source(plugin))
    body = src.split("def download_album_to_staging(", 1)[1]
    branch = body.split("if preferred_release:", 1)[1].split("else:", 1)[0]
    assert "no longer available" in branch
    assert "fallback" not in branch


# ── the pick reaches the batch ───────────────────────────────────────────────
def test_the_picker_exists_and_is_global():
    assert "async function openAlbumSourcePicker(" in _DOWNLOADS_JS
    assert "window.openAlbumSourcePicker = openAlbumSourcePicker;" in _DOWNLOADS_JS
    assert "window.setPendingAlbumPin = setPendingAlbumPin;" in _DOWNLOADS_JS


def test_the_pin_is_consumed_once():
    """Left in place it would silently re-pin a stale release the next time the
    same album is downloaded."""
    block = _DOWNLOADS_JS.split("if (_pendingAlbumPins[playlistId]) {", 1)[1][:400]
    assert "requestBody.pinned_release" in block
    assert "delete _pendingAlbumPins[playlistId]" in block


def test_choosing_automatically_is_still_offered():
    """No source having the album as one release is a normal outcome, not a
    dead end — track-by-track still works."""
    body = _DOWNLOADS_JS.split("async function openAlbumSourcePicker(", 1)[1]
    body = body[:body.index("\nwindow.openAlbumSourcePicker")]
    assert "album-sources-auto" in body
    assert "onPicked(null)" in body


def test_an_unpinnable_source_does_not_pretend_to_pin_a_release():
    """amazon / lidarr have no download_album_to_staging. Offering them is
    honest; implying we pinned their release would not be."""
    body = _DOWNLOADS_JS.split("async function openAlbumSourcePicker(", 1)[1]
    assert "c.pinnable ? 'Use this' : 'Use this source'" in body
    assert "album-source-row-unpinnable" in body


def test_album_cards_open_the_album_picker_not_the_track_one():
    """The distinction this whole module exists for."""
    assert "_openSourcesForAlbum" in _SEARCH_JS
    body = _SEARCH_JS.split("async function _openSourcesForAlbum(", 1)[1][:900]
    assert "openAlbumSourcePicker(" in body
    assert "openManualSearchFor(" not in body
    assert "setPendingAlbumPin(" in body


def test_the_picker_is_styled():
    for cls in ("album-source-row", "album-source-row-unpinnable",
                "album-source-pick-btn", "album-sources-footer"):
        assert "." + cls in _CSS, cls
