"""Read the active tab's WEB DOCUMENT, never the Chrome chrome tree."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from src.agent.browser.page_model import (
    GENERIC,
    WEB_DOCUMENT,
    PageModel,
    PageNode,
    classify_text_scope,
    confirm_page_type,
    contamination_score,
    document_origin,
    is_browser_noise,
)

_LD_JSON = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
_JOB_ID = re.compile(r"\b(?:jobId|job[_-]?id|requisition|req)[=:/\s-]*([A-Za-z0-9-]{3,})\b", re.I)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "head"}:
            self._skip += 1
        if tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div", "section"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "head"} and self._skip:
            self._skip -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = (data or "").strip()
        if text:
            self.parts.append(text)


def html_to_text(html: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(html or "")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html or "")
        return re.sub(r"\s+", " ", text).strip()
    raw = " ".join(parser.parts)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s+", "\n", raw)
    return raw.strip()


def parse_json_ld_jobpostings(html: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for block in _LD_JSON.findall(html or ""):
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type")
            labels = types if isinstance(types, list) else [types]
            if any(str(t) == "JobPosting" for t in labels):
                found.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                for node in graph:
                    if isinstance(node, dict) and "JobPosting" in str(node.get("@type")):
                        found.append(node)
    return found


def fetch_document_html(url: str, timeout_s: float = 12.0) -> tuple[str, str]:
    raw = (url or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return "", ""
    host = (parsed.hostname or "").lower()
    if host.endswith("example.com") or host in {"localhost", "127.0.0.1"}:
        return "", raw
    try:
        response = httpx.get(
            raw,
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0"},
        )
        if response.status_code >= 400:
            return "", str(response.url)
        return response.text or "", str(response.url)
    except Exception as e:
        print(f"[PageModel] document fetch failed: {e}")
        return "", raw


def collect_document_uia_text(hwnd: int, max_chars: int = 8000) -> str:
    """Accessibility text from the renderer document subtree only."""
    if not hwnd:
        return ""
    try:
        from src.agent.screen.uia import collect_document_text

        return collect_document_text(int(hwnd), max_chars=max_chars)
    except Exception as e:
        print(f"[PageModel] document UIA failed: {e}")
        return ""


def _best_jobposting(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {}
    def _score(item: dict[str, Any]) -> int:
        score = 0
        for key in ("qualifications", "skills", "description", "responsibilities", "title"):
            if item.get(key):
                score += len(str(item.get(key)))
        return score
    return max(items, key=_score)


def build_page_model(
    *,
    url: str = "",
    title: str = "",
    hwnd: int = 0,
    max_chars: int = 16000,
) -> PageModel:
    html, final_url = fetch_document_html(url)
    job_ld = parse_json_ld_jobpostings(html) if html else []
    posting = _best_jobposting(job_ld)
    document_text = ""
    method = ""
    if posting:
        chunks = [
            str(posting.get("title") or ""),
            str(posting.get("qualifications") or ""),
            str(posting.get("description") or ""),
            str(posting.get("responsibilities") or ""),
            str(posting.get("skills") or ""),
            str(posting.get("experienceRequirements") or ""),
            str(posting.get("educationRequirements") or ""),
        ]
        document_text = html_to_text("\n".join(c for c in chunks if c))
        method = "json_ld"
    if html and len(document_text) < 400:
        body = html_to_text(html)
        if len(body) > len(document_text):
            document_text = body
            method = method or "dom_html"
    uia_text = collect_document_uia_text(hwnd, max_chars=max_chars)
    if uia_text:
        uia_clean = "\n".join(
            line for line in uia_text.splitlines() if line.strip() and not is_browser_noise(line)
        )
        if not document_text:
            document_text = uia_clean
            method = "document_uia"
        elif len(uia_clean) > 80:
            # Keep JSON-LD/HTML as primary; UIA may add visible section text.
            method = method or "document_uia"
    document_text = document_text[: max(200, int(max_chars))]
    lines = [ln.strip() for ln in document_text.splitlines() if ln.strip()]
    kept: list[PageNode] = []
    rejected = 0
    for line in lines:
        scope = classify_text_scope(line)
        if scope != WEB_DOCUMENT:
            rejected += 1
            continue
        kept.append(
            PageNode(
                text=line,
                source_scope=WEB_DOCUMENT,
                source_method=method or "page_reader",
                confidence=0.9 if method == "json_ld" else 0.7,
            )
        )
    clean = "\n".join(n.text for n in kept)
    score, extra_rejected = contamination_score(lines)
    rejected += extra_rejected
    job_id = ""
    ident = posting.get("identifier") if posting else None
    if isinstance(ident, dict):
        job_id = str(ident.get("value") or ident.get("name") or "")
    elif ident:
        job_id = str(ident)
    if not job_id:
        m = _JOB_ID.search(final_url or url or "")
        job_id = m.group(1) if m else ""
    has_qual = bool(
        posting.get("qualifications")
        or re.search(r"\b(qualifications|required:|requirements)\b", clean, re.I)
    )
    page_type, confidence = confirm_page_type(
        url=final_url or url,
        title=title,
        document_text=clean,
        structured=bool(posting),
        has_qualifications=has_qual,
        has_job_id=bool(job_id),
        has_apply=bool(re.search(r"\bapply\b", clean, re.I)),
        form_fields=_form_hints(clean),
    )
    if score >= 0.35 and method != "json_ld":
        page_type = GENERIC
        confidence = min(confidence, 0.2)
    model = PageModel(
        browser_hwnd=int(hwnd or 0),
        tab_ref=title or "",
        document_url=final_url or url,
        document_origin=document_origin(final_url or url),
        page_type=page_type,
        page_type_confidence=confidence,
        main_content=clean,
        nodes=kept,
        chrome_excluded=True,
        extension_excluded=True,
        content_contamination_score=score if method != "json_ld" else 0.0,
        structured_jobposting_found=bool(posting),
        document_source_method=method or "none",
        form_fields=_form_hints(clean) if page_type != GENERIC or _form_hints(clean) else [],
        rejected_noise_count=rejected,
    )
    print(
        f"[PageModel] browser_hwnd={model.browser_hwnd or '-'} tab_ref={model.tab_ref!r} "
        f"document_url={model.document_url or '-'} page_type={model.page_type} "
        f"page_type_confidence={model.page_type_confidence:.2f} "
        f"document_source_method={model.document_source_method} "
        f"structured_jobposting_found={str(model.structured_jobposting_found).lower()} "
        f"web_document={len(kept)} chrome_nodes_excluded={rejected} "
        f"extension_excluded=true content_contamination_score={model.content_contamination_score:.2f}"
    )
    model._jobposting = posting  # type: ignore[attr-defined]
    return model


def _form_hints(text: str) -> list[str]:
    hints = []
    for line in (text or "").splitlines():
        if re.search(r"\b(required|upload|attach|first name|last name|email|phone|resume)\b", line, re.I):
            if 3 <= len(line) <= 80:
                hints.append(line.strip())
    return hints[:20]


def document_text_for_speech(model: PageModel) -> str:
    lines = [n.text for n in model.nodes if n.source_scope == WEB_DOCUMENT][:8]
    return " ".join(lines)[:400]
