from typing import Optional, List
import json
import os
from pathlib import Path

import yaml
from loguru import logger

from playwright_framework.browser_manager import BrowserManager
from utils.claude_client import ask

CONFIG_PATH = Path("config/agents_config.yaml")
AUTH_STATE  = Path("results/auth_state.json")

# Flows to always SKIP — login/signup handled separately by login_setup.py
AUTH_FLOW_NAMES = {"login", "logout", "signup", "register", "sign_in", "sign_out", "forgot_password"}

# Post-login seed flows — discovered after authentication
SEED_FLOWS = [
    {
        "name": "dashboard_overview",
        "description": "View the main dashboard after login and verify key widgets/stats load",
        "steps": [
            "Navigate to dashboard home",
            "Wait for page to fully load",
            "Verify key sections are visible (charts, stats, navigation)",
            "Take a screenshot of the full dashboard",
        ],
        "expected_outcome": "Dashboard loads with all expected components visible",
        "priority": "high",
    },
    {
        "name": "navigation_check",
        "description": "Click through all main navigation items and verify each page loads",
        "steps": [
            "Identify all top-level navigation links",
            "Click each navigation item",
            "Verify page loads without errors",
            "Take a screenshot of each page",
        ],
        "expected_outcome": "All navigation pages load without errors",
        "priority": "medium",
    },
]


class FlowDiscoveryAgent:
    """
    Discovers automatable post-login user flows by navigating the authenticated
    dashboard and analyzing page structure with Claude.
    Requires auth_state.json — run scripts/login_setup.py first.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        self.system_prompt = config["flow_discovery"]["system_prompt"]

    def _post_login_url(self, login_url: str) -> str:
        """Strip /login suffix to get the base app URL for post-login discovery."""
        base = login_url.rstrip("/")
        for suffix in ["/login", "/signin", "/sign-in", "/auth"]:
            if base.lower().endswith(suffix):
                return base[: -len(suffix)]
        return base

    async def run(self, url: Optional[str] = None) -> List[dict]:
        login_url  = url or os.getenv("CHARTREQUEST_URL", "https://qa.greencheckhealth.com/login")
        target_url = self._post_login_url(login_url)
        logger.info(f"[FlowDiscovery] Discovering post-login flows at {target_url}")

        if not AUTH_STATE.exists():
            logger.warning(
                "[FlowDiscovery] No auth_state.json found. "
                "Run 'python scripts/login_setup.py' first. Using seed flows only."
            )
            return SEED_FLOWS

        html_content      = ""
        interactive_tree  = ""

        try:
            async with BrowserManager(headless=True, record_video=False) as bm:
                page = await bm.new_page()
                await page.goto(target_url, wait_until="networkidle", timeout=30000)

                current_url = page.url
                logger.info(f"[FlowDiscovery] Landed on: {current_url}")

                # If redirected to login, auth state is stale
                if "login" in current_url.lower() or "signin" in current_url.lower():
                    logger.warning(
                        "[FlowDiscovery] Redirected to login — auth state may be expired. "
                        "Re-run python scripts/login_setup.py"
                    )
                    return SEED_FLOWS

                html_content = await page.content()
                interactive = await page.evaluate("""() => {
                    const els = document.querySelectorAll('a, button, input, select, textarea, [role], nav, [data-testid]');
                    return Array.from(els).slice(0, 100).map(el => ({
                        tag:  el.tagName.toLowerCase(),
                        type: el.type || null,
                        role: el.getAttribute('role') || null,
                        name: el.textContent?.trim().slice(0, 80)
                              || el.getAttribute('aria-label')
                              || el.getAttribute('data-testid')
                              || el.name || null,
                        href: el.href || null,
                    }));
                }""")
                interactive_tree = json.dumps(interactive, indent=2)[:4000]
                logger.info(f"[FlowDiscovery] Page loaded — HTML: {len(html_content)} chars")

        except Exception as e:
            logger.warning(f"[FlowDiscovery] Could not load page: {e}. Using seed flows only.")
            return SEED_FLOWS

        prompt = (
            f"Analyze this authenticated web application page from {target_url} "
            "and identify all key POST-LOGIN user flows worth automating.\n\n"
            "DO NOT include login, logout, signup, or password-reset flows — those are handled separately.\n\n"
            "Focus on: dashboard views, data entry forms, search, filters, reports, "
            "chart requests, document actions, settings, user management, etc.\n\n"
            f"--- HTML CONTENT (first 5000 chars) ---\n{html_content[:5000]}\n\n"
            f"--- INTERACTIVE ELEMENTS ---\n{interactive_tree}\n\n"
            "Return a JSON array of flow objects as specified."
        )

        try:
            response = ask(self.system_prompt, prompt)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            flows = json.loads(cleaned.strip())

            # Filter out any auth flows Claude might have included anyway
            flows = [f for f in flows if f.get("name", "").lower() not in AUTH_FLOW_NAMES]
            logger.info(f"[FlowDiscovery] Claude discovered {len(flows)} post-login flows")

        except Exception as e:
            logger.error(f"[FlowDiscovery] Failed to parse Claude response: {e}")
            flows = []

        # Merge with seeds, avoid duplicates
        existing_names = {f["name"] for f in flows}
        for seed in SEED_FLOWS:
            if seed["name"] not in existing_names:
                flows.append(seed)

        logger.info(f"[FlowDiscovery] Total flows: {len(flows)}")
        return flows
