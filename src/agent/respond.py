"""Turn tool/state payloads into short spoken replies. No LLM."""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse


def _normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return "https://" + raw.lstrip("/")
    return raw


# Conversation titles (e.g. "AI Desktop Agent Build") are not the page the user
# is on. Known hosts win so chatgpt.com is spoken as ChatGPT.
_SITE_IDENTITY: dict[str, str] = {
    "chatgpt.com": "ChatGPT",
    "chat.openai.com": "ChatGPT",
    "google.com": "Google",
    "google.co.uk": "Google",
    "mail.google.com": "Gmail",
    "calendar.google.com": "Google Calendar",
    "youtube.com": "YouTube",
    "github.com": "GitHub",
    "linkedin.com": "LinkedIn",
    "reddit.com": "Reddit",
    "amazon.com": "Amazon",
    "wikipedia.org": "Wikipedia",
    "facebook.com": "Facebook",
    "netflix.com": "Netflix",
    "x.com": "X",
    "twitter.com": "X",
    "coursera.org": "Coursera",
    "learn.coursera.org": "Coursera",
}


def _host_label(url: str) -> str:
    """Speak the website (coursera.org → Coursera), not a subdomain or path."""
    host = (urlparse(_normalize_url(url)).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net", "gov", "ac"}:
        base = parts[-3]
    elif len(parts) >= 2:
        base = parts[-2]
    else:
        base = parts[0]
    return base.replace("-", " ").title()


def page_identity(url: str = "", title: str = "") -> str:
    """Website from the URL host. Tab/course titles are not the site."""
    host = (urlparse(_normalize_url(url)).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _SITE_IDENTITY:
        return _SITE_IDENTITY[host]
    parts = host.split(".")
    if len(parts) >= 2:
        parent = ".".join(parts[-2:])
        if parent in _SITE_IDENTITY:
            return _SITE_IDENTITY[parent]
        if len(parts) >= 3:
            parent3 = ".".join(parts[-3:])
            if parent3 in _SITE_IDENTITY:
                return _SITE_IDENTITY[parent3]
    return _host_label(url) or _title_label(title)


def _title_label(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    t = re.split(r"\s+[|\-–—]\s+", t)[0].strip()
    return t[:80]


_SITE_TITLE_SUFFIX = re.compile(
    r"\s+[-–—|]\s+(YouTube|Coursera|Gmail|Netflix|Spotify|LinkedIn|GitHub|"
    r"Google Chrome|Microsoft Edge|Firefox)\s*$",
    re.I,
)


def _entity_title(title: str) -> str:
    """Full video/course title. Keep 'FIRST TAKE | …' — only strip the site suffix."""
    t = (title or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+-\s+Google Chrome\s*$", "", t, flags=re.I)
    t = _SITE_TITLE_SUFFIX.sub("", t).strip()
    return t[:160]


def format_page(url: str = "", title: str = "", open_: bool = True) -> str:
    if not open_ or not (url or title):
        return "No browser is open."
    label = _title_label(title) or _host_label(url) or url
    if url and url.startswith("http") and label.lower() not in (url or "").lower():
        return f"You're on {label}."
    if label:
        return f"You're on {label}."
    return f"You're on {url}."


def _monitor_phrase(data: dict[str, Any]) -> str:
    bounds = data.get("bounds") or ()
    if isinstance(bounds, (list, tuple)) and len(bounds) >= 1:
        try:
            if int(bounds[0]) < 0:
                return " on the left monitor"
        except (TypeError, ValueError):
            pass
    mon = data.get("browser_monitor")
    if mon in (1, "1", "DISPLAY2"):
        return " on the left monitor"
    if mon in (0, "0", "DISPLAY1"):
        return " on the main monitor"
    return ""


def format_current_page(state: Optional[dict[str, Any]] = None) -> str:
    """Spoken reply for current-page questions. Distinguishes discovery states."""
    data = state or {}
    status = data.get("discovery_status") or ""
    title = data.get("title") or data.get("browser_window_title") or ""
    url = data.get("url") or ""
    if status == "NO_BROWSER_WINDOW" or (not data.get("open") and not title):
        return "No browser is open."
    if status == "BROWSER_FOUND_STATE_UNAVAILABLE" and not title and not url:
        return "I found Chrome, but I could not read the current page."
    label = page_identity(url, title)
    app = "Chrome"
    src_name = str(data.get("process_name") or data.get("exe_path") or "").lower()
    if "msedge" in src_name:
        app = "Edge"
    elif "firefox" in src_name:
        app = "Firefox"
    if status == "BROWSER_FOUND_URL_UNAVAILABLE" or (title and not url):
        if label:
            return f"You're on {label} in {app}."
        return f"{app} is open, but I could not read the current URL."
    if label:
        return f"You're on {label} in {app}."
    if url:
        return f"You're on {url} in {app}."
    return f"{app} is open."


def _slug_label(url: str) -> str:
    raw = (url or "").split("#")[0].split("?")[0]
    path = urlparse(_normalize_url(raw)).path.strip("/")
    if not path:
        return ""
    skip = {
        "specializations", "professional-certificates", "certificates", "learn",
        "course", "courses", "programs", "c", "watch", "v", "u",
    }
    parts = [p for p in path.split("/") if p and p.lower() not in skip]
    slug = parts[-1] if parts else path.split("/")[-1]
    return slug.replace("-", " ").replace("_", " ").title()


def page_entity_label(url: str = "", title: str = "") -> str:
    """Course/article name from the tab title or URL path — not the site."""
    site = page_identity(url, "")
    label = _entity_title(title)
    if label and site and label.lower() == site.lower():
        label = ""
    return label or _slug_label(url)


def format_page_entity(state: Optional[dict[str, Any]] = None, *, kind: str = "course") -> str:
    data = state or {}
    status = data.get("discovery_status") or ""
    if status == "NO_BROWSER_WINDOW" or (not data.get("open") and not data.get("title") and not data.get("url")):
        return "No browser is open."
    label = page_entity_label(
        data.get("url") or "",
        data.get("title") or data.get("browser_window_title") or "",
    )
    site = page_identity(data.get("url") or "", "")
    noun = re.sub(r"[^a-z0-9-]", "", (kind or "page").lower()) or "page"
    if not label:
        if site:
            return f"I can see {site}, but not the specific {noun}."
        return "I could not read the current page."
    return f"The {noun} is {label}."


def format_url(url: str = "", open_: bool = True, *, discovery_status: str = "") -> str:
    if discovery_status == "NO_BROWSER_WINDOW" or (not open_ and not url):
        return "No browser is open."
    if not url:
        return "Chrome is open, but I could not read the current URL."
    return f"The current URL is {url}."


def format_tab(title: str = "", url: str = "", open_: bool = True) -> str:
    if not open_ and not title:
        return "There is no active browser tab."
    label = _title_label(title) or _host_label(url) or "a page"
    return f"The active tab is {label}."


def format_window(title: str = "", app: str = "") -> str:
    name = app or title
    if not name:
        return "I could not see an active window."
    if app and title:
        return f"You're in {app}: {title}."
    return f"You're in {name}."


def format_screen_status(status: Optional[dict[str, Any]] = None) -> str:
    data = status or {}
    if not data.get("available"):
        return "I do not have screen access right now."
    n = int(data.get("monitor_count") or 0)
    if n > 1:
        return f"Yes. I can see your screen. {n} monitors are available."
    return "Yes. I can see your screen."


def format_tabs(tabs: list[dict[str, Any]] | None = None, open_: bool = True) -> str:
    if not open_:
        return "No browser is open."
    items = tabs or []
    if not items:
        return "There are no browser tabs."
    labels = []
    for t in items[:8]:
        title = _title_label(t.get("title") or "") or _host_label(t.get("url") or "") or "a tab"
        labels.append(title)
    if len(labels) == 1:
        return f"You have one tab: {labels[0]}."
    return "You have " + str(len(items)) + " tabs: " + ", ".join(labels) + "."


def format_page_about(
    title: str = "",
    headings: list[str] | None = None,
    text: str = "",
    state: Optional[dict[str, Any]] = None,
) -> str:
    data = state or {}
    url = str(data.get("url") or "")
    title = title or str(data.get("title") or data.get("browser_window_title") or "")
    if data.get("discovery_status") == "NO_BROWSER_WINDOW" or (
        state is not None and not data.get("open") and not title and not url
    ):
        return "No browser is open."
    label = page_entity_label(url, title) or _title_label(title)
    heads = [h for h in (headings or []) if h]
    if heads:
        about = heads[0]
        if label and about.lower() not in label.lower():
            return f"This page is {label}: {about}."
        return f"This page is about {about}."
    snippet = (text or str(data.get("text") or "")).strip().split("\n")[0][:160]
    if label and snippet and snippet.lower() not in label.lower():
        return f"The page is about {label}. It says: {snippet}"
    if label:
        return f"The page is about {label}."
    if snippet:
        return f"This page says: {snippet}"
    return "I could not read this page."


def format_screen_describe(summary: str = "", vision_text: str = "") -> str:
    spoken = (vision_text or "").strip()
    if spoken:
        spoken = re.sub(r"\s+", " ", spoken)
        return spoken[:400]
    summary = (summary or "").strip()
    if summary:
        return f"I can see {summary}."
    return "I captured the screen but could not describe it."


def format_action(action: str, **kwargs: Any) -> str:
    url = kwargs.get("url") or ""
    title = kwargs.get("title") or ""
    app = kwargs.get("application") or kwargs.get("name") or ""
    if action in {"open", "navigate", "goto"}:
        label = _title_label(title) or _host_label(url) or url or "the page"
        return f"Opened {label}."
    if action == "back":
        return "Went back."
    if action == "forward":
        return "Went forward."
    if action == "reload":
        return "Refreshed the page."
    if action == "scroll":
        return "Scrolled."
    if action in {"fill", "type"}:
        return "Filled the field."
    if action in {"check", "uncheck"}:
        return "Updated the checkbox."
    if action == "select":
        return "Selected the option."
    if action in {"newTab", "new_tab"}:
        return "Opened a new tab."
    if action == "dismissDialog":
        return "Closed the dialog."
    if action == "close":
        return "Closed the browser."
    if action == "open_application":
        return f"{app or 'The app'} is open."
    if action == "close_window":
        return "Closed the window."
    return "Done."


def format_tool_batch(results: list[dict[str, Any]]) -> Optional[str]:
    """Spoken line after a successful deterministic tool sequence."""
    if not results:
        return None
    last = results[-1] or {}
    action = last.get("action") or ""
    if last.get("success") is False:
        err = last.get("error") or {}
        if isinstance(err, dict):
            return err.get("message") or "That didn't work."
        return str(err or "That didn't work.")
    url = last.get("url") or ""
    title = last.get("title") or ""
    if action in {"navigate", "goto", "open", "back", "forward", "reload"}:
        return format_action(action, url=url, title=title)
    if action in {"scroll", "close", "open_application", "close_window", "fill", "check", "uncheck", "select", "newTab", "new_tab", "dismissDialog"}:
        return format_action(action, url=url, title=title, application=last.get("application") or "")
    if url or title:
        return format_page(url, title, True)
    return None
