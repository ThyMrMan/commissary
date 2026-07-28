"""Read-only dump of one show's episode rows, for diagnosing bad season numbering.

Nothing is written. Point it at your video_library.db and give it a title:

    python tools/diagnose_show.py Bleach
    python tools/diagnose_show.py --db /path/to/video_library.db Bleach
    python tools/diagnose_show.py --tmdb 30984

It answers the questions that decide what the fix is:
  * how many SHOW rows exist for that title / tmdb id (two Plex entries matched
    to the same TMDB show would merge their episodes under one numbering),
  * per season, how many episodes and what date range they span,
  * for a suspicious season, every episode with where it came from — a
    ``server_id`` means your media server reported it, blank means SoulSync
    wrote it from TMDB/TVDB metadata,
  * which rows have a file on disk.

Paste the output back. It contains titles, dates and internal ids only — no
paths, no tokens, no server addresses.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

# Where the file actually lives, in order. VIDEO_DATABASE_PATH is set for you in
# the container (/app/data/video_library.db); /app/database is the Python
# PACKAGE, not the data — the Dockerfile moved the file to /app/data precisely so
# a volume mount couldn't shadow the package.
DB_CANDIDATES = [
    os.environ.get("VIDEO_DATABASE_PATH"),
    "/app/data/video_library.db",          # container
    "data/video_library.db",
    "database/video_library.db",           # source checkout
]


def find_db():
    for p in DB_CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def connect(path):
    if not os.path.exists(path):
        sys.exit(
            "No database at %s\n\nTried:\n  %s\n\n"
            "SoulSync in Docker keeps this in the named volume 'soulsync_database',\n"
            "which has no host path you can browse — so run the script INSIDE the\n"
            "container:\n\n"
            "  docker cp tools/diagnose_show.py soulsync:/tmp/\n"
            "  docker exec soulsync python /tmp/diagnose_show.py --season 2 Bleach\n\n"
            "(swap 'soulsync' for your container name from `docker ps`.)"
            % (path, "\n  ".join(p for p in DB_CANDIDATES if p)))
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)   # read-only, enforced
    conn.row_factory = sqlite3.Row
    return conn


def find_shows(conn, title, tmdb_id):
    if tmdb_id:
        where, args = "s.tmdb_id = ?", [tmdb_id]
    else:
        where, args = "s.title LIKE ? COLLATE NOCASE", ["%" + title + "%"]
    return conn.execute(
        "SELECT s.id, s.title, s.year, s.tmdb_id, s.tvdb_id, s.server_source, s.server_id, "
        "s.root_folder_id, s.episodes_synced, "
        "(SELECT COUNT(*) FROM episodes e WHERE e.show_id = s.id) AS episode_rows "
        "FROM shows s WHERE " + where + " ORDER BY s.id", args).fetchall()


def season_summary(conn, show_id):
    return conn.execute(
        "SELECT season_number, COUNT(*) AS n, "
        "       SUM(CASE WHEN server_id IS NOT NULL THEN 1 ELSE 0 END) AS from_server, "
        "       SUM(CASE WHEN has_file = 1 THEN 1 ELSE 0 END) AS with_file, "
        "       MIN(air_date) AS first_air, MAX(air_date) AS last_air "
        "FROM episodes WHERE show_id = ? GROUP BY season_number ORDER BY season_number",
        (show_id,)).fetchall()


def episodes(conn, show_id, season):
    return conn.execute(
        "SELECT episode_number, title, air_date, server_source, server_id, has_file, "
        "       (SELECT COUNT(*) FROM media_files f WHERE f.episode_id = episodes.id) AS files "
        "FROM episodes WHERE show_id = ? AND season_number = ? ORDER BY episode_number",
        (show_id, season)).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("title", nargs="?", default="", help="show title (partial match)")
    ap.add_argument("--db", default=None, help="path to video_library.db (auto-detected if omitted)")
    ap.add_argument("--tmdb", type=int, help="match on tmdb id instead of title")
    ap.add_argument("--season", type=int, action="append", default=[],
                    help="dump every episode of this season (repeatable)")
    a = ap.parse_args()
    if not a.title and not a.tmdb:
        sys.exit("Give a title or --tmdb.")

    db_path = a.db or find_db() or DB_CANDIDATES[-1]
    conn = connect(db_path)
    print("database: %s\n" % db_path)
    shows = find_shows(conn, a.title, a.tmdb)
    if not shows:
        sys.exit("No show matched.")

    print("=== SHOW ROWS (%d) ===" % len(shows))
    if len(shows) > 1:
        print("!! more than one row — separate media-server entries. If they share a\n"
              "   tmdb_id, anything that groups by tmdb_id will treat them as one show.\n")
    for s in shows:
        print("  id=%-5s %-40s year=%-6s tmdb=%-8s tvdb=%-8s" %
              (s["id"], (s["title"] or "")[:40], s["year"], s["tmdb_id"], s["tvdb_id"]))
        print("        source=%-9s server_id=%-14s episodes_synced=%s  episode_rows=%d" %
              (s["server_source"], s["server_id"], s["episodes_synced"], s["episode_rows"]))

    for s in shows:
        print("\n=== SEASONS for show id=%s (%s) ===" % (s["id"], s["title"]))
        print("  %-7s %-6s %-12s %-10s %-12s %-12s" %
              ("season", "eps", "from server", "with file", "first air", "last air"))
        for r in season_summary(conn, s["id"]):
            flag = ""
            # The signature of two numbering schemes collapsed into one season:
            # a single season spanning many years.
            fa, la = str(r["first_air"] or "")[:4], str(r["last_air"] or "")[:4]
            if fa and la and fa.isdigit() and la.isdigit() and int(la) - int(fa) >= 3:
                flag = "   <-- spans %s years" % (int(la) - int(fa))
            print("  %-7s %-6s %-12s %-10s %-12s %-12s%s" %
                  (r["season_number"], r["n"], r["from_server"], r["with_file"],
                   r["first_air"] or "-", r["last_air"] or "-", flag))

        for sn in a.season:
            print("\n--- show id=%s SEASON %d, every row ---" % (s["id"], sn))
            print("  %-4s %-42s %-12s %-8s %-16s %-5s" %
                  ("ep", "title", "air date", "source", "server_id", "file"))
            for e in episodes(conn, s["id"], sn):
                print("  %-4s %-42s %-12s %-8s %-16s %-5s" %
                      (e["episode_number"], (e["title"] or "")[:42], e["air_date"] or "-",
                       e["server_source"] or "-", e["server_id"] or "(none)",
                       "yes" if e["has_file"] else "no"))
    conn.close()


if __name__ == "__main__":
    main()
