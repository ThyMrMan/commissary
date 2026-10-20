"""The fork's identity: display name, repository, and where users get help.

Only USER-FACING surfaces use these (page title, sidebar name, sign-in screen,
PWA manifest, bug-report links, update check, the published image). Upstream
prose that says "SoulSync" is deliberately left alone: mass-renaming it makes
every upstream merge conflict.

Some ``soulsync`` strings are DATA, not branding, and must never change: the
Subsonic client name (a rename registers a second Navidrome player), the
``soulsync_database`` docker volume, ``SOULSYNC_*`` env vars, and the
``soulsync`` standalone server value / ``::soulsync`` id suffixes stored in
library rows. ``tests/test_commissary_branding.py`` pins them.
"""

APP_NAME = "Commissary"

GITHUB_REPO = "ThyMrMan/commissary"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
ISSUES_URL = f"{REPO_URL}/issues"
IMAGE = "ghcr.io/thymrman/commissary"

UPSTREAM_REPO = "Nezreka/SoulSync"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
