"""Live proof that existing Chrome is discovered even when JARVIS would have focus."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agent.browser.discovery import BrowserWindowRegistry, scan_desktop
from src.agent.browser.existing import ExistingBrowserAdapter
from src.agent.browser.resolver import BrowserTargetResolver
from src.agent.computer import ComputerController
from src.agent.observe import cheapest_web_state
from src.agent.respond import format_current_page


def main() -> int:
    print("=== LIVE DISCOVERY via ComputerController (the JARVIS path) ===")
    computer = ComputerController()
    registry = BrowserWindowRegistry()
    resolver = BrowserTargetResolver(computer=computer, registry=registry, use_native=True)
    adapter = ExistingBrowserAdapter(computer, resolver)
    resolver.seed_from_desktop()

    state = cheapest_web_state(None, resolver=resolver, existing_adapter=adapter)
    print("spoken:", format_current_page(state))
    print("state:", {k: state.get(k) for k in (
        "open", "title", "url", "discovery_status", "browser_target_source",
        "browser_hwnd", "browser_monitor", "browser_selection_reason",
    )})

    print()
    print("=== RAW SCAN ===")
    scan = scan_desktop()
    print(f"total={scan['total']} visible={scan['visible']} candidates={len(scan['candidates'])} fg={scan['foreground_hwnd']}")
    for c in scan["candidates"]:
        print(f"  hwnd={c.hwnd} pid={c.pid} exe={c.process_name} class={c.window_class!r} mon={c.monitor_id} title={c.title!r} fg={c.is_foreground}")

    ok = bool(state.get("open") and (state.get("title") or state.get("url")))
    print()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
