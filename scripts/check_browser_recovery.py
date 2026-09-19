"""What the browser tools do after the user closes the browser window themselves."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import yaml

from src.agent.browser.policy import UrlPolicy
from src.agent.browser.session import BrowserSession


def show(label: str, result) -> None:
    if not isinstance(result, dict):
        print(f"{label}: {result!r}")
        return
    err = result.get("error") or {}
    print(
        f"{label}: success={result.get('success')} "
        f"code={err.get('code')} message={str(err.get('message') or '')[:140]!r}"
    )


def main() -> None:
    config = yaml.safe_load(open("config.yaml", encoding="utf-8"))
    bcfg = config.get("browser") or {}
    session = BrowserSession(
        policy=UrlPolicy(
            allowed_hosts=bcfg.get("allowed_hosts") or [],
            blocked_hosts=bcfg.get("blocked_hosts") or [],
        ),
        profile_dir=Path("data/browser_probe_profile"),
        headed=True,
        persist_profile=False,
        channel=bcfg.get("channel", "chrome"),
    )
    try:
        show("open       ", session.agent.open())
        show("goto #1    ", session.goto("https://example.com"))

        print("\n-- closing every page the way a user closes the window --")
        for page in list(session.agent.driver.pages()):
            try:
                page.close()
            except Exception as e:
                print(f"   close error: {e}")
        time.sleep(1.0)

        show("goto #2    ", session.agent.navigate("https://example.org"))
        show("new_tab    ", session.agent.new_tab("https://example.org"))
        print(f"status     : {json.dumps(session.status())[:220]}")

        print("\n-- can it come back on its own? --")
        show("open again ", session.agent.open())
        show("goto #3    ", session.agent.navigate("https://example.org"))
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
