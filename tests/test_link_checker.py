from __future__ import annotations

from unittest.mock import Mock

import pytest
import requests

from src.link_checker import canonicalize_url, check_link


class Response:
    def __init__(self, html="", status=200, content_type="text/html", headers=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.encoding = "utf-8"
        self._body = html.encode()
        self.closed = False

    def iter_content(self, chunk_size=65536):
        return [self._body]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def audit(html, public_resolver, **kwargs):
    return check_link(
        "https://source.com/article", "https://target.com/page",
        session=Session(Response(html)), resolver=public_resolver, **kwargs,
    )


@pytest.mark.parametrize(
    ("rel", "status"),
    [
        ("", "ACTIVE_FOLLOW"), ("nofollow", "ACTIVE_NOFOLLOW"),
        ("sponsored", "ACTIVE_SPONSORED"), ("ugc", "ACTIVE_UGC"),
        ("nofollow sponsored", "ACTIVE_MIXED_REL"),
    ],
)
def test_rel_statuses(rel, status, public_resolver) -> None:
    result = audit(f'<main><p>Useful <a href="https://target.com/page/" rel="{rel}">Target</a></p></main>', public_resolver)
    assert result.found is True
    assert result.status == status
    assert result.placement_area == "main_content"
    assert result.nearby_context == "Useful Target"


def test_relative_link_fragments_and_tracking_are_normalized(public_resolver) -> None:
    html = '<html><title>A</title><a href="https://www.target.com/page/?utm_source=x#part">Anchor</a></html>'
    result = audit(html, public_resolver)
    assert result.found
    assert result.source_page_title == "A"


def test_relative_link(public_resolver) -> None:
    result = check_link(
        "https://source.com/article", "https://source.com/resources/page",
        session=Session(Response('<article><a href="/resources/page/">Resource</a></article>')),
        resolver=public_resolver,
    )
    assert result.found


def test_canonicalization_preserves_real_path_and_query_differences() -> None:
    assert canonicalize_url("https://www.EXAMPLE.com/a/?utm_medium=x#z") == "https://example.com/a"
    assert canonicalize_url("https://example.com/a?x=1") != canonicalize_url("https://example.com/a?x=2")
    assert canonicalize_url("https://example.com/a") != canonicalize_url("https://example.com/b")


def test_missing_link_uses_cautious_status(public_resolver) -> None:
    result = audit("<p>No link</p>", public_resolver)
    assert result.status == "NOT_FOUND_IN_HTML"
    assert "static HTML" in result.error


@pytest.mark.parametrize("status", [404, 410])
def test_removed_source_status(status, public_resolver) -> None:
    result = check_link("https://source.com/a", "https://target.com/page", session=Session(Response(status=status)), resolver=public_resolver)
    assert result.status == "SOURCE_4XX"
    assert result.http_status == status


def test_timeout(public_resolver) -> None:
    result = check_link("https://source.com/a", "https://target.com/page", session=Session(requests.Timeout("secret URL omitted")), resolver=public_resolver)
    assert result.status == "SOURCE_UNAVAILABLE"
    assert "secret" not in result.error


def test_non_html(public_resolver) -> None:
    result = check_link("https://source.com/a", "https://target.com/page", session=Session(Response("pdf", content_type="application/pdf")), resolver=public_resolver)
    assert result.status == "NON_HTML_PAGE"


def test_oversized_page(public_resolver) -> None:
    result = check_link("https://source.com/a", "https://target.com/page", session=Session(Response("x" * 11)), resolver=public_resolver, max_page_bytes=10)
    assert result.status == "PAGE_TOO_LARGE"


@pytest.mark.parametrize("url", ["http://localhost/a", "http://127.0.0.1/a", "file:///tmp/a"])
def test_private_or_non_http_source_is_blocked(url) -> None:
    def private(host, port, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1", port))]
    result = check_link(url, "https://target.com/page", session=Mock(), resolver=private)
    assert result.status == "INVALID_SOURCE_URL"


def test_private_redirect_is_blocked(public_resolver) -> None:
    def resolver(host, port, **kwargs):
        ip = "127.0.0.1" if host == "localhost" else "93.184.216.34"
        return [(2, 1, 6, "", (ip, port))]
    session = Session(Response(status=302, headers={"Location": "http://localhost/admin"}))
    result = check_link("https://source.com/a", "https://target.com/page", session=session, resolver=resolver)
    assert result.status == "BLOCKED_OR_FORBIDDEN"
