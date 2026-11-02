from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

import httpx

from runtime.aip.policy import is_public_host


SEARCH_ENDPOINT = "https://html.duckduckgo.com/html/"


class _DuckDuckGoResults(HTMLParser):
    def __init__(self):
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None
        self._capture: str | None = None

    def handle_starttag(self, tag: str, attrs):
        values = dict(attrs)
        classes = set((values.get("class") or "").split())
        if tag == "a" and "result__a" in classes:
            raw_url = values.get("href", "")
            parsed = urlsplit(raw_url)
            target = parse_qs(parsed.query).get("uddg", [raw_url])[0]
            self._current = {"url": unquote(target), "title": "", "snippet": ""}
            self._capture = "title"
        elif tag in {"a", "div"} and "result__snippet" in classes and self.results:
            self._capture = "snippet"

    def handle_data(self, data: str):
        if self._capture == "title" and self._current is not None:
            self._current["title"] += data
        elif self._capture == "snippet" and self.results:
            self.results[-1]["snippet"] += data

    def handle_endtag(self, tag: str):
        if self._capture == "title" and tag == "a" and self._current is not None:
            item = {key: value.strip() for key, value in self._current.items()}
            if item["title"] and item["url"]:
                self.results.append(item)
            self._current = None
            self._capture = None
        elif self._capture == "snippet" and tag in {"a", "div"}:
            self._capture = None


def _valid_result_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in (None, 443):
            return False
        return is_public_host(parsed.hostname)
    except ValueError:
        return False


async def search_public_web(query: str, limit: int = 3) -> list[dict[str, str]]:
    """Return bounded public search results from a fixed, credential-free endpoint."""
    clean_query = " ".join(query.split())
    if not 2 <= len(clean_query) <= 500:
        raise ValueError("Search query must contain between 2 and 500 characters.")
    headers = {"User-Agent": "KeplerV/0.1 (local research browser)"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(12), follow_redirects=False, headers=headers) as client:
        response = await client.get(f"{SEARCH_ENDPOINT}?{urlencode({'q': clean_query})}")
        response.raise_for_status()
    parser = _DuckDuckGoResults()
    parser.feed(response.text[:1_000_000])
    safe: list[dict[str, str]] = []
    seen: set[str] = set()
    # DNS checks can block briefly; keep them off the event loop.
    for result in parser.results:
        url = result["url"]
        if url in seen or not await asyncio.to_thread(_valid_result_url, url):
            continue
        seen.add(url)
        safe.append({"url": url, "title": result["title"][:300], "snippet": result["snippet"][:1000]})
        if len(safe) >= max(1, min(limit, 3)):
            break
    return safe
