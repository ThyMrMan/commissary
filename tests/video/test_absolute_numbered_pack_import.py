"""Importing a whole folder of absolute-numbered (fansub-style) episodes.

Reported: the Import page offered "Import folder" on /downloads/complete/Anime but
not inside '[GHOST] Nana (BD 1080p HEVC 10bit AC3)', so there was no way to import
a whole show. Its files are named '[GHOST] Nana - 01 [BD 1080p …]': a group tag
and the show's ABSOLUTE episode number, no season anywhere. The pack scan only
counted names carrying both a season and an episode, so the folder read as one
holding no episodes at all — and the parent only got the offer from the loose
SxxExx files sitting beside it.

Such a file is now a pack member with no season yet. Its season and episode come
from the show chosen in the Place dialog — the Nth aired episode (specials
excluded) IS absolute N — so a box set spanning two seasons files into both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from flask import Flask, g

from core.video.importer import pack_members, resolve_absolute_members, run_season_import
from database.video_database import VideoDatabase


def _ghost(n):
    return "/dl/Nana/[GHOST] Nana - %02d [BD 1080p HEVC 10bit AC3] [A1B2C3D4].mkv" % n


# ── the scan ────────────────────────────────────────────────────────────────
def test_absolute_numbered_files_are_pack_members():
    files = [_ghost(1), _ghost(2), "/dl/Nana/[GHOST] Nana - NCOP [BD].mkv", "/dl/Nana/info.nfo"]
    m = pack_members("/dl/Nana", lambda root: files, size_of=lambda p: 1_000_000_000)
    assert [(x["season"], x["episode"], x["absolute"]) for x in m] == [(None, 1, 1), (None, 2, 2)]


def test_sxxexx_members_are_unchanged_and_sort_first():
    files = ["/dl/p/Show.S01E02.1080p.mkv", _ghost(5), "/dl/p/Show.S01E01.1080p.mkv"]
    m = pack_members("/dl/p", lambda root: files, size_of=lambda p: 1_000_000_000)
    assert [(x["season"], x["episode"]) for x in m] == [(1, 1), (1, 2), (None, 5)]
    assert "absolute" not in m[0]


def test_a_name_without_a_group_tag_is_still_not_a_member():
    """'Movie - 2' style names must not turn into episodes — the group tag is
    what marks the fansub convention, same rule as fansub_absolute_episode."""
    m = pack_members("/dl/x", lambda root: ["/dl/x/Some Film - 2 1080p.mkv"],
                     size_of=lambda p: 1_000_000_000)
    assert m == []


# ── resolving the season ────────────────────────────────────────────────────
def _absolute(*ns):
    return [{"path": _ghost(n), "season": None, "episode": n, "episode_end": n,
             "absolute": n} for n in ns]


_TWO_SEASONS = {"episodes": {**{(1, e): {} for e in range(1, 14)},     # 13 in season 1
                             **{(2, e): {} for e in range(1, 13)},     # 12 in season 2
                             (0, 1): {}}}                              # a special


def test_the_shows_episode_list_places_each_absolute_number():
    out = resolve_absolute_members(_absolute(1, 13, 14, 25), identity=_TWO_SEASONS)
    assert [(m["season"], m["episode"]) for m in out] == [(1, 1), (1, 13), (2, 1), (2, 12)]


def test_specials_do_not_count_toward_the_absolute_number():
    out = resolve_absolute_members(_absolute(1), identity={"episodes": {(0, 1): {}, (1, 1): {}}})
    assert (out[0]["season"], out[0]["episode"]) == (1, 1)


def test_without_an_episode_list_the_typed_season_then_season_one():
    assert [(m["season"], m["episode"]) for m in
            resolve_absolute_members(_absolute(3), season=2)] == [(2, 3)]
    assert [(m["season"], m["episode"]) for m in
            resolve_absolute_members(_absolute(3))] == [(1, 3)]


def test_a_number_past_the_known_list_falls_through_rather_than_vanishing():
    out = resolve_absolute_members(_absolute(30), identity=_TWO_SEASONS)
    assert (out[0]["season"], out[0]["episode"]) == (1, 30)


# ── the whole placement ─────────────────────────────────────────────────────
class _FS:
    def __init__(self):
        self.wrote = []

    def list_dir(self, p): return []
    def makedirs(self, p): pass
    def copy(self, s, d): self.wrote.append(d)
    def move(self, s, d): self.wrote.append(d)
    def remove(self, p): pass
    def write_text(self, p, c): pass
    def save_url(self, u, d): pass


def _place(files, *, identity=None, season=None):
    fs = _FS()
    override = {"scope": "season", "title": "Nana", "media_id": "500", "target_dir": "/anime"}
    if season is not None:
        override["season"] = season
    dl = {"kind": "show", "media_id": "500", "media_source": "tmdb", "target_dir": "/anime",
          "release_title": "[GHOST] Nana (BD 1080p HEVC 10bit AC3)", "size_bytes": 1,
          "search_ctx": json.dumps({"scope": "season"})}
    patch = run_season_import(dl, "/dl/Nana", fs=fs, lister=lambda root: list(files),
                              settings={}, force=True, override=override, identity=identity,
                              size_of=lambda p: 1_000_000_000)
    return patch, fs.wrote


def test_a_folder_of_absolute_numbered_episodes_imports_every_one():
    patch, wrote = _place([_ghost(n) for n in (1, 2, 3)])
    assert patch["status"] == "completed", patch
    assert patch["_pack_imported"] == 3
    assert all("Season 01" in p for p in wrote), wrote
    joined = " ".join(wrote)
    assert all("S01E%02d" % n in joined for n in (1, 2, 3)), wrote


def test_a_box_set_spanning_two_seasons_files_into_both():
    patch, wrote = _place([_ghost(n) for n in (13, 14)], identity=_TWO_SEASONS)
    assert patch["status"] == "completed", patch
    joined = " ".join(wrote)
    assert "S01E13" in joined and "S02E01" in joined, wrote


def test_a_typed_season_seeds_the_resolution_instead_of_renumbering_it():
    patch, wrote = _place([_ghost(n) for n in (1, 2)], season=3)
    assert patch["status"] == "completed", patch
    assert all("S03E0" in p for p in wrote), wrote


# ── the Import page ─────────────────────────────────────────────────────────
@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    import core.video.sources as sources
    monkeypatch.setattr(sources, "resolve_video_server", lambda *a, **k: "plex")
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")

    @app.before_request
    def _p():
        g.profile_id = 1; g.is_admin = True; g.can_download = True; g.allowed_sides = "both"

    try:
        yield app.test_client()
    finally:
        videoapi._video_db = None


@pytest.fixture()
def nana(tmp_path):
    d = tmp_path / "[GHOST] Nana (BD 1080p HEVC 10bit AC3)"
    d.mkdir()
    for n in (1, 2, 3):
        (d / Path(_ghost(n)).name).write_bytes(b"x" * 64)
    return d


def test_the_browser_offers_the_folder(client, nana):
    d = client.get("/api/video/import/browse", query_string={"path": str(nana)}).get_json()
    assert d["success"] is True
    assert d["pack"] == {"count": 3, "seasons": []}


def test_the_folder_can_be_added_and_previewed(client, nana):
    added = client.post("/api/video/import/add", json={"path": str(nana)}).get_json()
    assert added["success"] is True and added["episodes"] == 3
    prev = client.get("/api/video/import/pack-preview", query_string={"path": str(nana)}).get_json()
    assert prev["count"] == 3 and prev["absolute"] == 3 and prev["seasons"] == []
    assert [i["episode"] for i in prev["items"]] == [1, 2, 3]


def test_the_shows_list_wins_over_a_typed_season():
    """Once the episode list has placed each file, a typed season must not be
    applied on top — that would refile season 2's episode under the typed one."""
    patch, wrote = _place([_ghost(n) for n in (13, 14)], identity=_TWO_SEASONS, season=3)
    assert patch["status"] == "completed", patch
    joined = " ".join(wrote)
    assert "S01E13" in joined and "S02E01" in joined and "S03E" not in joined, wrote


def test_a_folder_mixing_both_namings_can_be_added_and_previewed(client, nana):
    """A season None beside a numbered season must not reach a sort together."""
    (nana / "Nana.S00E01.Recap.1080p.mkv").write_bytes(b"x" * 64)
    added = client.post("/api/video/import/add", json={"path": str(nana)}).get_json()
    assert added["success"] is True and added["episodes"] == 4
    prev = client.get("/api/video/import/pack-preview", query_string={"path": str(nana)}).get_json()
    assert prev["seasons"] == [0] and prev["absolute"] == 3
