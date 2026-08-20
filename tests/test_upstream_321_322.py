"""Three fixes carried over from upstream SoulSync 3.2.1 / 3.2.2.

Each was verified present as a real defect in this fork before being ported —
the fork has diverged far enough that most of upstream's release notes either do
not apply or were already solved here differently.

  · #1158 — a library scan gradually corrupted stored file paths.
  · #1159 — punctuation glued to a search term buried the track you asked for.
  · "Replace the original" did nothing when you imported the file by hand.

Deliberately NOT ported alongside these: the Soulseek-side fixes (this install
logs 9 Soulseek lines against 2059 YouTube), and upstream's fix for a stale
``settings.py`` inside the user's bind mount, which ``entrypoint.sh`` already
solves here by copying it from ``/defaults`` on every start.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from database.music_database import MusicDatabase


# ── #1158: a scan must never overwrite a good path with a fabricated one ─────

def _db(tmp_path):
    db = MusicDatabase(database_path=str(tmp_path / "t.db"))
    with db._get_connection() as c:
        c.execute("INSERT OR IGNORE INTO artists (id, name) VALUES ('art1', 'Kanaria')")
        c.execute("INSERT OR IGNORE INTO albums (id, artist_id, title) "
                  "VALUES ('alb1', 'art1', 'Dec.')")
        c.commit()
    return db


def _subsonic_track(rating_key, title, **kw):
    """A Navidrome/Subsonic-shaped track object. ``path`` is the field the API
    omits transiently — during a library rescan, or a network hiccup."""
    return SimpleNamespace(ratingKey=rating_key, title=title, trackNumber=1,
                           duration=134340, suffix="flac", **kw)


def _path_of(db, track_id):
    with db._get_connection() as c:
        row = c.execute("SELECT file_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
    return row["file_path"] if row else None


class TestScanNeverCorruptsAPath:
    def test_a_missing_path_is_not_replaced_by_a_bare_filename(self, tmp_path):
        """The first half. A fabricated "Dec..flac" has no directory structure,
        matches nothing on disk, and is what later overwrites the real value."""
        db = _db(tmp_path)
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.'),
                                        'alb1', 'art1', 'navidrome')
        assert _path_of(db, 't1') in (None, "")

    def test_a_later_scan_with_no_path_keeps_the_stored_one(self, tmp_path):
        """The second half, and the one that made the damage progressive: the
        UPDATE overwrote file_path unconditionally while every other nullable
        column beside it was already COALESCE-protected."""
        db = _db(tmp_path)
        good = "/music/Kanaria/Kanaria - Dec/01 - Dec.opus"
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.', path=good),
                                        'alb1', 'art1', 'navidrome')
        assert _path_of(db, 't1') == good
        # the API drops 'path' on the next pass
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.'),
                                        'alb1', 'art1', 'navidrome')
        assert _path_of(db, 't1') == good

    def test_a_scan_that_does_report_a_path_still_updates_it(self, tmp_path):
        """COALESCE protects a NULL, it must not freeze the column — a genuinely
        moved file has to be followed."""
        db = _db(tmp_path)
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.', path="/old/a.flac"),
                                        'alb1', 'art1', 'navidrome')
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.', path="/new/b.flac"),
                                        'alb1', 'art1', 'navidrome')
        assert _path_of(db, 't1') == "/new/b.flac"

    def test_repeated_scans_do_not_accumulate_damage(self, tmp_path):
        """The reported symptom was erosion across scans, not one bad write."""
        db = _db(tmp_path)
        good = "/music/Kanaria/Kanaria - Dec/01 - Dec.opus"
        db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.', path=good),
                                        'alb1', 'art1', 'navidrome')
        for _ in range(5):
            db.insert_or_update_media_track(_subsonic_track('t1', 'Dec.'),
                                            'alb1', 'art1', 'navidrome')
        assert _path_of(db, 't1') == good


# ── #1159: punctuation must not be glued to a fuzzy search term ──────────────

class TestFuzzyTerms:
    def test_trailing_punctuation_comes_off(self):
        """THE regression. Every term but the last carried its comma, so only
        the last one could match a library file tagged without them."""
        assert MusicDatabase._fuzzy_terms("would've, could've, should've") == \
            ["would've", "could've", "should've"]

    @pytest.mark.parametrize("text,expected", [
        ("n.w.a straight outta compton", ["n.w.a", "straight", "outta", "compton"]),
        ("p!nk raise your glass", ["p!nk", "raise", "your", "glass"]),
        ("ac/dc back in black", ["ac/dc", "back", "black"]),
        ("sgt. pepper's lonely hearts", ["sgt", "pepper's", "lonely", "hearts"]),
    ])
    def test_internal_punctuation_survives(self, text, expected):
        """Ends only. These are real names and mangling them would trade one
        bad search for another."""
        assert MusicDatabase._fuzzy_terms(text) == expected

    def test_short_words_are_still_dropped(self):
        """Unchanged behaviour — a two-letter LIKE term matches everything."""
        assert MusicDatabase._fuzzy_terms("a in the of song") == ["the", "song"]

    def test_a_term_that_is_only_punctuation_disappears(self):
        assert MusicDatabase._fuzzy_terms("-- song --") == ["song"]

    @pytest.mark.parametrize("text", ["", None, "   "])
    def test_nothing_in_nothing_out(self, text):
        assert MusicDatabase._fuzzy_terms(text) == []

    def test_it_is_strictly_broader_than_the_old_split(self):
        """The safety property: the column keeps its own punctuation, so a
        trimmed term matches everything the untrimmed one did and more. It can
        raise a row's score, never lower it."""
        for text in ["would've, could've, should've", "crosby, stills & nash",
                     "n.w.a", "hello world", "sgt. pepper's"]:
            old = [w.strip() for w in text.split() if len(w.strip()) >= 3]
            new = MusicDatabase._fuzzy_terms(text)
            for o in old:
                assert any(o.startswith(n) or n in o for n in new), \
                    "term %r lost its match (old=%r new=%r)" % (o, old, new)


class TestFuzzySearchEndToEnd:
    def _seed(self, tmp_path):
        db = MusicDatabase(database_path=str(tmp_path / "t.db"))
        with db._get_connection() as c:
            c.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Taylor Swift')")
            c.execute("INSERT INTO artists (id, name) VALUES ('a2', 'Someone Else')")
            c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('b1','a1','Midnights')")
            c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('b2','a2','Other')")
            # tagged WITHOUT the commas, which taggers and filesystems routinely do
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number) "
                      "VALUES ('t1','b1','a1','Would''ve Could''ve Should''ve', 1)")
            # noise sharing exactly one word — what used to outrank the real track
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number) "
                      "VALUES ('t2','b2','a2','Should''ve Been Me', 1)")
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number) "
                      "VALUES ('t3','b2','a2','Should''ve Known', 2)")
            c.commit()
        return db

    def test_the_real_track_is_found_despite_the_commas(self, tmp_path):
        db = self._seed(tmp_path)
        with db._get_connection() as c:
            rows = db._search_tracks_fuzzy_rows(c.cursor(),
                                                "Would've, Could've, Should've", "", 10)
        ids = [r["id"] for r in rows]
        assert "t1" in ids, ids
        assert ids[0] == "t1", "the real track should outrank the one-word noise: %s" % ids

    def test_a_comma_in_an_artist_name_matches_too(self, tmp_path):
        """The artist half had the identical bug, and upstream's own note says
        their first pass missed it because the tests only covered titles.
        'Crosby, Stills & Nash' is not an exotic case.

        Needs a decoy sharing the LAST word: with the terms untrimmed, only
        '%nash%' matches broadly, so the real artist and the decoy both score 1.
        The sort is stable and the SQL orders by title, so the decoy's title
        must sort BEFORE the real one ('Chicago' < 'Helpless') or the tie lands
        on the right answer by luck and the test proves nothing."""
        db = MusicDatabase(database_path=str(tmp_path / "t.db"))
        with db._get_connection() as c:
            c.execute("INSERT INTO artists (id, name) VALUES ('a1', 'Crosby Stills Nash')")
            c.execute("INSERT INTO artists (id, name) VALUES ('a2', 'Graham Nash')")
            c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('b1','a1','Deja Vu')")
            c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('b2','a2','Songs')")
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number) "
                      "VALUES ('t1','b1','a1','Helpless', 1)")
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number) "
                      "VALUES ('t2','b2','a2','Chicago', 1)")
            c.commit()
            rows = db._search_tracks_fuzzy_rows(c.cursor(), "", "Crosby, Stills, Nash", 10)
        ids = [r["id"] for r in rows]
        assert ids and ids[0] == "t1", \
            "the three-word artist should outrank the one-word decoy: %s" % ids


# ── "Replace the original" on a manual import ────────────────────────────────

class TestManualRematchReplace:
    def _setup(self, tmp_path, monkeypatch, *, replace_track_id=1, staged="/stage/new.flac"):
        import database.music_database as mdb
        from core.imports.rematch_hints import RematchHint, create_hint

        db = MusicDatabase(database_path=str(tmp_path / "t.db"))
        old_file = tmp_path / "old.flac"
        old_file.write_bytes(b"\x00\x00")
        with db._get_connection() as c:
            c.execute("INSERT INTO artists (id, name) VALUES ('a1','A')")
            c.execute("INSERT INTO albums (id, artist_id, title) VALUES ('b1','a1','B')")
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) "
                      "VALUES (?, 'b1','a1','Old', 1, ?)",
                      (str(replace_track_id), str(old_file)))
            create_hint(c.cursor(), RematchHint(staged_path=staged, source="manual",
                                                replace_track_id=replace_track_id))
            c.commit()
        monkeypatch.setattr(mdb, "get_database", lambda *a, **k: db)
        return db, old_file

    def _runtime(self):
        from core.imports.routes import ImportRouteRuntime
        return ImportRouteRuntime()

    def _rows(self, db):
        with db._get_connection() as c:
            return [r["id"] for r in c.execute("SELECT id FROM tracks").fetchall()]

    def _pending(self, db):
        with db._get_connection() as c:
            return c.execute("SELECT COUNT(*) FROM rematch_hints "
                             "WHERE status='pending'").fetchone()[0]

    def test_the_old_row_and_file_go(self, tmp_path, monkeypatch):
        """THE fix: importing the staged file by hand now honours the checkbox."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch)
        finalize_manual_rematch_replace(
            self._runtime(), "/stage/new.flac",
            {"_final_processed_path": str(tmp_path / "landed.flac")})
        assert self._rows(db) == []
        assert not old_file.exists()
        assert self._pending(db) == 0

    def test_no_hint_is_a_silent_no_op(self, tmp_path, monkeypatch):
        """The overwhelmingly common case — an ordinary staging file."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch, staged="/stage/other.flac")
        finalize_manual_rematch_replace(self._runtime(), "/stage/unrelated.flac",
                                        {"_final_processed_path": "/x/y.flac"})
        assert self._rows(db) == ["1"]
        assert old_file.exists()

    def test_re_identifying_onto_the_same_home_deletes_nothing(self, tmp_path, monkeypatch):
        """The same-home guard. Re-identify to the release the file is already
        in and the import reuses that very file — deleting it would be the
        data loss this whole path exists to avoid."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch)
        finalize_manual_rematch_replace(self._runtime(), "/stage/new.flac",
                                        {"_final_processed_path": str(old_file)})
        assert self._rows(db) == ["1"]
        assert old_file.exists()

    def test_an_unknown_landing_path_keeps_the_original(self, tmp_path, monkeypatch):
        """Without knowing where the import landed the same-home guard is blind,
        so it refuses. A duplicate is recoverable; the only copy is not."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch)
        finalize_manual_rematch_replace(self._runtime(), "/stage/new.flac", {})
        assert self._rows(db) == ["1"]
        assert old_file.exists()
        # ...but the hint is still spent, so it cannot fire later against a
        # stale path
        assert self._pending(db) == 0

    def test_processed_path_outranks_the_older_key(self, tmp_path, monkeypatch):
        """``_final_processed_path`` is canonical: post-processing can move a
        file after ``_final_path`` was recorded, and reading only the older key
        leaves the same-home guard looking at the wrong place."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch)
        finalize_manual_rematch_replace(
            self._runtime(), "/stage/new.flac",
            {"_final_processed_path": str(old_file), "_final_path": "/somewhere/else.flac"})
        assert self._rows(db) == ["1"], "the canonical key should have won"
        assert old_file.exists()

    def test_a_hint_without_replace_intent_only_gets_consumed(self, tmp_path, monkeypatch):
        """Re-identify WITHOUT the checkbox: nothing may be deleted."""
        from core.imports.routes import finalize_manual_rematch_replace
        db, old_file = self._setup(tmp_path, monkeypatch, replace_track_id=None)
        finalize_manual_rematch_replace(self._runtime(), "/stage/new.flac",
                                        {"_final_processed_path": "/x/y.flac"})
        assert old_file.exists()
        assert self._pending(db) == 0

    def test_a_broken_database_never_fails_the_import(self, tmp_path, monkeypatch):
        """The import has already succeeded by this point; a cleanup problem
        must be logged and swallowed, never raised."""
        import database.music_database as mdb
        from core.imports.routes import finalize_manual_rematch_replace
        monkeypatch.setattr(mdb, "get_database",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        finalize_manual_rematch_replace(self._runtime(), "/stage/new.flac", {})


def test_the_manual_import_path_actually_calls_it():
    """Source guard: the function is inert unless the single-file import path
    invokes it, and it must sit AFTER the rejection check — a quarantined
    import must never delete the original it failed to replace."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "imports" / "routes.py"
           ).read_text(encoding="utf-8")
    assert "finalize_manual_rematch_replace(runtime, file_path, context)" in src
    call = src.index("finalize_manual_rematch_replace(runtime, file_path, context)")
    reject = src.index("if reject_reason:")
    assert reject < call, "the reject check must come first"
