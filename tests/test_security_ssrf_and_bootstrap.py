"""Two pre-Internet-exposure holes, closed.

1. /api/v1/api-keys/bootstrap minted an API key with NO authentication. The
   login gate deliberately exempts /api/v1/ (it authenticates with its own keys),
   so an anonymous caller could obtain a credential and then PATCH
   /api/v1/settings to set security.require_login back to false — a complete
   bypass of login mode on any reachable instance.

2. ImageCache._is_fetch_allowed ended in `bool(parsed.hostname) or
   is_internal_image_host(url)`. The no-hostname case already returned False
   above it, so the left side was ALWAYS true: nothing was ever refused, and the
   image proxy would fetch any address a caller named — loopback, link-local
   (cloud metadata), RFC1918. The dead right-hand side could not have helped
   either: is_internal_image_host DETECTS internal hosts, so as an allow
   condition it approves precisely the addresses that matter.
"""

from __future__ import annotations

import pytest

from core.image_cache import ImageCache


# ── 1. bootstrap ─────────────────────────────────────────────────────────────
@pytest.fixture()
def client():
    import web_server
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as c:
        yield c


@pytest.fixture()
def clean_keys():
    """Run with zero API keys — the only state in which bootstrap does anything."""
    import web_server
    cm = web_server.app.soulsync["config_manager"]
    keys, login = cm.get("api_keys", []), cm.get("security.require_login", False)
    cm.set("api_keys", [])
    try:
        yield cm
    finally:
        cm.set("api_keys", keys)
        cm.set("security.require_login", login)


def _admin(monkeypatch, is_admin=True):
    import database.music_database as m
    monkeypatch.setattr(m, "get_database", lambda: type("_D", (), {
        "get_profile": staticmethod(lambda pid: {"id": pid, "name": "P", "is_admin": is_admin})})())


def test_anonymous_cannot_bootstrap_with_login_off(client, clean_keys):
    """With login OFF every request already resolves to profile 1, so an admin
    check alone would authorise anonymous callers — the endpoint has to refuse
    outright. Same call the plaintext config export makes."""
    clean_keys.set("security.require_login", False)
    r = client.post("/api/v1/api-keys/bootstrap", json={"label": "x"})
    assert r.status_code == 403
    assert clean_keys.get("api_keys", []) == []      # nothing minted


def test_anonymous_cannot_bootstrap_with_login_on(client, clean_keys):
    """The original bypass: /api/v1/ is exempt from the login gate, so this
    returned 201 even in login mode."""
    clean_keys.set("security.require_login", True)
    r = client.post("/api/v1/api-keys/bootstrap", json={"label": "x"})
    assert r.status_code == 401
    assert clean_keys.get("api_keys", []) == []


def test_an_authenticated_standard_user_cannot_bootstrap(client, clean_keys, monkeypatch):
    clean_keys.set("security.require_login", True)
    _admin(monkeypatch, is_admin=False)
    with client.session_transaction() as s:
        s["login_authenticated"] = True
        s["profile_id"] = 7
    assert client.post("/api/v1/api-keys/bootstrap", json={}).status_code == 403
    assert clean_keys.get("api_keys", []) == []


def test_an_authenticated_admin_still_can(client, clean_keys, monkeypatch):
    """The legitimate path has to keep working, or this is just a broken route."""
    clean_keys.set("security.require_login", True)
    _admin(monkeypatch, is_admin=True)
    with client.session_transaction() as s:
        s["login_authenticated"] = True
        s["profile_id"] = 1
    r = client.post("/api/v1/api-keys/bootstrap", json={"label": "legit"})
    assert r.status_code == 201
    assert r.get_json()["data"]["key"].startswith("sk_")
    # still one-shot: a second call is refused now that a key exists
    assert client.post("/api/v1/api-keys/bootstrap", json={}).status_code == 403


def test_admin_is_resolved_from_the_db_not_flask_g(client, clean_keys, monkeypatch):
    """web_server's profile hook skips /api/v1/ entirely, so g.is_admin is never
    set here. A gate reading it would see the getattr default and wave everyone
    through — this must fail CLOSED when the profile can't be read."""
    import database.music_database as m
    clean_keys.set("security.require_login", True)

    def _boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(m, "get_database", _boom)
    with client.session_transaction() as s:
        s["login_authenticated"] = True
        s["profile_id"] = 1
    assert client.post("/api/v1/api-keys/bootstrap", json={}).status_code == 403


# ── 2. SSRF allowlist ────────────────────────────────────────────────────────
@pytest.fixture()
def cache(tmp_path):
    return ImageCache(tmp_path / "imgcache")


def _configured(monkeypatch, **pairs):
    import core.image_cache as ic
    monkeypatch.setattr(ic.config_manager, "get",
                        lambda key, default=None: pairs.get(key, default))


def test_public_hosts_are_still_allowed(cache, monkeypatch):
    _configured(monkeypatch)
    for u in ("https://image.tmdb.org/t/p/w342/a.jpg",
              "https://i.scdn.co/image/abc",
              "http://coverartarchive.org/release/1/front.jpg"):
        assert cache._is_fetch_allowed(u) is True, u


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # cloud instance metadata
    "http://127.0.0.1:8008/api/settings",
    "http://localhost/admin",
    "http://[::1]/x.png",
    "http://192.168.1.1/",                        # home router
    "http://10.0.0.5:9000/internal",
    "http://172.16.4.4/x",
    "http://host.docker.internal:9090/",
    "http://internal-only/x.png",                 # single-label docker service name
])
def test_internal_addresses_are_refused(cache, monkeypatch, url):
    _configured(monkeypatch)          # no media servers configured
    assert cache._is_fetch_allowed(url) is False, url


def test_a_configured_media_server_is_allowed(cache, monkeypatch):
    """The reason internal hosts can't just be banned: Plex/Jellyfin artwork
    legitimately lives on LAN/Docker URLs."""
    _configured(monkeypatch, **{"plex.base_url": "http://192.168.1.50:32400"})
    assert cache._is_fetch_allowed("http://192.168.1.50:32400/library/metadata/1/thumb") is True
    # ... and ONLY that host, not its neighbours
    assert cache._is_fetch_allowed("http://192.168.1.51:32400/x") is False
    assert cache._is_fetch_allowed("http://127.0.0.1/x") is False


def test_every_configured_server_key_counts(cache, monkeypatch):
    """Video can carry its own credentials or inherit music's, so both halves'
    keys have to be consulted — missing one silently breaks that install's art."""
    for key, host in (("plex.base_url", "plex.lan"),
                      ("jellyfin.base_url", "jelly.lan"),
                      ("navidrome.base_url", "nav.lan"),
                      ("video_plex.base_url", "vplex.lan"),
                      ("video_jellyfin.base_url", "vjelly.lan")):
        _configured(monkeypatch, **{key: f"http://{host}:8096"})
        assert cache._is_fetch_allowed(f"http://{host}:8096/art.png") is True, key


def test_non_http_schemes_and_embedded_credentials_are_refused(cache, monkeypatch):
    _configured(monkeypatch)
    for u in ("file:///etc/passwd", "gopher://x/1", "ftp://h/a.png",
              "http://user:pw@image.tmdb.org/a.jpg", "https://"):
        assert cache._is_fetch_allowed(u) is False, u


def test_the_check_is_no_longer_a_tautology(cache, monkeypatch):
    """The regression pin. The old form returned True for everything with a
    hostname; if it ever comes back, this is the test that notices."""
    _configured(monkeypatch)
    assert cache._is_fetch_allowed("http://169.254.169.254/") is False
    # Anchored to the STATEMENT, not the phrase: the docstring quotes the old
    # expression on purpose to explain the bug, and matching that would make
    # this guard fail on its own explanation.
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "core" / "image_cache.py").read_text(encoding="utf-8")
    assert "return bool(parsed.hostname) or" not in src
