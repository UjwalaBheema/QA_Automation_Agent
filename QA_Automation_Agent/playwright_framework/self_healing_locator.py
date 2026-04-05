from typing import Optional, List
from loguru import logger
from playwright.async_api import Page, Locator


class SelfHealingLocator:
    """
    A resilient Playwright locator that tries a primary selector, falls back to
    alternatives, and optionally calls an AI repair agent when all selectors fail.
    """

    def __init__(
        self,
        page: Page,
        primary_selector: str,
        fallbacks: List[str] | None = None,
        description: str = "",
        timeout: int = 5000,
    ):
        self.page = page
        self.primary_selector = primary_selector
        self.fallbacks = fallbacks or []
        self.description = description or primary_selector
        self.timeout = timeout

    async def _try_selector(self, selector: str) -> Locator | None:
        try:
            locator = self.page.locator(selector)
            await locator.wait_for(state="visible", timeout=self.timeout)
            logger.debug(f"[SelfHealing] Found '{self.description}' with: {selector}")
            return locator
        except Exception:
            logger.debug(f"[SelfHealing] Selector failed: {selector}")
            return None

    async def locate(self) -> Locator:
        all_selectors = [self.primary_selector] + self.fallbacks
        for selector in all_selectors:
            locator = await self._try_selector(selector)
            if locator is not None:
                return locator

        # All selectors failed — attempt AI-assisted repair
        logger.warning(
            f"[SelfHealing] All {len(all_selectors)} selectors failed for '{self.description}'. "
            "Attempting AI repair..."
        )
        repaired_selector = await self._ai_repair()
        if repaired_selector:
            locator = await self._try_selector(repaired_selector)
            if locator is not None:
                self.primary_selector = repaired_selector  # cache the fix
                return locator

        raise RuntimeError(
            f"SelfHealingLocator: Could not locate '{self.description}' "
            f"with any selector: {all_selectors}"
        )

    async def _ai_repair(self) -> Optional[str]:
        """Capture DOM snapshot and ask Claude for a working selector."""
        try:
            from utils.claude_client import ask
            dom = await self.page.content()
            prompt = (
                f"I'm trying to locate this element on a web page: '{self.description}'\n\n"
                f"These selectors all failed:\n"
                + "\n".join(f"  - {s}" for s in [self.primary_selector] + self.fallbacks)
                + f"\n\nHere is the current page HTML (truncated to 4000 chars):\n{dom[:4000]}\n\n"
                "Return ONLY a single Playwright-compatible CSS or text selector that would match "
                "this element. No explanation, no code, just the selector string."
            )
            system = (
                "You are a Playwright expert. Given a DOM snapshot and a failed element description, "
                "return exactly one working CSS selector or Playwright locator string. Nothing else."
            )
            result = ask(system, prompt, max_tokens=200)
            selector = result.strip().strip('"').strip("'")
            logger.info(f"[SelfHealing] AI suggested selector: {selector}")
            return selector
        except Exception as e:
            logger.error(f"[SelfHealing] AI repair failed: {e}")
            return None

    async def click(self, **kwargs):
        locator = await self.locate()
        await locator.click(**kwargs)

    async def fill(self, text: str, **kwargs):
        locator = await self.locate()
        await locator.fill(text, **kwargs)

    async def get_text(self) -> str:
        locator = await self.locate()
        return await locator.inner_text()

    async def is_visible(self) -> bool:
        try:
            locator = await self.locate()
            return await locator.is_visible()
        except Exception:
            return False
