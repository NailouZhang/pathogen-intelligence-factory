from __future__ import annotations

import os
from typing import Any

from .utils import clean_space


DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def browser_enabled() -> bool:
    value = os.getenv("PIF_NEWS_BROWSER_ENABLED", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def fetch_rendered_html(
    url: str,
    *,
    timeout_ms: int | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Render one public page with Chromium and return HTML.

    This fallback is intentionally ordinary browser automation. It does not use
    stealth plugins, CAPTCHA solving, login automation, proxy rotation, or any
    mechanism intended to bypass access controls. A blocked or paywalled page is
    recorded as unavailable and the pipeline moves to another candidate.
    """
    if not browser_enabled():
        return {"status": "disabled", "url": url, "html": "", "error": "PIF_NEWS_BROWSER_ENABLED=false"}
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"status": "unavailable", "url": url, "html": "", "error": f"playwright import failed: {clean_space(exc)}"}

    timeout_ms = timeout_ms or int(os.getenv("PIF_NEWS_BROWSER_TIMEOUT_MS", "18000"))
    ua = clean_space(user_agent or os.getenv("PIF_NEWS_BROWSER_USER_AGENT") or DEFAULT_BROWSER_UA)
    browser = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=ua,
                locale="en-US",
                java_script_enabled=True,
                viewport={"width": 1365, "height": 900},
            )
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=min(7000, timeout_ms // 3))
            except PlaywrightTimeoutError:
                pass
            # Allow a short standards-based delay for client-rendered article
            # elements without repeatedly sleeping or simulating user actions.
            try:
                page.locator("article, main, [role='main']").first.wait_for(state="attached", timeout=3500)
            except Exception:
                pass
            final_url = page.url
            title = clean_space(page.title())
            html = page.content()
            status_code = response.status if response else None
            lower = clean_space(page.locator("body").inner_text(timeout=5000)).casefold()[:6000]
            blocked_markers = (
                "verify you are human", "access denied", "captcha", "enable cookies",
                "subscribe to continue", "sign in to continue", "temporarily unavailable",
            )
            blocked_http = bool(status_code and (status_code in {401, 403, 407, 408, 409, 429, 451} or status_code >= 500))
            blocked = blocked_http or any(marker in lower for marker in blocked_markers)
            context.close()
            browser.close()
            browser = None
            return {
                "status": "blocked" if blocked else "success",
                "url": final_url,
                "requested_url": url,
                "title": title,
                "html": html if not blocked else "",
                "http_status": status_code,
                "blocked": blocked,
                "blocked_http": blocked_http,
            }
    except Exception as exc:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        return {"status": "failed", "url": url, "html": "", "error": clean_space(exc)[:500]}
