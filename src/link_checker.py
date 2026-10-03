from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from .models import LinkCheck


LOGGER = logging.getLogger(__name__)
TRACKING_PARAMETERS = {"gclid", "fbclid", "msclkid", "mc_cid", "mc_eid"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


def canonicalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    port = parsed.port
    netloc = host if port is None or (parsed.scheme == "http" and port == 80) or (parsed.scheme == "https" and port == 443) else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    query = sorted(
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    )
    return urlunsplit((parsed.scheme.casefold(), netloc, path, urlencode(query), ""))


def _is_public_http_url(value: str, resolver: Callable[..., list[tuple]]) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        addresses = resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses:
            return False
        for item in addresses:
            address = ipaddress.ip_address(item[4][0].split("%", 1)[0])
            if not address.is_global:
                return False
        return True
    except (ValueError, OSError, socket.gaierror):
        return False


def _placement(anchor: Tag) -> str:
    for parent in [anchor, *anchor.parents]:
        if not isinstance(parent, Tag):
            continue
        name = parent.name.casefold()
        role = str(parent.get("role", "")).casefold()
        classes = " ".join(parent.get("class", [])).casefold()
        marker = f"{name} {role} {classes} {parent.get('id', '')}".casefold()
        if "comment" in marker:
            return "comments"
        if name == "header" or "header" in marker:
            return "header"
        if name == "footer" or "footer" in marker:
            return "footer"
        if name == "nav" or "navigation" in marker or " menu" in marker:
            return "navigation"
        if name == "aside" or "sidebar" in marker:
            return "sidebar"
        if name in {"main", "article"} or role == "main":
            return "main_content"
    return "unknown"


def _context(anchor: Tag) -> str:
    container = anchor.find_parent(["p", "li", "blockquote", "section", "article", "div"])
    return " ".join((container or anchor.parent or anchor).get_text(" ", strip=True).split())[:1200]


def _rel_status(rel: list[str]) -> str:
    restrictive = set(rel) & {"nofollow", "sponsored", "ugc"}
    if not restrictive:
        return "ACTIVE_FOLLOW"
    if len(restrictive) > 1:
        return "ACTIVE_MIXED_REL"
    return {"nofollow": "ACTIVE_NOFOLLOW", "sponsored": "ACTIVE_SPONSORED", "ugc": "ACTIVE_UGC"}[next(iter(restrictive))]


def _parse_html(
    html: str,
    current_url: str,
    target_url: str,
    status_code: int,
    *,
    rendered: bool = False,
) -> LinkCheck:
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join(soup.title.get_text(" ", strip=True).split())[:500] if soup.title else ""
    expected = canonicalize_url(target_url)
    for anchor in soup.find_all("a", href=True):
        resolved = urljoin(current_url, str(anchor.get("href", "")))
        if canonicalize_url(resolved) != expected:
            continue
        rel = sorted({str(item).casefold() for item in (anchor.get("rel") or [])})
        return LinkCheck(
            status=_rel_status(rel), found=True, rel=rel, http_status=status_code,
            resolved_target_url=resolved, anchor_text=" ".join(anchor.get_text(" ", strip=True).split())[:300],
            nearby_context=_context(anchor), source_page_title=title, placement_area=_placement(anchor),
        )
    return LinkCheck(
        status="NOT_FOUND_IN_HTML", found=False, http_status=status_code,
        resolved_target_url=target_url, source_page_title=title,
        error=(
            f"Expected link was not found in {'rendered' if rendered else 'static'} HTML; "
            "blocking or a temporary variation may require manual verification."
        ),
    )


def check_link(
    source_url: str,
    target_url: str,
    *,
    timeout: int = 15,
    max_page_bytes: int = 2_500_000,
    user_agent: str = "AI-Outreach-Link-Auditor/1.0",
    session: requests.Session | None = None,
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
    max_redirects: int = 5,
) -> LinkCheck:
    if not target_url.strip():
        return LinkCheck(status="NOT_CONFIGURED")
    if not _is_public_http_url(source_url, resolver):
        return LinkCheck(status="INVALID_SOURCE_URL", resolved_target_url=target_url, error="Source URL is invalid, unresolvable, or not public.")
    if not _is_public_http_url(target_url, resolver):
        return LinkCheck(status="INVALID_TARGET_URL", resolved_target_url=target_url, error="Target URL is invalid, unresolvable, or not public.")

    http = session or requests.Session()
    current_url = source_url
    response: requests.Response | None = None
    try:
        for _ in range(max_redirects + 1):
            response = http.get(
                current_url,
                timeout=timeout,
                headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"},
                allow_redirects=False,
                stream=True,
            )
            if response.status_code not in REDIRECT_STATUSES:
                break
            location = response.headers.get("Location", "")
            next_url = urljoin(current_url, location)
            response.close()
            if not location or not _is_public_http_url(next_url, resolver):
                return LinkCheck(status="BLOCKED_OR_FORBIDDEN", http_status=response.status_code, error="Redirect target is invalid or not public.")
            current_url = next_url
        else:
            return LinkCheck(status="SOURCE_UNAVAILABLE", error="Too many redirects.")

        assert response is not None
        status_code = response.status_code
        if status_code in {401, 403}:
            return LinkCheck(status="BLOCKED_OR_FORBIDDEN", http_status=status_code, error="Source page denied access.")
        if status_code in {404, 410} or 400 <= status_code < 500:
            return LinkCheck(status="SOURCE_4XX", http_status=status_code, error=f"Source page returned HTTP {status_code}.")
        if 500 <= status_code:
            return LinkCheck(status="SOURCE_5XX", http_status=status_code, error=f"Source page returned HTTP {status_code}.")

        content_type = response.headers.get("Content-Type", "").casefold()
        if "html" not in content_type:
            return LinkCheck(status="NON_HTML_PAGE", http_status=status_code)
        try:
            declared_size = int(response.headers.get("Content-Length", "0"))
        except ValueError:
            declared_size = 0
        if declared_size > max_page_bytes:
            return LinkCheck(status="PAGE_TOO_LARGE", http_status=status_code)

        body = bytearray()
        for chunk in response.iter_content(chunk_size=65_536):
            body.extend(chunk)
            if len(body) > max_page_bytes:
                return LinkCheck(status="PAGE_TOO_LARGE", http_status=status_code)
        html = bytes(body).decode(response.encoding or "utf-8", errors="replace")
    except requests.Timeout as exc:
        return LinkCheck(status="SOURCE_UNAVAILABLE", resolved_target_url=target_url, error=f"Link check timed out ({type(exc).__name__}).")
    except requests.RequestException as exc:
        return LinkCheck(status="SOURCE_UNAVAILABLE", resolved_target_url=target_url, error=f"Link check failed ({type(exc).__name__}).")
    finally:
        if response is not None:
            response.close()

    return _parse_html(html, current_url, target_url, status_code)


def check_link_in_browser(
    source_url: str,
    target_url: str,
    *,
    timeout: int = 25,
    max_page_bytes: int = 2_500_000,
    browser_executable: str = "",
    resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
) -> LinkCheck:
    """Use a clean, visible Chrome session after an HTTP 401/403; never bypass CAPTCHA."""
    if not _is_public_http_url(source_url, resolver) or not _is_public_http_url(target_url, resolver):
        return LinkCheck(
            status="BLOCKED_OR_FORBIDDEN", resolved_target_url=target_url,
            error="Browser fallback refused a non-public or invalid URL.",
        )
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return LinkCheck(
            status="BLOCKED_OR_FORBIDDEN", resolved_target_url=target_url,
            error="Browser fallback is unavailable because Playwright is not installed.",
        )

    try:
        with sync_playwright() as playwright:
            launch_options: dict[str, object] = {"headless": False}
            if browser_executable:
                launch_options["executable_path"] = browser_executable
            else:
                launch_options["channel"] = "chrome"
            browser = playwright.chromium.launch(**launch_options)
            try:
                context = browser.new_context()
                public_hosts: dict[tuple[str, int], bool] = {}

                def allow_public(route) -> None:
                    parsed = urlsplit(route.request.url)
                    if parsed.scheme in {"data", "blob"}:
                        route.continue_()
                        return
                    port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    key = (parsed.hostname or "", port)
                    allowed = public_hosts.get(key)
                    if allowed is None:
                        allowed = _is_public_http_url(route.request.url, resolver)
                        public_hosts[key] = allowed
                    if allowed:
                        route.continue_()
                    else:
                        route.abort()

                context.route("**/*", allow_public)
                page = context.new_page()
                response = page.goto(source_url, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(1500)
                final_url = page.url
                if not _is_public_http_url(final_url, resolver):
                    return LinkCheck(
                        status="BLOCKED_OR_FORBIDDEN", resolved_target_url=target_url,
                        error="Browser fallback redirected to a non-public or invalid URL.",
                    )
                status_code = response.status if response is not None else 0
                html = page.content()
                if len(html.encode("utf-8")) > max_page_bytes:
                    return LinkCheck(status="PAGE_TOO_LARGE", http_status=status_code, resolved_target_url=target_url)
                if status_code in {401, 403}:
                    return LinkCheck(
                        status="BLOCKED_OR_FORBIDDEN", http_status=status_code, resolved_target_url=target_url,
                        error="The visible Chrome fallback was also denied access; CAPTCHA or manual verification may be required.",
                    )
                return _parse_html(html, final_url, target_url, status_code or 200, rendered=True)
            finally:
                browser.close()
    except PlaywrightTimeoutError:
        return LinkCheck(
            status="BLOCKED_OR_FORBIDDEN", resolved_target_url=target_url,
            error="Visible Chrome fallback timed out; close any CAPTCHA and retry or verify manually.",
        )
    except PlaywrightError as exc:
        LOGGER.warning("Browser fallback failed: %s", type(exc).__name__)
        return LinkCheck(
            status="BLOCKED_OR_FORBIDDEN", resolved_target_url=target_url,
            error=f"Visible Chrome fallback could not start or load the page ({type(exc).__name__}).",
        )
