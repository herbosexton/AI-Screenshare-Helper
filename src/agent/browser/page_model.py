"""PageModel: browser chrome and the web document are different surfaces."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

WEB_DOCUMENT = "WEB_DOCUMENT"
BROWSER_CHROME = "BROWSER_CHROME"
BROWSER_EXTENSION = "BROWSER_EXTENSION"
OS_UI = "OS_UI"
UNKNOWN = "UNKNOWN"

JOB_LISTING = "JOB_LISTING"
JOB_APPLICATION = "JOB_APPLICATION"
GENERIC = "GENERIC"

SOURCE_SCOPES = (WEB_DOCUMENT, BROWSER_CHROME, BROWSER_EXTENSION, OS_UI, UNKNOWN)

_CHROME_LABELS = {
    "home",
    "view site information",
    "bookmark this tab",
    "clear input",
    "close side panel",
    "managed bookmarks",
    "ask google",
    "free vpn for chrome",
    "open tab in split view",
    "wants access to this site",
    "has access to this site",
    "energy saver is on",
    "performance issue alert",
    "text to speech",
    "tts",
    "minimize",
    "maximize",
    "close",
    "reload",
    "back",
    "forward",
    "new tab",
    "extensions",
    "bookmarks",
    "downloads",
    "chrome",
    "google chrome",
    "address and search bar",
}

_CHROME_PATTERNS = (
    re.compile(r"^zoom:\s*\d+%", re.I),
    re.compile(r"^install\s+\w+", re.I),
    re.compile(r"\bfree vpn\b", re.I),
    re.compile(r"\benergy saver\b", re.I),
    re.compile(r"\bside panel\b", re.I),
    re.compile(r"\bmanaged bookmarks?\b", re.I),
    re.compile(r"\bbookmark this tab\b", re.I),
    re.compile(r"^https?://", re.I),
    re.compile(r"\bchrome://", re.I),
    re.compile(r"\bwants access to this site\b", re.I),
    re.compile(r"\bhas access to this site\b", re.I),
    re.compile(r"\bsplit view\b", re.I),
    re.compile(r"\bview site information\b", re.I),
    re.compile(r"^text to speech\b", re.I),
)

_EXTENSION_PATTERNS = (
    re.compile(r"^install\s+", re.I),
    re.compile(r"\bfor chrome\b", re.I),
    re.compile(r"\bextension\b", re.I),
)

_NAV_WORDS = frozenset(
    {
        "home",
        "back",
        "forward",
        "reload",
        "menu",
        "search",
        "settings",
        "help",
        "close",
        "cancel",
        "ok",
        "apply",
        "next",
        "previous",
        "login",
        "sign in",
        "share",
        "print",
        "save",
    }
)


@dataclass
class PageNode:
    text: str
    source_scope: str = UNKNOWN
    source_method: str = ""
    section: str = ""
    confidence: float = 0.0
    accessibility_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PageModel:
    browser_hwnd: int = 0
    tab_ref: str = ""
    document_url: str = ""
    document_origin: str = ""
    page_type: str = GENERIC
    page_type_confidence: float = 0.0
    main_content: str = ""
    nodes: list[PageNode] = field(default_factory=list)
    chrome_excluded: bool = True
    extension_excluded: bool = True
    content_contamination_score: float = 0.0
    structured_jobposting_found: bool = False
    document_source_method: str = ""
    form_fields: list[str] = field(default_factory=list)
    rejected_noise_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "browser_hwnd": self.browser_hwnd,
            "tab_ref": self.tab_ref,
            "document_url": self.document_url,
            "document_origin": self.document_origin,
            "page_type": self.page_type,
            "page_type_confidence": self.page_type_confidence,
            "main_content": self.main_content,
            "nodes": [n.as_dict() for n in self.nodes],
            "chrome_excluded": self.chrome_excluded,
            "extension_excluded": self.extension_excluded,
            "content_contamination_score": self.content_contamination_score,
            "structured_jobposting_found": self.structured_jobposting_found,
            "document_source_method": self.document_source_method,
            "form_fields": list(self.form_fields),
            "rejected_noise_count": self.rejected_noise_count,
        }


def document_origin(url: str) -> str:
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}" if host else ""


def classify_text_scope(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return UNKNOWN
    low = t.lower()
    if low in _CHROME_LABELS or any(p.search(t) for p in _CHROME_PATTERNS):
        if any(p.search(t) for p in _EXTENSION_PATTERNS):
            return BROWSER_EXTENSION
        return BROWSER_CHROME
    if any(p.search(t) for p in _EXTENSION_PATTERNS):
        return BROWSER_EXTENSION
    if low in _NAV_WORDS and len(t.split()) <= 2:
        return BROWSER_CHROME
    return WEB_DOCUMENT


def is_browser_noise(text: str) -> bool:
    return classify_text_scope(text) in {BROWSER_CHROME, BROWSER_EXTENSION, OS_UI}


def contamination_score(texts: list[str]) -> tuple[float, int]:
    if not texts:
        return 0.0, 0
    rejected = 0
    chrome = 0
    for raw in texts:
        scope = classify_text_scope(raw)
        if scope in {BROWSER_CHROME, BROWSER_EXTENSION, OS_UI}:
            rejected += 1
            chrome += 1
        elif len((raw or "").split()) <= 2 and (raw or "").strip().lower() in _NAV_WORDS:
            rejected += 1
            chrome += 1
    score = chrome / max(1, len(texts))
    return min(1.0, score), rejected


def confirm_page_type(
    *,
    url: str = "",
    title: str = "",
    document_text: str = "",
    structured: bool = False,
    form_fields: list[str] | None = None,
    has_qualifications: bool = False,
    has_job_id: bool = False,
    has_apply: bool = False,
) -> tuple[str, float]:
    """Do not classify from URL or title alone."""
    fields = list(form_fields or [])
    text = f"{document_text or ''}"
    evidence = 0
    if structured:
        evidence += 2
    if has_qualifications:
        evidence += 2
    if has_job_id:
        evidence += 1
    if re.search(r"\b(job description|qualifications|required:|responsibilities)\b", text, re.I):
        evidence += 2
    if re.search(r"\b(apply now|submit application|requisition)\b", text, re.I):
        evidence += 1
    if has_apply:
        evidence += 1
    upload = any(re.search(r"upload|resume|cv|attach", f, re.I) for f in fields)
    required_flags = any(re.search(r"required|mandatory", f, re.I) for f in fields)
    if fields and (upload or required_flags or len(fields) >= 4):
        if evidence < 3:
            return JOB_APPLICATION, min(0.9, 0.45 + 0.1 * len(fields[:5]))
    if evidence >= 3:
        return JOB_LISTING, min(0.95, 0.4 + 0.1 * evidence)
    if evidence:
        return GENERIC, 0.25
    return GENERIC, 0.1
