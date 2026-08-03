"""Rename a show's or movie's files from its own library page.

Asked for: rename media from the show's library page, showing the available
variables and previewing the resulting names before committing.

The engine already existed — core/video/mass_rename.py does preview + apply
with collision safety, sidecar moves and DB path updates — but only
library-wide, driven from the Tools page, with no way to see or type a
template. This adds a scope and a one-off template override, and the endpoints
the per-title panel drives.

The dangerous part is apply. `_apply_inner` recomputes the plan by calling
preview() rather than trusting paths from the browser, which is right — but it
called it with NO arguments. Forwarding the scope and template is therefore not
a nicety: without it, approving a preview of one show under a typed template
would rebuild the plan from the WHOLE library under the SAVED template and
rename files the user never saw. `keys` cannot save you, because it only
filters a plan whose proposed names already came from the wrong template.
"""

from __future__ import annotations

import inspect
import pathlib

import pytest

from core.video import mass_rename


_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ── apply must reuse what was previewed ──────────────────────────────────────
def test_apply_forwards_the_scope_and_template_to_the_plan():
    """The whole safety property, asserted on the call itself."""
    src = inspect.getsource(mass_rename._apply_inner)
    assert "preview(scope=scope, template=template)" in src, src


def test_apply_accepts_both_and_hands_them_over(monkeypatch):
    """Behavioural version of the above — a bare preview() would show up here
    as the recorded call missing the scope."""
    seen = {}

    def fake_preview(progress=None, *, scope=None, template=None):
        seen.update(scope=scope, template=template)
        return {"status": "completed", "entries": [], "unresolved": 0}

    monkeypatch.setattr(mass_rename, "preview", fake_preview)
    monkeypatch.setattr(mass_rename, "get_video_db", lambda: None, raising=False)
    res = mass_rename.apply(None, scope=("show", 7), template="$series - $episode")

    assert res["status"] == "completed"
    assert seen["scope"] == ("show", 7)
    assert seen["template"] == "$series - $episode"


def test_apply_without_a_scope_still_means_the_whole_library(monkeypatch):
    """The Tools-page path must keep working exactly as before."""
    seen = {}

    def fake_preview(progress=None, *, scope=None, template=None):
        seen.update(scope=scope, template=template)
        return {"status": "completed", "entries": [], "unresolved": 0}

    monkeypatch.setattr(mass_rename, "preview", fake_preview)
    mass_rename.apply()
    assert seen == {"scope": None, "template": None}


# ── the template override is one-off ─────────────────────────────────────────
def test_the_override_never_reaches_the_saved_settings():
    """A typed template renames files; it must not silently become the global
    naming scheme for every future import."""
    src = inspect.getsource(mass_rename.preview)
    assert "settings = dict(settings)" in src, "the override must not mutate the loaded settings"
    assert "organization.save" not in src, "preview must never persist anything"


def test_the_override_targets_the_right_template_key():
    src = inspect.getsource(mass_rename.preview)
    assert '"movie_template" if kind_scope == "movie" else "episode_template"' in src


def test_a_blank_template_falls_back_to_the_saved_one():
    """An empty box means 'use my saved template', not 'render everything to
    the same empty name'."""
    src = inspect.getsource(mass_rename.preview)
    assert 'if template and str(template).strip():' in src


# ── scoping ──────────────────────────────────────────────────────────────────
def test_scope_selects_only_that_kind():
    src = inspect.getsource(mass_rename.preview)
    body = src.split("total = len(movies)", 1)[0]
    assert 'db.repair_owned_movie_files(movie_id=scope_id)' in body
    assert 'db.rename_owned_episode_files(show_id=scope_id)' in body
    # a show scope must not drag every movie in the library into the plan
    assert 'movies, episodes = [], db.rename_owned_episode_files(show_id=scope_id)' in body


def test_the_queries_default_to_the_whole_library():
    """Every existing caller passes nothing and must keep getting everything."""
    from database.video_database import VideoDatabase
    for name in ("repair_owned_movie_files", "rename_owned_episode_files"):
        sig = inspect.signature(getattr(VideoDatabase, name))
        param = list(sig.parameters.values())[1]
        assert param.default is None, f"{name}'s filter must default to None"


def test_the_scoped_queries_filter_by_id():
    from database.video_database import VideoDatabase
    movie_src = inspect.getsource(VideoDatabase.repair_owned_movie_files)
    show_src = inspect.getsource(VideoDatabase.rename_owned_episode_files)
    assert 'where += " AND m.id=?"' in movie_src
    assert 'where += " AND s.id=?"' in show_src
    # parameterised, not interpolated — these take a user-supplied id
    assert "args.append(int(movie_id))" in movie_src
    assert "args.append(int(show_id))" in show_src


# ── the variable list ────────────────────────────────────────────────────────
def test_the_tokens_come_from_the_template_engine_itself():
    """Hand-listing them would drift from what actually substitutes."""
    src = inspect.getsource(mass_rename.tokens_for)
    assert "organization._movie_values" in src
    assert "organization._episode_values" in src


@pytest.mark.parametrize("kind", ["show", "movie"])
def test_every_offered_variable_has_a_description(kind):
    """A token added to organization.py but not described here would render as
    a bare name with no explanation — this fails instead."""
    undescribed = [t["token"] for t in mass_rename.tokens_for(kind) if not t["description"]]
    assert not undescribed, f"undescribed {kind} tokens: {undescribed}"


def test_the_show_vocabulary_is_what_the_episode_template_uses():
    tokens = {t["token"] for t in mass_rename.tokens_for("show")}
    assert {"$series", "$season", "$episode", "$episodetitle", "$quality"} <= tokens
    assert "$title" not in tokens, "movie-only token offered on a show"


def test_the_movie_vocabulary_is_distinct():
    tokens = {t["token"] for t in mass_rename.tokens_for("movie")}
    assert {"$title", "$year", "$edition", "$tmdbid"} <= tokens
    assert "$episode" not in tokens, "episode-only token offered on a movie"


def test_tokens_show_this_title_s_own_values():
    """The point of the list is seeing what YOUR file resolves to, not a
    generic legend."""
    row = {"show_title": "Breaking Bad", "season_number": 2, "episode_number": 5,
           "episode_title": "Breakage", "quality": "1080p"}
    got = {t["token"]: t["example"] for t in mass_rename.tokens_for("show", row)}
    assert got["$series"] == "Breaking Bad"
    assert got["$season"] == "02"
    assert got["$episode"] == "05"
    assert got["$episodetitle"] == "Breakage"


def test_tokens_survive_having_no_files_yet():
    """The panel asks before it knows there are files; this must not raise."""
    for kind in ("show", "movie"):
        assert mass_rename.tokens_for(kind, None)


# ── the endpoints ────────────────────────────────────────────────────────────
def _downloads_src():
    return (_ROOT / "api" / "video" / "downloads.py").read_text(encoding="utf-8")


def test_the_endpoints_live_under_the_admin_gated_prefix():
    """Renaming library files is management. /organization/* is covered by the
    blueprint's admin gate — putting them anywhere else would expose them."""
    src = _downloads_src()
    for route in ("/organization/rename/tokens",
                  "/organization/rename/preview/title",
                  "/organization/rename/apply/title"):
        assert f'"{route}"' in src, f"{route} missing"


def test_the_apply_endpoint_resends_scope_and_template():
    src = _downloads_src()
    body = src.split("def video_rename_apply_title", 1)[1].split("@bp.route", 1)[0]
    assert "scope=scope" in body and "template=body.get(\"template\")" in body


def test_a_request_naming_no_title_is_refused():
    """Without this, a malformed body would fall through to scope=None and
    rename the entire library."""
    src = _downloads_src()
    helper = src.split("def _rename_scope", 1)[1].split("@bp.route", 1)[0]
    assert 'if kind not in ("show", "movie")' in helper
    assert "return None" in helper
    for fn in ("video_rename_preview_title", "video_rename_apply_title"):
        body = src.split("def %s" % fn, 1)[1].split("@bp.route", 1)[0]
        assert "if scope is None:" in body
        assert "400" in body


def test_the_per_title_preview_does_not_disturb_the_library_wide_job():
    """The Tools page runs a background scan with shared state; a panel opening
    on a show must not clobber someone's in-flight library scan."""
    src = _downloads_src()
    body = src.split("def video_rename_preview_title", 1)[1].split("@bp.route", 1)[0]
    assert "start_preview" not in body
    assert "from core.video.mass_rename import preview" in body


# ── the panel ────────────────────────────────────────────────────────────────
def _panel_src():
    return (_ROOT / "webui" / "static" / "video" / "video-rename-panel.js").read_text(encoding="utf-8")


def test_the_panel_is_loaded_by_the_page():
    html = (_ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    assert "video/video-rename-panel.js" in html


def test_the_detail_page_offers_and_dispatches_the_button():
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
    assert 'data-vd-act="rename"' in js
    assert "which === 'rename'" in js, "button rendered but never wired to a handler"
    assert "function openRenamePanel" in js
    assert "VideoRename.open" in js


def test_the_button_is_admin_only_and_library_only():
    """The endpoints are admin-gated, so offering it to anyone else produces a
    panel whose every action 403s — the same reasoning as Manage."""
    js = (_ROOT / "webui" / "static" / "video" / "video-detail.js").read_text(encoding="utf-8")
    block = js.split("var renameId =", 1)[1].split(";", 1)[0]
    assert "_isAdmin" in block
    assert "ownLibItem" in block


def test_the_panel_sends_the_template_on_both_calls():
    """Previewing with a typed template and applying without it is the exact
    mismatch this whole feature has to avoid."""
    src = _panel_src()
    for endpoint in ("/preview/title", "/apply/title"):
        call = src.split(endpoint, 1)[1][:220]
        assert "template: state.template" in call, f"{endpoint} does not send the template"
        assert "kind: state.kind" in call and "id: state.id" in call


def test_the_panel_confirms_before_touching_disk():
    src = _panel_src()
    apply_fn = src.split("function applyRenames", 1)[1].split("\n    }", 1)[0]
    assert "confirmDlg" in apply_fn
    assert "destructive: true" in apply_fn


def test_the_panel_checks_admin_before_opening():
    src = _panel_src()
    open_fn = src.split("function open(opts)", 1)[1].split("\n    }", 1)[0]
    assert "is_admin" in open_fn


def test_stale_previews_cannot_overwrite_a_newer_one():
    """Typing is debounced but responses still race; the last keystroke has to
    win or the list can end up showing names for a template you have edited."""
    src = _panel_src()
    assert "++state.seq" in src
    assert "if (seq !== state.seq) return;" in src


def test_the_panel_says_the_saved_template_is_untouched():
    """The user chose a one-off; the UI has to make that explicit or it reads
    like it is editing the global setting."""
    src = _panel_src()
    assert "saved template is not changed" in src
