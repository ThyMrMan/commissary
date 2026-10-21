"""Commissary's -c.N versions are compared, and the check asks our repo."""

from core.commissary import brand
from core.commissary.update_check import evaluate_update, parse_version


def _rel(tag, name=None, body="", draft=False, prerelease=False):
    return {"tag_name": tag, "name": name or f"Version {tag}", "body": body,
            "draft": draft, "prerelease": prerelease,
            "html_url": f"https://github.com/x/y/releases/{tag}"}


def test_parse_version_reads_the_fork_counter():
    assert parse_version("3.5.7-c.2") == (3, 5, 7, 2)
    assert parse_version("v3.5.7-c.12+abc1234") == (3, 5, 7, 12)
    assert parse_version("3.5.7") == (3, 5, 7, 0)
    assert parse_version("unknown") is None


def test_a_newer_fork_release_on_the_same_upstream_base_is_offered():
    """Upstream's parser drops '-c.N', so c.2 vs c.1 looked identical."""
    res = evaluate_update("3.5.7-c.1", [_rel("3.5.7-c.2")])
    assert res["available"] is True
    assert res["latest_version"] == "3.5.7-c.2"
    assert res["severity"] == "update"


def test_same_or_older_is_not_an_update():
    assert evaluate_update("3.5.7-c.2", [_rel("3.5.7-c.2"), _rel("3.5.7-c.1")])["available"] is False


def test_a_merged_upstream_bump_beats_any_fork_counter():
    res = evaluate_update("3.5.7-c.9", [_rel("3.5.8-c.1"), _rel("3.5.7-c.9")])
    assert res["latest_version"] == "3.5.8-c.1"


def test_newest_wins_regardless_of_listing_order():
    res = evaluate_update("3.5.7-c.1", [_rel("3.5.7-c.2"), _rel("3.5.7-c.4"), _rel("3.5.7-c.3")])
    assert res["latest_version"] == "3.5.7-c.4"


def test_severity_rules_match_upstream():
    assert evaluate_update("3.5.7-c.1", [_rel("4.0.0-c.1")])["severity"] == "major"
    crit = evaluate_update("3.5.7-c.1", [_rel("3.5.7-c.3"), _rel("3.5.7-c.2", body="security fix")])
    assert crit["severity"] == "critical"


def test_drafts_and_prereleases_are_ignored():
    res = evaluate_update("3.5.7-c.1", [_rel("3.5.7-c.2", draft=True), _rel("3.5.7-c.3", prerelease=True)])
    assert res["available"] is False


def test_the_running_app_checks_our_repo_with_our_parser():
    import web_server

    assert web_server._GITHUB_REPO == brand.GITHUB_REPO
    src = open(web_server.__file__, encoding="utf-8").read()
    assert "from core.commissary.update_check import evaluate_update" in src


def test_the_app_version_is_upstream_plus_a_fork_counter():
    import web_server

    assert parse_version(web_server._SOULSYNC_BASE_VERSION)[3] >= 1
