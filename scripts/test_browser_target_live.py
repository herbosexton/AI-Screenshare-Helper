"""Live desktop test: browser target resolution with real Chrome.

Run this with Chrome already open on any monitor.
Reports all metrics required by the Definition of Done.
"""

from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.browser.existing import ExistingBrowserAdapter
from src.agent.browser.resolver import BrowserTargetResolver
from src.agent.computer.windows import WindowsComputerPlatform
from src.agent.observe import cheapest_web_state


def main():
    print("=" * 70)
    print("JARVIS BROWSER TARGET RESOLUTION — LIVE TEST")
    print("=" * 70)
    print()

    computer = WindowsComputerPlatform()
    resolver = BrowserTargetResolver(computer=computer)
    adapter = ExistingBrowserAdapter(computer, resolver)

    # -- Test 1: Enumerate all browser windows --
    print("--- TEST 1: Enumerate browser windows across all monitors ---")
    all_browsers = resolver.find_all()
    print(f"  Found {len(all_browsers)} browser window(s):")
    for bw in all_browsers:
        print(f"    hwnd={bw.hwnd}  pid={bw.process_id}  monitor={bw.monitor_index}")
        print(f"    title={bw.window_title!r}")
        print(f"    bounds={bw.bounds}  foreground={bw.is_foreground}  managed={bw.playwright_owned}")
        print(f"    source={bw.source}  tab_title={bw.active_tab_title!r}")
        print()

    if not all_browsers:
        print("  [!] No Chrome windows found. Open Chrome and re-run.")
        return

    print("--- TEST 2: Foreground browser window ---")
    fg = resolver.find_foreground_browser()
    if fg:
        print(f"  [OK] Foreground browser: hwnd={fg.hwnd}")
        print(f"    title={fg.window_title!r}")
        print(f"    monitor={fg.monitor_index}  source={fg.source}")
    else:
        print("  [!] No foreground browser (another app is focused)")
        existing = resolver.find_existing_chrome()
        if existing:
            print(f"  [OK] Existing Chrome: hwnd={existing.hwnd} title={existing.window_title!r}")

    print()
    print("--- TEST 3: cheapest_web_state ---")
    state = cheapest_web_state(None, resolver=resolver, existing_adapter=adapter)
    print(f"  browser_target_source = {state.get('browser_target_source', 'N/A')}")
    print(f"  browser_hwnd          = {state.get('browser_hwnd', 'N/A')}")
    print(f"  browser_pid           = {state.get('browser_pid', 'N/A')}")
    print(f"  browser_monitor       = {state.get('browser_monitor', 'N/A')}")
    print(f"  browser_window_title  = {state.get('browser_window_title', 'N/A')}")
    print(f"  browser_selection_reason = {state.get('browser_selection_reason', 'N/A')}")
    print(f"  title                 = {state.get('title', 'N/A')}")
    print(f"  url                   = {state.get('url', 'N/A')}")
    print(f"  about:blank returned  = {'about:blank' in state.get('url', '')}")

    print()
    print("--- TEST 4: should_launch_new ---")
    print(f"  '' (contextual):                {resolver.should_launch_new('')}")
    print(f"  'Open a separate browser':      {resolver.should_launch_new('Open a separate browser')}")
    print(f"  'Open an automation browser':   {resolver.should_launch_new('Open an automation browser')}")

    print()
    print("--- TEST 5: Focus existing Chrome ---")
    result = adapter.focus()
    print(f"  ok={result.get('ok')}  hwnd={result.get('hwnd')}  title={result.get('title')}")

    print()
    print("--- TEST 6: Get current page (via title + address bar) ---")
    time.sleep(0.5)
    page = adapter.get_current_page()
    print(f"  ok={page.get('ok')}")
    print(f"  window_title     = {page.get('window_title', 'N/A')}")
    print(f"  active_tab_title = {page.get('active_tab_title', 'N/A')}")
    print(f"  url              = {page.get('url', 'N/A')}")
    print(f"  source           = {page.get('source', 'N/A')}")
    print(f"  is_foreground    = {page.get('is_foreground', 'N/A')}")

    print()
    print("--- TEST 7: open_application('chrome') focuses existing ---")
    result = computer.open_application("chrome")
    print(f"  method={result.get('method')}")
    print(f"  new_process={result.get('new_process', 'N/A')}")
    print(f"  hwnd={result.get('hwnd', 'N/A')}")
    print(f"  title={result.get('title', 'N/A')}")
    print(f"  focused={result.get('focused', 'N/A')}")

    print()
    print("--- TEST 8: Monitor information ---")
    screen = computer.get_screen_size()
    print(f"  Primary: {screen.width}x{screen.height}")
    print(f"  Monitors: {len(screen.monitors)}")
    for i, m in enumerate(screen.monitors):
        print(f"    [{i}] {m}")

    # ── Summary ──
    print()
    print("=" * 70)
    print("REPORT SUMMARY")
    print("=" * 70)
    about_blank_count = sum(1 for bw in all_browsers if "about:blank" in bw.window_title.lower())
    existing_count = sum(1 for bw in all_browsers if not bw.playwright_owned)
    managed_count = sum(1 for bw in all_browsers if bw.playwright_owned)
    report = {
        "foreground_browser_hwnd": fg.hwnd if fg else "none",
        "selected_monitor": fg.monitor_index if fg else -1,
        "selected_session_source": state.get("browser_target_source", "N/A"),
        "browser_windows_found": len(all_browsers),
        "existing_desktop_windows": existing_count,
        "managed_playwright_windows": managed_count,
        "about_blank_windows": about_blank_count,
        "active_page_identified": state.get("title", "N/A"),
        "url_identified": state.get("url", "N/A"),
        "open_chrome_method": result.get("method", "N/A"),
        "new_chrome_processes_launched": 0 if result.get("method") == "focused_existing" else 1,
    }
    for k, v in report.items():
        print(f"  {k:40s} = {v}")

    print()
    checks = [
        ("Existing Chrome found", existing_count > 0),
        ("about:blank NOT selected as active page", "about:blank" not in state.get("url", "")),
        ("Source is EXISTING_DESKTOP", state.get("browser_target_source") == "EXISTING_DESKTOP"),
        ("Page title identified", bool(state.get("title"))),
        ("Open Chrome focused existing", result.get("method") == "focused_existing"),
        ("No new Chrome process", result.get("new_process", True) is False),
    ]
    all_pass = True
    for label, passed in checks:
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {label}")
        if not passed:
            all_pass = False

    print()
    if all_pass:
        print("  [PASS] ALL CHECKS PASSED")
    else:
        print("  [FAIL] SOME CHECKS FAILED -- see above")
    print()


if __name__ == "__main__":
    main()
