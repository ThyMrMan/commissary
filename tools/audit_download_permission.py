"""Who can currently start or cancel downloads? Read-only.

The default for a NEW profile changed: it now matches the role (on for an
admin, off for everyone else). EXISTING profiles are deliberately untouched —
silently revoking a permission someone relies on is its own kind of bug — so
this lists what they actually have, for review.

    python tools/audit_download_permission.py
    docker exec soulsync python /tmp/audit_download_permission.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

CANDIDATES = [os.environ.get("DATABASE_PATH"), "/app/data/music_library.db",
              "data/music_library.db", "database/music_library.db"]


def main():
    path = next((p for p in CANDIDATES if p and os.path.exists(p)), None)
    if not path:
        sys.exit("No music database found. Tried:\n  " +
                 "\n  ".join(p for p in CANDIDATES if p))
    conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, is_admin, can_download, allowed_sides, plex_username "
        "FROM profiles ORDER BY is_admin DESC, id").fetchall()
    conn.close()

    print("database: %s\n" % path)
    print("  %-4s %-22s %-7s %-13s %-8s %s" %
          ("id", "name", "admin", "can_download", "sides", "plex"))
    review = []
    for r in rows:
        admin = bool(r["is_admin"])
        dl = bool(r["can_download"])
        flag = ""
        if dl and not admin:
            flag = "   <-- can download AND cancel"
            review.append(r)
        print("  %-4s %-22s %-7s %-13s %-8s %s%s" % (
            r["id"], (r["name"] or "")[:22], "yes" if admin else "-",
            "ON" if dl else "off", r["allowed_sides"] or "(default)",
            r["plex_username"] or "-", flag))

    print()
    if review:
        print("%d non-admin profile(s) can start downloads and cancel the admin's." % len(review))
        print("That is unchanged from before — review them in Manage Profiles and")
        print("turn 'Can download' off for any that should only be able to ask.")
    else:
        print("No non-admin profile can start or cancel downloads.")


if __name__ == "__main__":
    main()
