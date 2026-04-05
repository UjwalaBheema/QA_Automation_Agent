from typing import Optional, List
import os
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

RESULTS_DIR = Path("results")


class BrowserManager:
    """
    Manages Playwright browser lifecycle with auth state reuse, tracing, and video recording.
    Use as an async context manager:

        async with BrowserManager() as bm:
            page = await bm.new_page()
            ...
    """

    AUTH_STATE_PATH = RESULTS_DIR / "auth_state.json"

    def __init__(
        self,
        headless: Optional[bool] = None,
        slow_mo: Optional[int] = None,
        record_video: bool = True,
    ):
        self.headless = headless if headless is not None else os.getenv("HEADLESS", "true").lower() == "true"
        self.slow_mo = slow_mo if slow_mo is not None else int(os.getenv("SLOW_MO", "0"))
        self.record_video = record_video

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._tracing_active = False
        self._current_trace_name: Optional[str] = None

    async def start(self) -> "BrowserManager":
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "videos").mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "traces").mkdir(parents=True, exist_ok=True)
        (RESULTS_DIR / "screenshots").mkdir(parents=True, exist_ok=True)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
        )

        context_kwargs: dict = {
            "viewport": {"width": 1280, "height": 720},
        }
        if self.record_video:
            context_kwargs["record_video_dir"] = str(RESULTS_DIR / "videos")
            context_kwargs["record_video_size"] = {"width": 1280, "height": 720}

        # HTTP Basic Auth — handles the browser-level "Sign In" pop-up
        basic_user = os.getenv("BASIC_AUTH_USER")
        basic_pass = os.getenv("BASIC_AUTH_PASSWORD")
        if basic_user and basic_pass:
            context_kwargs["http_credentials"] = {
                "username": basic_user,
                "password": basic_pass,
            }
            logger.info(f"HTTP Basic Auth configured for user: {basic_user}")

        # Reuse saved auth state if available
        if self.AUTH_STATE_PATH.exists():
            context_kwargs["storage_state"] = str(self.AUTH_STATE_PATH)
            logger.info("Loaded saved auth state")

        self._context = await self._browser.new_context(**context_kwargs)
        logger.info(f"Browser started (headless={self.headless})")
        return self

    async def start_tracing(self, name: str):
        if self._context is None:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        await self._context.tracing.start(
            name=name,
            screenshots=True,
            snapshots=True,
            sources=True,
        )
        self._tracing_active = True
        self._current_trace_name = name
        logger.debug(f"Tracing started: {name}")

    async def stop_tracing(self, name: Optional[str] = None) -> str:
        if self._context is None or not self._tracing_active:
            return ""
        trace_name = name or self._current_trace_name or "trace"
        trace_path = str(RESULTS_DIR / "traces" / f"{trace_name}.zip")
        await self._context.tracing.stop(path=trace_path)
        self._tracing_active = False
        self._current_trace_name = None
        logger.info(f"Trace saved: {trace_path}")
        return trace_path

    async def new_page(self) -> Page:
        if self._context is None:
            raise RuntimeError("BrowserManager not started. Call start() first.")
        page = await self._context.new_page()
        return page

    async def save_auth_state(self):
        if self._context is None:
            return
        self.AUTH_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self.AUTH_STATE_PATH))
        logger.info(f"Auth state saved: {self.AUTH_STATE_PATH}")

    async def close(self, save_auth: bool = False):
        if save_auth:
            await self.save_auth_state()
        if self._tracing_active:
            await self.stop_tracing()
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed")

    async def __aenter__(self) -> "BrowserManager":
        return await self.start()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
