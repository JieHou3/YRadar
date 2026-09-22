"""Chrome CDP helpers — use 127.0.0.1; on Windows localhost may resolve to ::1."""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Optional

CDP_HOST = '127.0.0.1'
_OWNED_PROCESSES = {}

_CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def find_chrome():
    """Return the first existing Chrome / Chromium binary, or None."""
    for path in _CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def cdp_base_url(port: int) -> str:
    return f'http://{CDP_HOST}:{port}'


def is_cdp_ready(port: int, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(
            f'{cdp_base_url(port)}/json/version', timeout=timeout
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


async def wait_for_cdp_ready(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_cdp_ready(port):
            return True
        await asyncio.sleep(0.5)
    return False


def clear_chrome_window_placement(profile_dir: str) -> None:
    """Drop remembered off-screen window coords so the next launch is on-screen."""
    prefs_path = os.path.join(profile_dir, 'Default', 'Preferences')
    if not os.path.exists(prefs_path):
        return
    try:
        with open(prefs_path, 'r', encoding='utf-8') as f:
            prefs = json.load(f)
        browser = prefs.get('browser') or {}
        changed = False
        for key in ('window_placement', 'window_placement_popup', 'window_placement_app'):
            if key in browser:
                del browser[key]
                changed = True
        if changed:
            prefs['browser'] = browser
            with open(prefs_path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, ensure_ascii=False, separators=(',', ':'))
            print(' [CDP] Cleared remembered off-screen Chrome window position.')
    except Exception:
        pass


def minimize_console_window() -> bool:
    """Minimize this console during a challenge so it does not cover Chrome."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            return True
    except Exception:
        pass
    return False


def restore_console_window() -> bool:
    """Restore the console after a challenge."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            return True
    except Exception:
        pass
    return False


def force_chrome_on_screen(pid: Optional[int] = None, *, bring_to_front: bool = True) -> int:
    """
    Windows Win32: move off-screen Chrome windows onto the primary display (80,60).
    bring_to_front=False: move only, do not steal focus (required during Press & Hold;
    otherwise a failed SetForegroundWindow can pop this console in front).
    """
    if sys.platform != 'win32' or pid is None:
        return 0

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    SW_RESTORE = 9
    HWND_TOP = 0
    SWP_SHOWWINDOW = 0x0040
    SWP_NOACTIVATE = 0x0010
    GW_OWNER = 4

    EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    moved = []

    def _callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != pid:
            return True
        if user32.GetWindow(hwnd, GW_OWNER):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)
        cls = class_buf.value or ''
        is_chrome_class = cls.startswith('Chrome_WidgetWin')
        title = ''
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ''
        lower = title.lower()
        if not is_chrome_class and 'chrome' not in lower and 'tesla' not in lower:
            return True

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        left, top = rect.left, rect.top
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width < 50 or height < 50:
            return True
        offscreen = left < -50 or top < -50 or left > 3500 or top > 2500
        if not offscreen and 'tesla' not in lower:
            return True

        flags = SWP_SHOWWINDOW
        if not bring_to_front:
            flags |= SWP_NOACTIVATE
        else:
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetWindowPos(
            hwnd, HWND_TOP, 80, 60, 1400, 900, flags
        )
        if bring_to_front:
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass
        moved.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(_callback), 0)
    return len(moved)


def launch_chrome_debug(
    chrome_path: str,
    port: int,
    profile_dir: str,
    *,
    visible: bool = False,
    window_size: str = '1200,800',
) -> subprocess.Popen:
    clear_chrome_window_placement(profile_dir)

    args = [
        chrome_path,
        f'--remote-debugging-port={port}',
        f'--remote-debugging-address={CDP_HOST}',
        f'--user-data-dir={profile_dir}',
        f'--window-size={window_size}',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-session-crashed-bubble',
        '--disable-infobars',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-features=CalculateNativeWinOcclusion',
    ]
    if visible:
        args.append('--window-position=80,60')
    else:
        args.append('--window-position=-2400,-2400')

    kwargs = {}
    if sys.platform == 'win32':
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_BREAKAWAY_FROM_JOB = 0x01000000
        kwargs['creationflags'] = CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        kwargs['close_fds'] = True

    if is_cdp_ready(port):
        raise RuntimeError(f'Port {port} is already in use. Close the dedicated YRadar Chrome window before restarting.')
    if sys.platform == 'win32' and not visible:
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        kwargs['startupinfo'] = startup
    proc = subprocess.Popen(args, **kwargs)
    _OWNED_PROCESSES[port] = proc
    return proc


async def _window_id_for_page(page):
    browser = page.context.browser
    if not browser:
        raise RuntimeError('no browser context')

    session = await browser.new_browser_cdp_session()
    targets = await session.send('Target.getTargets')
    target_id = None
    page_url = page.url or ''
    for info in targets.get('targetInfos', []):
        if info.get('type') != 'page':
            continue
        url = info.get('url', '')
        if page_url and url == page_url:
            target_id = info['targetId']
            break
        if target_id is None and 'tesla.com' in url:
            target_id = info['targetId']

    if not target_id:
        for info in targets.get('targetInfos', []):
            if info.get('type') == 'page':
                target_id = info['targetId']
                break
    if not target_id:
        raise RuntimeError('no page target')

    win = await session.send('Browser.getWindowForTarget', {'targetId': target_id})
    return session, win['windowId']


async def minimize_chrome_window(page) -> None:
    """Park the window off-screen (more reliable than minimize after the display sleeps)."""
    try:
        session, window_id = await _window_id_for_page(page)
        await session.send('Browser.setWindowBounds', {
            'windowId': window_id,
            'bounds': {
                'left': -2400,
                'top': -2400,
                'width': 1280,
                'height': 900,
                'windowState': 'normal',
            },
        })
    except Exception:
        pass


async def show_chrome_window(page, chrome_pid: Optional[int] = None) -> None:
    """When a human challenge is needed: CDP + Win32, pull Chrome onto the main screen."""
    cdp_ok = False
    try:
        session, window_id = await _window_id_for_page(page)
        await session.send('Browser.setWindowBounds', {
            'windowId': window_id,
            'bounds': {'windowState': 'normal'},
        })
        await session.send('Browser.setWindowBounds', {
            'windowId': window_id,
            'bounds': {
                'left': 80,
                'top': 60,
                'width': 1400,
                'height': 900,
                'windowState': 'normal',
            },
        })
        await page.bring_to_front()
        try:
            page_session = await page.context.new_cdp_session(page)
            await page_session.send('Page.bringToFront')
        except Exception:
            pass
        cdp_ok = True
    except Exception as exc:
        print(f" [CDP] Browser.setWindowBounds failed: {exc}")

    # Win32 is the reliable path: move off-screen Chrome / Tesla-titled windows
    moved = force_chrome_on_screen(chrome_pid)
    if moved:
        print(f" [CDP] Moved {moved} Chrome window(s) back on-screen (80,60).")
    elif not cdp_ok:
        print(" [CDP] Could not pop Chrome automatically. Run warmup_tesla.py, or Alt+Tab to Chrome.")


def terminate_chrome_proc(proc: Optional[subprocess.Popen]) -> None:
    if not proc:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def force_kill_cdp_port(port: int, proc: Optional[subprocess.Popen] = None) -> None:
    """Close only Chrome launched by this process; never kill an arbitrary listener."""
    owned = _OWNED_PROCESSES.get(port)
    if proc is not None and owned is not None and proc is not owned:
        raise RuntimeError('Refusing to stop a Chrome process not owned by this monitor.')
    target = owned or proc
    if target is not None:
        if target.poll() is None:
            terminate_chrome_proc(target)
        _OWNED_PROCESSES.pop(port, None)
    # An unknown listener belongs to another monitor or application. Leave it alone.


def close_cdp_port(port: int, proc: Optional[subprocess.Popen] = None) -> None:
    """Close CDP Chrome on this port."""
    force_kill_cdp_port(port, proc)


async def release_cdp_port(port: int) -> None:
    """Best-effort graceful close (standalone tools only; the monitor uses force_kill_cdp_port)."""
    if not is_cdp_ready(port):
        return
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(
                cdp_base_url(port), timeout=5000
            )
            await browser.close()
    except Exception:
        pass
    deadline = time.monotonic() + 10
    while is_cdp_ready(port) and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
