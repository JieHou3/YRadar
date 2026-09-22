"""Open a visible Tesla Chrome (same profile as the monitor) so you can pass Akamai / cookies once."""
import asyncio
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

from config import CURRENT_DIR, TESLA_ZIP, TESLA_SEARCH_RANGE, TESLA_MODELS
from fetchers.tesla_fetcher import TeslaFetcher
from fetchers.cdp_chrome import (
    cdp_base_url, force_kill_cdp_port, launch_chrome_debug,
    wait_for_cdp_ready, show_chrome_window, force_chrome_on_screen,
    clear_chrome_window_placement, find_chrome,
)

CDP_PORT = TeslaFetcher.CDP_PORT
PROFILE_DIR = os.path.join(CURRENT_DIR, 'chrome_debug_profile_stealth')
LIST_URL = (
    f"https://www.tesla.com/inventory/used/{TESLA_MODELS[0]}"
    f"?arrangeby=plh&zip={TESLA_ZIP}&range={TESLA_SEARCH_RANGE}"
)


async def _run():
    chrome_path = find_chrome()
    if not chrome_path:
        print(f"[error] Google Chrome not found.")
        return 1

    os.makedirs(PROFILE_DIR, exist_ok=True)

    print("[Warmup] Closing leftover Tesla monitor Chrome...")
    force_kill_cdp_port(CDP_PORT)
    clear_chrome_window_placement(PROFILE_DIR)

    print(f"[Warmup] Profile: {PROFILE_DIR}")
    print(f"[Warmup] Debug port: {CDP_PORT}")
    print("[Warmup] Launching a visible Chrome window (main screen 80,60)...")
    proc = launch_chrome_debug(
        chrome_path, CDP_PORT, PROFILE_DIR,
        visible=True, window_size='1280,900',
    )
    if not await wait_for_cdp_ready(CDP_PORT):
        force_kill_cdp_port(CDP_PORT, proc)
        print("[error] Chrome debug port did not come up. Close other Chrome windows using this profile, then retry.")
        return 1

    await asyncio.sleep(1.5)
    n = force_chrome_on_screen(proc.pid if proc else None)
    print(f"[Warmup] Win32 moved {n} window(s)")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(
            cdp_base_url(CDP_PORT), timeout=15000,
        )
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        print(f"[Warmup] Opening: {LIST_URL}")
        await page.goto(LIST_URL, wait_until='domcontentloaded', timeout=60000)
        await show_chrome_window(page, chrome_pid=proc.pid if proc else None)
        force_chrome_on_screen(proc.pid if proc else None)

        content = await page.content()
        if 'Access Denied' in content:
            print()
            print("[status] Still Access Denied.")
            print("Refresh in Chrome, complete the challenge, or wait, until the used inventory list appears.")
        else:
            print()
            print("[status] No Access Denied in the page source (if you can see the list, the session is usable).")

        print()
        print("What to do:")
        print("  1. Confirm you can browse Tesla used inventory in the Chrome window")
        print("  2. If there is a captcha / Press & Hold, complete it by hand")
        print("  3. Close that Chrome window when it works (cookies stay in the stealth profile)")
        print("  4. Run start.bat (or python monitor.py --loop)")
        print()
        print("Waiting for you to close Chrome… (or Ctrl+C in this window)")

        try:
            while True:
                await asyncio.sleep(2)
                if not await wait_for_cdp_ready(CDP_PORT, timeout=1.0):
                    print("[Warmup] Chrome closed.")
                    break
        except KeyboardInterrupt:
            print("\n[Warmup] Interrupted — closing Chrome...")
            force_kill_cdp_port(CDP_PORT, proc)

    print("[Warmup] Done. If the list loaded, start the monitor.")
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(_run()))
