""""Reset to the standard layout" must post the standard layout.

The Library Organization card's reset button used to POST an explicit copy of
every setting — a second set of defaults, written in JavaScript, sitting a
directory away from the real one in ``core/video/organization.DEFAULTS``. It
drifted, exactly the way a duplicated constant does:

  * ``save_artwork: false`` and ``write_nfo: false`` were posted against a
    ``True`` default, so the button labelled "reset" silently TURNED OFF the nfo
    and artwork sidecars — the opposite of restoring the standard layout, and
    invisible until someone noticed their library had stopped getting sidecars.
  * ``min_free_disk_gb: 0`` was posted at all. That key is not video's: it is the
    app-wide ``settings.min_free_disk_gb`` shared with the music side (see
    ``_SHARED_MIN_FREE_KEY``), and ``organization.save()`` writes the shared key
    whenever the posted body carries it. Resetting a video NAMING card therefore
    dropped music's disk floor to 0, disabling its guard.

The fix posts only the blank templates and lets ``normalize()`` fill the rest
from DEFAULTS — which is what the handler's comment always claimed it did. These
tests pin the invariant that broke: the reset payload cannot disagree with
DEFAULTS. They read the JS source (the idiom in
``test_video_naming_template_persist.py``) so the two constants are compared
directly rather than through a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.video.organization import DEFAULTS, normalize

_ROOT = Path(__file__).resolve().parents[1]
_JS = (_ROOT / "webui" / "static" / "video" / "video-settings.js").read_text(encoding="utf-8")

_TEMPLATE_KEYS = ("movie_template", "episode_template", "youtube_template")


def _reset_handler() -> str:
    """The vo-reset click handler, at the end of wireOrganization()."""
    start = _JS.index("document.getElementById('vo-reset')")
    return _JS[start:_JS.index("\n    }", start)]


def _js_literal(text: str):
    """Parse one JS scalar — string, boolean or number — into its Python value."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        return float(text)


def _reset_payload() -> dict:
    """The object literal the reset handler POSTs, as a Python dict."""
    body = _reset_handler()
    open_brace = body.index("{", body.index("JSON.stringify("))
    depth, end = 0, None
    for i in range(open_brace, len(body)):
        if body[i] == "{":
            depth += 1
        elif body[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    assert end is not None, "unbalanced object literal in the vo-reset payload"
    inner = body[open_brace + 1:end].strip()
    if not inner:
        return {}
    out = {}
    for entry in inner.split(","):
        entry = entry.strip()
        if not entry:
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", entry, re.S)
        assert m, "could not parse %r out of the vo-reset payload" % entry
        out[m.group(1)] = _js_literal(m.group(2))
    return out


@pytest.fixture(scope="module")
def payload() -> dict:
    return _reset_payload()


def test_payload_parses_and_is_not_empty(payload):
    """Guard the guard: if the handler is rewritten into a shape this file cannot
    read, the tests below would pass vacuously on an empty dict."""
    assert payload, "the reset must still POST a body"
    assert set(payload) <= set(DEFAULTS), (
        "the reset posts %s, which are not settings at all"
        % sorted(set(payload) - set(DEFAULTS))
    )


def test_no_posted_value_disagrees_with_defaults(payload):
    """THE invariant. Any key the reset restates must carry the default's exact
    value — a blank template is the sanctioned exception, since normalize() reads
    blank as "use the default"."""
    for key, posted in payload.items():
        if key in _TEMPLATE_KEYS and posted == "":
            continue
        expected = DEFAULTS[key]
        assert type(posted) is type(expected) and posted == expected, (
            "reset posts %s=%r but the standard layout is %r — the button would "
            "not restore the default it claims to" % (key, posted, expected)
        )


def test_reset_does_not_rewrite_the_shared_disk_floor(payload):
    """min_free_disk_gb lives on settings.min_free_disk_gb, shared with music, and
    save() writes that shared key iff the body carries it. Even posting the
    matching default 0 is wrong here: music's default floor is non-zero, so a
    reset of the video naming card would disable music's guard."""
    assert "min_free_disk_gb" not in payload, (
        "the video reset must leave the shared music/video disk floor alone — "
        "omit the key so organization.save() does not touch settings.min_free_disk_gb"
    )


def test_the_reset_actually_restores_every_default(payload):
    """End to end through the real backend coercion: whatever the button posts,
    the settings that come back are the standard layout, key for key."""
    assert normalize(payload) == DEFAULTS


def test_reset_leaves_the_defaults_to_the_backend(payload):
    """The structural half of the fix. Even a payload that happens to agree with
    DEFAULTS today is a second copy waiting to drift, so the handler is held to
    posting nothing but the blank templates."""
    assert set(payload) <= set(_TEMPLATE_KEYS), (
        "the reset restates %s in JavaScript; drop them and let normalize() fill "
        "them from DEFAULTS" % sorted(set(payload) - set(_TEMPLATE_KEYS))
    )
    for key in _TEMPLATE_KEYS:
        assert payload.get(key) == "", (
            "%s must be posted blank — that is what tells normalize() to reinstate "
            "the default template" % key
        )
