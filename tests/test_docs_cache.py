import io
from pathlib import Path

import pytest

from maniac.config import Config
from maniac.sources.docs import cache


def test_release_metadata_revalidates_after_positive_window_expires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The positive revalidation window (release.py's caller) is independent
    of, and longer than, the definitive-absence window: pin it directly
    against `_download_cached_result` rather than through the full probe
    stack, whose own (shorter) definitive-absence cache would otherwise
    force a re-probe -- but not a metadata re-fetch -- partway through.
    """
    now = 1000.0
    calls = 0

    def download(url: str, cfg: object) -> bytes:
        nonlocal calls
        calls += 1
        return b"first" if calls == 1 else b"second"

    monkeypatch.setattr(cache.time, "time", lambda: now)
    monkeypatch.setattr(cache, "_download", download)
    cfg = Config(cache_dir=tmp_path)
    url = "https://api.github.com/repos/owner/tool/releases/tags/v1.2.3"

    def fetch() -> bytes | None:
        return cache._download_cached_result(
            url, tmp_path, cfg, max_age=cache._RELEASE_METADATA_REVALIDATION_TTL
        )[0]

    assert fetch() == b"first"
    now += cache._RELEASE_METADATA_REVALIDATION_TTL - 1
    assert fetch() == b"first"
    assert calls == 1

    now += 2
    assert fetch() == b"second"
    assert calls == 2


@pytest.mark.parametrize("status", [404, 410])
def test_definitively_missing_download_is_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    from email.message import Message
    from urllib.error import HTTPError

    calls = 0

    def missing(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise HTTPError("https://example.test/page", status, "missing", Message(), None)

    monkeypatch.setattr(cache, "_open", missing)
    cfg = Config(cache_dir=tmp_path)
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    assert calls == 1


def test_expired_definitively_missing_download_refreshes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from email.message import Message
    from urllib.error import HTTPError

    now = 1000.0
    calls = 0

    def missing(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise HTTPError("https://example.test/page", 404, "missing", Message(), None)

    monkeypatch.setattr(cache.time, "time", lambda: now)
    monkeypatch.setattr(cache, "_open", missing)
    cfg = Config(cache_dir=tmp_path)
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    now += cache._DEFINITIVE_ABSENCE_TTL + 1
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    assert calls == 2


@pytest.mark.parametrize("failure", ["url", "server"])
def test_transient_download_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    from email.message import Message
    from urllib.error import HTTPError, URLError

    calls = 0

    def unavailable(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if failure == "url":
            raise URLError("offline")
        raise HTTPError(
            "https://example.test/page", 503, "unavailable", Message(), None
        )

    monkeypatch.setattr(cache, "_open", unavailable)
    cfg = Config(cache_dir=tmp_path)
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    assert (
        cache._download_cached_result("https://example.test/page", tmp_path, cfg)[0]
        is None
    )
    assert calls == 2


@pytest.mark.parametrize("status", [401, 403])
def test_auth_and_rate_limit_failures_are_not_definitive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int
) -> None:
    from email.message import Message
    from urllib.error import HTTPError

    def denied(*args: object, **kwargs: object) -> object:
        raise HTTPError(
            "https://api.github.com/release", status, "denied", Message(), None
        )

    monkeypatch.setattr(cache, "_open", denied)
    cfg = Config(cache_dir=tmp_path)

    content, definitive = cache._download_result("https://api.github.com/release", cfg)

    assert content is None
    assert definitive is False


def test_authorization_header_only_for_github_api_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache, "resolve_github_token", lambda: "stub-token")

    assert (
        cache._github_headers("https://api.github.com/repos/x/y")["Authorization"]
        == "Bearer stub-token"
    )
    assert (
        cache._github_headers("https://API.GitHub.com/x")["Authorization"]
        == "Bearer stub-token"
    )
    assert (
        cache._github_headers("https://api.github.com:443/x")["Authorization"]
        == "Bearer stub-token"
    )
    assert "Authorization" not in cache._github_headers(
        "https://objects.githubusercontent.com/x"
    )
    assert "Authorization" not in cache._github_headers(
        "https://evil.example.com/api.github.com"
    )
    assert "Authorization" not in cache._github_headers(
        "https://api.github.com.evil.test/x"
    )
    assert "Authorization" not in cache._github_headers("https://evil.api.github.com/x")
    assert "Authorization" not in cache._github_headers("https://api.github.com./x")
    assert "Authorization" not in cache._github_headers(
        "https://api.github.com@evil.test/x"
    )


def test_no_authorization_header_without_a_token() -> None:
    assert "Authorization" not in cache._github_headers(
        "https://api.github.com/repos/x/y"
    )


def test_redirect_handler_drops_authorization_across_hosts() -> None:
    from http.client import HTTPMessage
    from urllib.request import Request

    request = Request(
        "https://api.github.com/repos/x/y/releases/assets/1",
        headers={"Authorization": "Bearer secret", "User-Agent": "maniac"},
    )
    handler = cache._AuthStrippingRedirectHandler()

    new_request = handler.redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "https://objects.githubusercontent.com/x",
    )

    assert new_request is not None
    assert "Authorization" not in new_request.headers


def test_redirect_handler_keeps_authorization_same_host() -> None:
    from http.client import HTTPMessage
    from urllib.request import Request

    request = Request(
        "https://api.github.com/repos/x/y/releases/assets/1",
        headers={"Authorization": "Bearer secret", "User-Agent": "maniac"},
    )
    handler = cache._AuthStrippingRedirectHandler()

    new_request = handler.redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "https://api.github.com/other/path",
    )

    assert new_request is not None
    assert new_request.headers.get("Authorization") == "Bearer secret"


def test_redirect_handler_drops_authorization_on_scheme_downgrade() -> None:
    from http.client import HTTPMessage
    from urllib.request import Request

    request = Request(
        "https://api.github.com/repos/x/y/releases/assets/1",
        headers={"Authorization": "Bearer secret", "User-Agent": "maniac"},
    )
    handler = cache._AuthStrippingRedirectHandler()

    new_request = handler.redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        HTTPMessage(),
        "http://api.github.com/other/path",
    )

    assert new_request is not None
    assert "Authorization" not in new_request.headers


def test_download_degrades_on_invalid_request_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache, "_open", lambda *args, **kwargs: None)

    assert cache._download("\x00", Config()) is None
