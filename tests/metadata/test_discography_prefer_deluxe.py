"""The artist page's merged card goes to the deluxe under "Prefer deluxe editions".

Same-year editions of one album collapse into a single card, and the plain title
has always won it — so the deluxe was a click further away than the edition a
deluxe-preferring user wants. With the option on, a deluxe-marked edition wins,
the biggest first; between variants that aren't deluxe the usual order stands.
"""

from __future__ import annotations

from core.metadata import discography as metadata_discography
from core.metadata.lookup import MetadataLookupOptions


def _card(id_, name, tracks, date="2005-12-06"):
    return {"id": id_, "name": name, "title": name, "album_type": "album",
            "release_date": date, "track_count": tracks}


def _ids(cards):
    return [card["id"] for card in cards]


def test_the_plain_title_wins_without_the_option():
    cards = [_card("std", "Curtain Call: The Hits", 17),
             _card("dlx", "Curtain Call: The Hits (Deluxe Edition)", 24)]
    assert _ids(metadata_discography._dedup_variant_releases(cards)) == ["std"]


def test_the_deluxe_wins_with_the_option():
    cards = [_card("std", "Curtain Call: The Hits", 17),
             _card("dlx", "Curtain Call: The Hits (Deluxe Edition)", 24)]
    assert _ids(metadata_discography._dedup_variant_releases(cards, prefer_deluxe=True)) == ["dlx"]


def test_the_biggest_deluxe_wins():
    cards = [_card("dlx", "Album (Deluxe Edition)", 20),
             _card("sup", "Album (Super Deluxe Edition)", 40),
             _card("std", "Album", 12)]
    assert _ids(metadata_discography._dedup_variant_releases(cards, prefer_deluxe=True)) == ["sup"]


def test_non_deluxe_variants_keep_the_usual_order_with_the_option():
    """A regional edition with two extra tracks is not a deluxe: the plain title stays."""
    cards = [_card("std", "Variant Album", 10),
             _card("se", "Variant Album (Swedish Edition)", 12),
             _card("rm", "Variant Album (2023 Remaster)", 10)]
    assert _ids(metadata_discography._dedup_variant_releases(cards, prefer_deluxe=True)) == ["std"]


def test_the_artist_page_reads_the_setting(monkeypatch):
    monkeypatch.setattr(metadata_discography, "prefer_deluxe_enabled", lambda: True)
    monkeypatch.setattr(
        "core.metadata.discography.get_artist_discography",
        lambda artist_id, artist_name='', options=None: {
            "albums": [
                {"id": "std", "name": "Recovery", "album_type": "album",
                 "release_date": "2010-06-18", "total_tracks": 17},
                {"id": "dlx", "name": "Recovery (Deluxe Edition)", "album_type": "album",
                 "release_date": "2010-06-18", "total_tracks": 19},
            ],
            "singles": [],
            "source": "deezer",
            "source_priority": ["deezer"],
        },
    )

    result = metadata_discography.get_artist_detail_discography("artist-1", "Eminem",
                                                                MetadataLookupOptions())

    assert _ids(result["albums"]) == ["dlx"]


def test_the_strict_artist_page_reads_the_setting_too(monkeypatch):
    """The strict three-state discography path merges its cards the same way."""
    from core.metadata import discography_strict

    monkeypatch.setattr(discography_strict, "prefer_deluxe_enabled", lambda: True)
    monkeypatch.setattr(
        discography_strict, "get_artist_discography",
        lambda artist_id, artist_name="", options=None: {
            "albums": [
                {"id": "std", "name": "Recovery", "album_type": "album",
                 "release_date": "2010-06-18", "total_tracks": 17},
                {"id": "dlx", "name": "Recovery (Deluxe Edition)", "album_type": "album",
                 "release_date": "2010-06-18", "total_tracks": 19},
            ],
            "singles": [],
            "source": "deezer",
            "source_priority": ["deezer"],
        },
    )

    result = discography_strict.get_artist_detail_discography("artist-1", "Eminem",
                                                              MetadataLookupOptions())

    assert _ids(result["albums"]) == ["dlx"]
