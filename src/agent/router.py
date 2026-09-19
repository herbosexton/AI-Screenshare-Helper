"""Fast-path command router. Direct commands never touch the planner LLM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FastIntent:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    speak: str = ""


_URL = re.compile(
    r"(https?://[^\s]+|(?:www\.)[^\s]+|[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z]{2,})(?:/[^\s]*)?)",
    re.I,
)

_PAGE_Q = re.compile(
    r"\b("
    r"what page am i on|"
    r"what website am i on|what site am i on|"
    r"what(?:'s| is) the (?:current )?(?:url|website|site)|"
    r"what(?:'s| is) (?:this |the )?(?:website|site)|"
    r"which (?:website|site|page) am i on|"
    r"current url|"
    r"what tab am i on|what browser tab"
    r")\b",
    re.I,
)
_APP_Q = re.compile(
    r"\b(what application am i in|what(?:'s| is)? (?:the )?app|"
    r"what window am i (?:looking at|in)|what(?:'s| is) (?:the )?active window)\b",
    re.I,
)

_SCREEN_STATUS = re.compile(
    r"\b("
    r"can you see (?:my |the )?screen|"
    r"do you see (?:my |the )?screen|"
    r"are you (?:viewing|looking at) (?:my |the )?screen|"
    r"do you have screen access|"
    r"are you able to see (?:my |the )?screen|"
    r"is screen (?:capture|access) (?:on|working|available)"
    r")\b",
    re.I,
)
_SCREEN_DESCRIBE = re.compile(
    r"\b("
    r"what do you see on (?:my |the )?screen|"
    r"what is on (?:my |the )?screen|"
    r"what(?:'s|s) on (?:my |the )?screen|"
    r"describe (?:my |the )?screen|"
    r"tell me what (?:you see|is) on (?:my |the )?screen|"
    r"what are you looking at"
    r")\b",
    re.I,
)
_MULTI_STEP = re.compile(
    r"\b(then|after that|and then|continue the application|fill .+ and|find .+ and click)\b",
    re.I,
)
_CLICK_CMD = re.compile(
    r"^(?:please )?(?:click|press|tap)(?:\s+(?:on|the))?\s+(.+?)$",
    re.I,
)

_OPEN_BROWSER = re.compile(
    r"\b(open|launch|start)(?:\s+up)?(?:\s+the)?\s+(?:google\s+)?(browser|chrome|edge|chromium)\b",
    re.I,
)
_OPEN_APP = {
    "cursor": "Cursor",
    "file explorer": "explorer",
    "explorer": "explorer",
    "outlook": "Outlook",
    "notepad": "Notepad",
    "calculator": "Calculator",
}

_TAB_ALIAS = {
    "gmail": "gmail",
    "linkedin": "linkedin",
    "youtube": "youtube",
    "job": "job",
}

_COMPOUND_SITES: dict[str, str] = {
    "google": "https://www.google.com",
    "google calendar": "https://calendar.google.com",
    "google docs": "https://docs.google.com",
    "google sheets": "https://sheets.google.com",
    "google drive": "https://drive.google.com",
    "google maps": "https://maps.google.com",
    "google meet": "https://meet.google.com",
    "google photos": "https://photos.google.com",
    "google news": "https://news.google.com",
    "chat gpt": "https://chatgpt.com",
    "chatgpt": "https://chatgpt.com",
    "trading view": "https://www.tradingview.com",
    "tradingview": "https://www.tradingview.com",
}


_NAV_CONTEXT = re.compile(
    r"\b(go to|go back|switch|open|navigate|take me|show me|return to)\b",
    re.I,
)
_CHAT_STT = re.compile(
    r"\bchat[\s\-]*(?:gbt|gtp|jpt|gpd|gpt|g\s*p\s*t)\b",
    re.I,
)
_DISCOURSE_LEAD = re.compile(
    r"^(?:now|just|so|okay|ok|please|hey|then|and|also|next|alright)\s+",
)
_GO_TO_SITE = re.compile(
    r"^(?:go to|navigate to|take me to|show me|open)\s+(.+)$",
)
_SWITCH_TO_TAB = re.compile(
    r"^(?:switch (?:back )?to|go back to|return to)\s+(.+)$",
)


def _repair_site_token(n: str) -> str:
    """Repair a site token that is already in site-resolution context."""
    t = (n or "").strip().lower()
    t = _CHAT_STT.sub("chatgpt", t)
    t = re.sub(r"\byou[\s\-]?tube\b", "youtube", t)
    t = re.sub(r"\bgit[\s\-]?hub\b", "github", t)
    return t


def _repair_site_stt(t: str) -> str:
    """Map spoken site variants only in browser/site commands."""
    if not t or not _NAV_CONTEXT.search(t):
        return t
    return _repair_site_token(t)


def _clean_site_target(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"^(the|a|an)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+in (?:google )?chrome$", "", t, flags=re.I)
    t = re.sub(r"\s+(tab|website|site|page|window)$", "", t, flags=re.I)
    return t.strip()


def _site_label(name: str, url: str = "") -> str:
    from src.agent.respond import page_identity

    ident = page_identity(url or "", "")
    if ident:
        return ident
    n = _clean_site_target(name)
    return n.replace("-", " ").title() if n else ""


def _site_url(name: str) -> str:
    n = _repair_site_token(_clean_site_target(_norm(name)))
    n = re.sub(r"^(the|a|an)\s+", "", n)
    if not n:
        return ""
    hit = _COMPOUND_SITES.get(n)
    if hit:
        return hit
    from src.agent.phase6.binding import KNOWN_SITES

    return str(KNOWN_SITES.get(n) or "")


def _norm(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u2032", "'").replace("`", "'")
    t = t.replace("what's", "what is").replace("whats", "what is")
    t = re.sub(r"[^\w\s.:/\-]+", " ", t)
    t = re.sub(r"[.!?]+$", "", t)
    t = " ".join(t.split())
    t = re.sub(r"\bwhat s\b", "what is", t)
    # Strip leading discourse ("okay now go to X") without eating the command.
    while True:
        nxt = _DISCOURSE_LEAD.sub("", t)
        if nxt == t:
            break
        t = nxt
    return _repair_site_stt(t)


def _is_page_or_website_question(t: str) -> bool:
    """True for current-site questions, including curly-apostrophe leftovers."""
    if not t:
        return False
    if re.search(r"\b(open|launch|go to|navigate|search|find|click)\b", t):
        return False
    if _PAGE_Q.search(t) or t in {
        "what page am i on",
        "what page am i on right now",
        "where am i",
        "what is the website",
        "what is the site",
        "whats the website",
        "the website",
        "website",
    }:
        return True
    return bool(re.search(r"\b(the website|the site|this website|this site|what website|what site)\b", t))


def _is_page_content_question(t: str) -> bool:
    if not t or re.search(r"\b(open|launch|go to|navigate|search|find|click)\b", t):
        return False
    return bool(re.search(
        r"\b("
        r"what is the page saying|what does the page say|"
        r"what is (?:the |this )?page (saying|about)|"
        r"what is it saying|what does it say|"
        r"whats the page saying|what is on the page|"
        r"tell me what the page (says|is saying)"
        r")\b",
        t,
    ))


_KIND_ALIASES = {
    "repo": "repository",
    "repos": "repository",
    "vid": "video",
    "watching": "video",
    "reading": "article",
}

_KIND_BLOCK = frozenset({
    "time", "weather", "task", "tasks", "date", "day",
    "monitor", "monitors", "screen", "screens",
    "website", "site", "url", "app", "application", "window",
    "name", "difference", "error", "current",
})


def page_entity_kind(t: str) -> Optional[str]:
    """Generic 'what X is this' — repo, video, course, article, …"""
    if not t or re.search(r"\b(open|launch|go to|navigate|search|find|click|add|enroll)\b", t):
        return None
    if re.search(r"\b(current )?(url|website|site)\b", t) and re.search(r"\bwhat\b", t):
        return None
    if re.search(r"\bwhat am i watching\b", t):
        return "video"
    m = (
        re.search(r"\bwhat(?: is)? (?:the |this )?([a-z][a-z0-9-]{1,20}) is this\b", t)
        or re.search(r"\bwhat is this ([a-z][a-z0-9-]{1,20})\b", t)
        or re.search(r"\bwhich ([a-z][a-z0-9-]{1,20}) is this\b", t)
        or re.search(r"\bwhat is the ([a-z][a-z0-9-]{1,20})\b", t)
    )
    if not m:
        return None
    kind = m.group(1)
    if kind in _KIND_BLOCK:
        return None
    if kind == "page" and re.search(r"\b(saying|says|about)\b", t):
        return None
    return _KIND_ALIASES.get(kind, kind)


def _is_page_entity_question(t: str) -> bool:
    return page_entity_kind(t) is not None


def _extract_url(text: str) -> str:
    m = _URL.search(text or "")
    if not m:
        return ""
    raw = m.group(1).rstrip(".,)")
    if raw.lower().startswith("http"):
        return raw
    return "https://" + raw.lstrip("/")


class FastCommandRouter:
    last_coverage: Any = None

    def route(self, text: str, *, skip_tasks: bool = False) -> Optional[FastIntent]:
        intent = self._match(text, skip_tasks=skip_tasks)
        return self._gated(text, intent)

    def _gated(self, text: str, intent: Optional[FastIntent]) -> Optional[FastIntent]:
        from src.agent.fast_coverage import analyze_fast_coverage

        report = analyze_fast_coverage(
            text,
            intent_action=intent.action if intent else "",
            intent_args=intent.args if intent else {},
        )
        self.last_coverage = report
        print(report.as_log())
        if intent is None:
            return None
        if not report.fast_route_allowed:
            return None
        return intent

    def _match(self, text: str, *, skip_tasks: bool = False) -> Optional[FastIntent]:
        from src.agent.command_clause import extract_command_clause
        from src.agent.fast_coverage import is_compound_multi_action
        from src.agent.voice_echo import strip_assistant_prefix

        extracted = extract_command_clause(text)
        payload, _prefix = strip_assistant_prefix(text)
        # A compound goal must be matched against the whole utterance, not one clause.
        if is_compound_multi_action(text):
            raw = (payload or text or "").strip()
        else:
            raw = (extracted.command_clause or payload or text or "").strip()
        t = _norm(raw)
        if not t:
            return None

        if re.search(r"\bhow many (monitors|screens|displays)\b", t) or t in {
            "how many monitors do i have",
            "how many monitors do i have open",
            "how many screens do i have",
            "how many displays do i have",
        }:
            return FastIntent("screen.monitor_count", confidence=0.99)

        if _is_page_content_question(t):
            return FastIntent("browser.page_about", confidence=0.99)

        kind = page_entity_kind(t)
        if kind:
            return FastIntent("browser.page_entity", args={"kind": kind}, confidence=0.99)

        if _is_page_or_website_question(t):
            from src.agent.browser.discovery import parse_preferred_browser

            preferred = parse_preferred_browser(t)
            args = {"preferred_browser": preferred} if preferred else {}
            if "url" in t:
                return FastIntent("browser.current_url", args=args, confidence=0.99)
            if "tab" in t:
                return FastIntent("browser.active_tab", args=args, confidence=0.99)
            return FastIntent("browser.current_page", args=args, confidence=0.99)

        from src.agent.screen.intent import ScreenIntentRouter

        screen_intent = ScreenIntentRouter().route(raw)
        if screen_intent is not None and not _MULTI_STEP.search(t):
            return screen_intent

        # Conversational follow-up: "And go to LinkedIn."
        t = re.sub(r"^(and|then|also)\s+", "", t)
        raw_follow = re.sub(r"^(and|then|also)\s+", "", raw, flags=re.I).strip()

        if not skip_tasks:
            from src.agent.voice_tasks import route_task_intent

            task_intent = route_task_intent(raw_follow or raw)
            if task_intent is not None:
                return task_intent

        if t in {"go back", "back", "browser back"} or re.fullmatch(r"(please )?go back( please)?", t):
            return FastIntent("browser.back", confidence=0.98)
        if t in {"go forward", "forward"}:
            return FastIntent("browser.forward", confidence=0.98)
        if t in {"refresh", "reload", "reload the page", "refresh the page"}:
            return FastIntent("browser.reload", confidence=0.98)

        if t in {"scroll down", "page down"}:
            return FastIntent("browser.scroll", args={"direction": "down"}, confidence=0.9)
        if t in {"scroll up", "page up"}:
            return FastIntent("browser.scroll", args={"direction": "up"}, confidence=0.9)
        if t in {"scroll to the top", "scroll back to the top", "scroll to top"}:
            return FastIntent("browser.scroll", args={"direction": "top"}, confidence=0.95)
        if t in {"scroll to the bottom", "scroll to bottom", "scroll to the end"}:
            return FastIntent("browser.scroll", args={"direction": "bottom"}, confidence=0.95)
        m = re.match(r"scroll (?:down )?to (?:the )?(.+?)(?: section)?$", t)
        if m:
            target = m.group(1).strip()
            if target not in {"top", "bottom", "the top", "the bottom"}:
                return FastIntent("browser.scroll_to", args={"name": target}, confidence=0.9)

        if t in {"what tabs do i have open", "what tabs are open", "list tabs", "what tabs"}:
            return FastIntent("browser.list_tabs", confidence=0.98)
        if t in {"close this tab", "close the tab", "close tab"}:
            return FastIntent("browser.close_tab", confidence=0.95)
        if t in {"switch to the next tab", "next tab", "go to the next tab"}:
            return FastIntent("browser.next_tab", confidence=0.95)

        m = re.search(
            r"\b(?:open |start )?(?:a |another )?new tab(?: and(?: then)? (?:go to|open|navigate to) (.+))?$",
            t,
        )
        if m or t in {"open a new tab", "new tab", "open new tab", "open another tab"}:
            dest = (m.group(1) or "").strip() if m else ""
            url = _site_url(dest) if dest else ""
            if not url and dest:
                extracted = _extract_url(dest)
                url = extracted
            args = {"url": url} if url else {}
            return FastIntent("browser.new_tab", args=args, confidence=0.96)
        if re.search(
            r"\bwhat (is (this|the) page about|does this page say)\b", t
        ) or t in {"summarize this page", "summarize the page", "what does this page say"}:
            return FastIntent("browser.page_about", confidence=0.9)

        m = _SWITCH_TO_TAB.match(t)
        if m:
            query = _clean_site_target(m.group(1).strip())
            if query:
                url = _site_url(query)
                args: dict[str, Any] = {"query": query}
                if url:
                    args["url"] = url
                    args["site"] = _site_label(query, url)
                    args["recognized"] = "BROWSER_SWITCH_TO_TAB"
                return FastIntent("browser.switch_tab", args=args, confidence=0.92)

        m = re.match(r"fill (?:the )?(.+?)(?: field)? with (.+)$", t)
        if m:
            return FastIntent(
                "browser.fill",
                args={"name": m.group(1).strip(), "text": m.group(2).strip()},
                confidence=0.9,
            )
        if re.fullmatch(r"(please )?check (the )?checkbox", t):
            return FastIntent("browser.check", args={"selector": "input[type=checkbox]"}, confidence=0.85)
        if not re.search(r"\b(check off|tick off|check-off|cross out|cross off)\b", t):
            # A checkbox label is short and "check in/out/on/up/with" is a phrasal verb,
            # not a command. Without both guards a spoken sentence beginning "check in
            # with Tony..." is taken as the name of a checkbox to tick.
            m = re.match(r"(?:check|tick) (?:the )?([^.,;?!]{1,40})$", t)
            if (
                m
                and "checkbox" not in m.group(1)
                and not re.match(r"(?:in|out|on|up|with|for|back|around)\b", m.group(1))
            ):
                return FastIntent("browser.check", args={"name": m.group(1).strip()}, confidence=0.85)
        m = re.match(r"select (?:option )?(.+)$", t)
        if m:
            return FastIntent("browser.select", args={"value": m.group(1).strip()}, confidence=0.85)
        if t in {"close it", "close the modal", "close modal", "dismiss the dialog"}:
            return FastIntent("browser.dismiss_dialog", confidence=0.85)
        if t in {"open the modal", "open modal"}:
            return FastIntent("screen.click", args={"name": "Open modal"}, confidence=0.8)
        if t in {"close this window", "close the window"}:
            return FastIntent("computer.close_window", confidence=0.85)
        if re.fullmatch(r"(please )?(close|shut) (the |that |my )?(resume|cv|document)( window)?", t):
            return FastIntent(
                "computer.close_window",
                args={"kind": "resume", "query": "resume"},
                confidence=0.93,
            )
        if t in {"close the browser", "close browser", "close chrome"}:
            return FastIntent("browser.close", confidence=0.95)

        url = _extract_url(raw_follow or raw)
        if url and (_OPEN_BROWSER.search(t) or re.search(r"\b(go to|navigate to|open)\b", t)):
            return FastIntent("browser.open_and_goto", args={"url": url}, confidence=0.97)

        m = _GO_TO_SITE.match(t)
        if m:
            target = _clean_site_target(m.group(1).strip())
            url = _site_url(target)
            if url:
                kind = "BROWSER_OPEN_SITE" if t.startswith("open ") else "BROWSER_GO_TO_SITE"
                return FastIntent(
                    "browser.open_and_goto",
                    args={"url": url, "site": _site_label(target, url), "recognized": kind},
                    confidence=0.93,
                )

        if _OPEN_BROWSER.search(t) and not url:
            return FastIntent("browser.open", confidence=0.95)

        for needle, app in _OPEN_APP.items():
            if re.search(rf"\b(open|launch|start)\b.*\b{re.escape(needle)}\b", t):
                return FastIntent("computer.open_application", args={"name": app}, confidence=0.9)

        return None
