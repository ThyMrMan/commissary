"""Placing a season folder under a season number the user chose.

Reported as: "tried to import a season that is called Season 8 as season 7, and
it ignored me and imported as season 8 regardless."

It did, and at two layers at once:

* ``run_season_import`` overwrote the placement's season with the one parsed
  out of each FILENAME, so the number the user assigned was replaced by the
  number they were trying to correct. The docstring asserted this was correct.
* the Place dialog offered no season field for a folder at all, and never sent
  one — the pack panel was a read-only list.

The reasoning behind the original behaviour is half right, and the half that is
right is load-bearing: EPISODE numbers must come from each file, because
stamping one across a pack files the whole season on top of itself. SEASON is
not that kind of fact. It describes the pack, not its members, so one number
does describe it — which is exactly why a release group numbering differently
from TMDB makes this a routine correction rather than an exotic one.

What is NOT allowed is renumbering a pack that spans several seasons: one
number cannot describe them, and applying it anyway files S07E01 and S08E01 at
the same path, the second overwriting the first. That refuses loudly rather
than silently picking one, since silently ignoring the number is the bug.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from core.video.importer import plan_import, run_season_import

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _js(*parts) -> str:
    """webui source with line endings normalised — LF in git, CRLF on a Windows
    checkout, so an anchor spanning a newline would pass on CI and fail here."""
    return _ROOT.joinpath(*parts).read_text(encoding="utf-8").replace("\r\n", "\n")


_IMPORT_JS = _js("webui", "static", "video", "video-import.js")
_CSS = _js("webui", "static", "video", "video-side.css")


def _dl():
    return {"kind": "episode", "media_id": "500", "media_source": "tmdb",
            "target_dir": "/tv", "release_title": "My.Show.S08E01.1080p.WEB.x264",
            "size_bytes": 3_000_000_000,
            "search_ctx": json.dumps({"scope": "episode", "title": "My Show",
                                      "season": 8, "episode": 1})}


class _FS:
    """Records destinations; copies nothing."""
    def __init__(self):
        self.wrote = []

    def list_dir(self, p): return []
    def makedirs(self, p): pass
    def copy(self, s, d): self.wrote.append(d)
    def move(self, s, d): self.wrote.append(d)
    def remove(self, p): pass
    def write_text(self, p, c): pass
    def save_url(self, u, d): pass


def _members(*specs, show="My Show"):
    """specs are (season, episode) or (season, episode, episode_end)."""
    out = []
    for s in specs:
        if len(s) == 3:
            out.append("/dl/pack/%s.S%02dE%02d-E%02d.1080p.WEB.x264.mkv"
                       % (show.replace(" ", "."), s[0], s[1], s[2]))
        else:
            out.append("/dl/pack/%s.S%02dE%02d.1080p.WEB.x264.mkv"
                       % (show.replace(" ", "."), s[0], s[1]))
    return out


def _place(files, season=None, *, src="/dl/pack"):
    fs = _FS()
    override = {"scope": "season", "title": "My Show", "media_id": "500",
                "target_dir": "/tv"}
    if season is not None:
        override["season"] = season
    patch = run_season_import(_dl(), src, fs=fs, lister=lambda root: list(files),
                              settings={}, force=True, override=override,
                              size_of=lambda p: 3_000_000_000)
    return patch, fs.wrote


def _seasons_written(paths):
    import re
    return sorted({int(m.group(1)) for p in paths
                   for m in [re.search(r"Season (\d+)", p)] if m})


# ── the report ──────────────────────────────────────────────────────────────

def test_a_season_folder_is_filed_under_the_season_the_user_chose():
    """THE bug. A folder whose filenames all say S08, placed as season 7."""
    _patch, wrote = _place(_members((8, 1), (8, 2), (8, 3)), season=7)
    assert len(wrote) == 3
    assert _seasons_written(wrote) == [7], wrote
    assert all("S07E" in p for p in wrote), wrote


def test_each_file_still_keeps_its_own_episode_number():
    """The half of the original reasoning that was right. One episode number
    across a pack files the whole season on top of itself."""
    _patch, wrote = _place(_members((8, 1), (8, 2), (8, 3)), season=7)
    assert sorted(p.split("S07E")[1][:2] for p in wrote) == ["01", "02", "03"]


def test_without_a_season_nothing_about_today_changes():
    """The automatic path passes no season override at all; it must keep filing
    by what each filename says."""
    _patch, wrote = _place(_members((8, 1), (8, 2)))
    assert _seasons_written(wrote) == [8], wrote


def test_confirming_the_detected_season_is_a_no_op():
    """The dialog pre-fills the detected number, so most placements send back
    the season they were given. That must behave exactly as sending none."""
    _p1, a = _place(_members((8, 1), (8, 2)), season=8)
    _p2, b = _place(_members((8, 1), (8, 2)))
    assert a == b


def test_season_zero_is_a_real_answer_not_a_missing_one():
    """Specials. `if season:` would silently drop this one."""
    _patch, wrote = _place(_members((8, 1), (8, 2)), season=0)
    assert _seasons_written(wrote) == [0], wrote


# ── the refusal that stops a renumber destroying files ──────────────────────

def test_a_pack_spanning_seasons_refuses_the_renumber():
    """S07E01 and S08E01 both becoming S07E01 is one file overwriting the
    other. One number cannot describe two seasons, so it says so."""
    patch, wrote = _place(_members((7, 1), (8, 1)), season=5)
    assert patch["status"] == "import_failed"
    assert wrote == [], "nothing may be copied before the refusal"
    assert "seasons 7, 8" in patch["error"]
    assert "on top of each other" in patch["error"]


def test_the_refusal_happens_before_anything_is_copied():
    """Discovering it halfway through would leave half a pack renumbered and
    half not — worse than either outcome."""
    patch, wrote = _place(_members((7, 1), (7, 2), (8, 1), (8, 2)), season=5)
    assert patch["status"] == "import_failed" and wrote == []


def test_a_multi_season_pack_without_a_renumber_still_imports():
    """The refusal is about the OVERRIDE, not about mixed packs — a full-series
    folder has always been importable and must stay so."""
    _patch, wrote = _place(_members((7, 1), (8, 1)))
    assert _seasons_written(wrote) == [7, 8], wrote


def test_a_junk_season_is_ignored_rather_than_crashing():
    """The field is a number input, but the endpoint takes JSON from a client."""
    _patch, wrote = _place(_members((8, 1)), season="not a number")
    assert _seasons_written(wrote) == [8], wrote


# ── the span that a renumber would otherwise silently drop ──────────────────

def test_a_double_episode_keeps_its_span_through_a_renumber():
    """plan_import only trusts a parsed span when the parsed season agrees with
    the one being filed — and under a renumber they deliberately disagree. Left
    alone, S08E01-E02 would be filed as a single S07E01, quietly losing the
    second episode from the name the media server reads."""
    _patch, wrote = _place(_members((8, 1, 2), (8, 3)), season=7)
    joined = " ".join(wrote)
    assert "S07E01-E02" in joined, wrote
    assert "S07E03" in joined, wrote


def test_a_double_episode_is_unaffected_when_nothing_is_renumbered():
    _patch, wrote = _place(_members((8, 1, 2)))
    assert "S08E01-E02" in " ".join(wrote), wrote


def test_a_single_episode_never_grows_a_span():
    """`episode_end` is set to the episode itself for ordinary files, so the
    explicit-span path must not turn every one of them into SxxE01-E01."""
    _patch, wrote = _place(_members((8, 1), (8, 2)), season=7)
    assert not any("-E" in p for p in wrote), wrote


def test_a_single_file_placement_was_always_correct():
    """Scope='episode' honoured the override already — establishing that is what
    localises the bug to the folder path rather than to overrides generally."""
    plan = plan_import(_dl(), "/dl/My.Show.S08E01.1080p.WEB.x264.mkv",
                       list_dir=lambda d: [], force=True,
                       override={"scope": "episode", "title": "My Show", "season": 7,
                                 "episode": 1, "media_id": "500", "target_dir": "/tv"})
    assert "Season 07" in plan["dest"]["dir"]
    assert "S07E01" in plan["dest"]["filename"]


# ── the dialog has to offer it ──────────────────────────────────────────────

def test_the_dialog_offers_a_season_for_a_whole_folder():
    """There was no field at all, so the number could not be assigned even
    though the backend is now willing to honour it."""
    body = _IMPORT_JS.split("function packHTML(", 1)[1][:2200]
    assert "Import as season" in body
    # The real input tag, not a substring of some longer attribute name —
    # a back-out that renamed it to data-vimp-pack-season-X satisfied the
    # looser check and this test passed with the field gone.
    assert 'type="number" min="0" data-vimp-pack-season>' in body
    # Offered only where a single number is meaningful.
    assert "p.seasons.length === 1" in body


def test_a_multi_season_folder_is_told_why_it_has_no_field():
    body = _IMPORT_JS.split("function packHTML(", 1)[1][:2200]
    assert "spans seasons" in body
    assert "cannot be given a single season number" in body


def test_the_chosen_season_reaches_the_request():
    place = _IMPORT_JS.split("var body = {", 1)[1][:1200]
    assert "r.kind === 'season' && r.season !== ''" in place
    assert "body.season = parseInt(r.season, 10);" in place


def test_the_field_is_prefilled_with_what_the_filenames_say():
    """Blank would make the common case — confirming season 8 — look like a
    choice the user had to make, and an empty box invites a typo."""
    body = _IMPORT_JS.split("if (r.kind === 'season') {", 1)[1][:700]
    assert "r.season = r.pack.seasons[0];" in body
    # Set as a property, never interpolated into a value="" attribute.
    assert "ps.value =" in body


def test_typing_does_not_re_render_the_field_out_from_under_the_caret():
    """renderModal() rewrites the whole pack panel, so calling it per keystroke
    would destroy the input being typed into."""
    handler = _IMPORT_JS.split("if (e.target.matches('[data-vimp-pack-season]')) {", 1)[1][:400]
    assert "updatePackArrows();" in handler
    # Comments stripped: the code says "NOT renderModal", which a raw substring
    # check reads as a call. A test that a comment can satisfy proves nothing.
    code = "\n".join(ln.split("//")[0] for ln in handler.split("\n"))
    assert "renderModal" not in code


def test_the_preview_shows_where_each_file_will_actually_land():
    """A wrong season here is unpicked one file at a time, so the result is
    shown before anything is copied rather than after."""
    fn = _IMPORT_JS.split("function updatePackArrows(", 1)[1][:1200]
    assert "data-vimp-pack-to" in fn
    assert "target !== p.seasons[0]" in fn, "unchanged season must show no arrow"
    # The rule itself, at the start of a line: `.vimp-pack-row .vimp-pack-moved`
    # also contains that substring, so a looser check passed with the arrow's
    # own colour deleted and the arrow rendering in the body text colour.
    assert "\n.vimp-pack-moved {" in _CSS


def test_a_half_typed_season_cannot_be_submitted():
    body = _IMPORT_JS.split("function updateConfirm(", 1)[1][:900]
    assert "packSeasonOk" in body
    assert "parseInt(r.season, 10) >= 0" in body


def test_the_stale_comment_that_documented_the_bug_is_gone():
    """It said the dialog's numbers are "deliberately not applied", which is
    what made the behaviour look intended rather than broken."""
    assert "deliberately not applied" not in _IMPORT_JS
    src = (_ROOT / "core" / "video" / "importer.py").read_text(encoding="utf-8")
    doc = src.split("def run_season_import(", 1)[1][:2600]
    assert "never from the override" not in doc
    assert "EPISODE numbers always come from each FILE" in doc
