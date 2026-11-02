from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from runtime.browser.tools import ExtractArgs, FillArgs, InspectArgs, NavigateArgs, SelectorArgs


class BrowserToolError(RuntimeError):
    pass


class BrowserController:
    """One isolated Chromium context, scoped to one exact user-approved origin."""

    def __init__(self, run_id: str, authorized_origin: tuple[str, str, int], data_dir: Path):
        self.run_id = run_id
        self.authorized_origin = authorized_origin
        self.data_dir = data_dir
        self._playwright = None
        self._browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.current_url = ""

    async def start(self) -> None:
        if self.page:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserToolError("Browser automation is not installed. Install runtime/requirements.txt to use the agent browser.") from exc
        self._playwright = await async_playwright().start()
        try:
            self._browser = await self._playwright.chromium.launch(headless=True)
            self.context = await self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                accept_downloads=False,
                service_workers="block",
            )
            await self.context.route("**/*", self._guard_request)
            self.page = await self.context.new_page()
        except Exception:
            await self.close()
            raise

    async def _guard_request(self, route) -> None:
        request_url = route.request.url
        if self._same_origin(request_url):
            await route.continue_()
        else:
            await route.abort("blockedbyclient")

    def _same_origin(self, url: str) -> bool:
        parsed = urlsplit(url)
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return False
        return (parsed.scheme, (parsed.hostname or "").lower().rstrip("."), port) == self.authorized_origin

    async def navigate(self, args: dict) -> dict:
        parsed = NavigateArgs.model_validate(args)
        target = str(parsed.url)
        if not self._same_origin(target):
            raise BrowserToolError("Navigation must stay on the HTTPS origin authorized by the user.")
        await self.start()
        assert self.page
        await self.page.goto(target, wait_until="domcontentloaded", timeout=25_000)
        final = self.page.url
        if not self._same_origin(final):
            raise BrowserToolError("The page redirected outside the authorized origin; navigation was stopped.")
        self.current_url = final
        return await self.inspect({})

    async def inspect(self, args: dict) -> dict:
        InspectArgs.model_validate(args)
        self._require_page()
        assert self.page
        title = await self.page.title()
        text = await self.page.locator("body").inner_text(timeout=5_000)
        return {"title": title[:500], "url": self.page.url, "visible_text": text[:12_000], "content_is_untrusted": True}

    async def extract_text(self, args: dict) -> dict:
        parsed = ExtractArgs.model_validate(args)
        self._require_page()
        assert self.page
        locator = self.page.locator(parsed.selector) if parsed.selector else self.page.locator("body")
        text = await locator.first.inner_text(timeout=5_000)
        return {"url": self.page.url, "selector": parsed.selector or "body", "text": text[:parsed.max_chars], "content_is_untrusted": True}

    async def screenshot(self) -> dict:
        self._require_page()
        assert self.page
        folder = self.data_dir / "screenshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{re.sub(r'[^a-zA-Z0-9_-]', '_', self.run_id)}.png"
        await self.page.screenshot(path=str(path), full_page=False, animations="disabled", timeout=10_000)
        return {"url": self.page.url, "screenshot_path": str(path), "format": "png"}

    async def click(self, args: dict) -> dict:
        parsed = SelectorArgs.model_validate(args)
        self._require_page()
        assert self.page
        await self.page.locator(parsed.selector).first.click(timeout=5_000)
        return {"url": self.page.url, "clicked": parsed.selector}

    async def fill(self, args: dict) -> dict:
        parsed = FillArgs.model_validate(args)
        self._require_page()
        assert self.page
        await self.page.locator(parsed.selector).first.fill(parsed.value, timeout=5_000)
        return {"url": self.page.url, "filled": parsed.selector}

    def _require_page(self) -> None:
        if not self.page:
            raise BrowserToolError("Navigate to the user-authorized page before using this browser tool.")

    async def close(self) -> None:
        if self.context:
            await self.context.close()
            self.context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self.page = None
