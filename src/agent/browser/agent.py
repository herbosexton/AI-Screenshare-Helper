"""BrowserAgent — application-level browser control. Playwright stays behind the driver."""

from __future__ import annotations

import hashlib
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from src.agent.browser.driver import PlaywrightBrowserDriver
from src.agent.browser.errors import (
    AMBIGUOUS_ELEMENT,
    AUTHENTICATION_REQUIRED,
    CAPTCHA_REQUIRED,
    DIALOG_BLOCKING,
    ELEMENT_NOT_FOUND,
    ELEMENT_NOT_INTERACTABLE,
    PAGE_LOAD_TIMEOUT,
    SESSION_DISCONNECTED,
    TAB_NOT_FOUND,
    UNSUPPORTED_BROWSER,
    UPLOAD_FAILED,
    URL_BLOCKED,
    VISUAL_FALLBACK_REQUIRED,
    BrowserError,
    BrowserUnavailable,
)
from src.agent.browser.fallback import VisualFallback
from src.agent.browser.files_io import BrowserFileIO
from src.agent.browser.models import (
    BrowserActionResult,
    BrowserElement,
    BrowserForm,
    BrowserPageState,
    BrowserTab,
    FormField,
)
from src.agent.browser.policy import UrlPolicy, UrlPolicyError
from src.agent.browser.resolver import pick_element, resolve_candidates, score_tab
from src.agent.files.permissions import FilePathPolicy

# Playwright's wording when the page, context, or browser died under us. A liveness check
# can pass and the very next call still land on a target the user just closed.
_DEAD_TARGET = re.compile(
    r"has been closed|target closed|target page.*closed|browser closed|connection closed",
    re.I,
)

EXTRACT_JS = """() => {
  const vis = (el) => {
    if (!el) return false;
    const s = window.getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.visibility !== 'hidden' && s.display !== 'none' && r.width + r.height > 0;
  };
  const labelFor = (el) => {
    if (el.labels && el.labels[0]) return el.labels[0].innerText.trim();
    const id = el.id ? document.querySelector('label[for="'+el.id+'"]') : null;
    if (id) return id.innerText.trim();
    return (el.getAttribute('aria-label') || el.placeholder || el.name || '').trim();
  };
  const items = [];
  const sel = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [role="checkbox"], [role="radio"], [role="dialog"], dialog';
  document.querySelectorAll(sel).forEach((el, i) => {
    const r = el.getBoundingClientRect();
    items.push({
      idx: i,
      tag: (el.tagName || '').toLowerCase(),
      role: el.getAttribute('role') || (el.tagName || '').toLowerCase(),
      name: (el.innerText || labelFor(el) || '').trim().slice(0, 120),
      text: (el.innerText || '').trim().slice(0, 120),
      type: el.type || '',
      placeholder: el.placeholder || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      value: (el.value || '').toString().slice(0, 200),
      href: el.href || '',
      checked: el.checked === undefined ? null : !!el.checked,
      disabled: !!el.disabled,
      visible: vis(el),
      editable: !el.disabled && ['input','textarea','select'].includes((el.tagName||'').toLowerCase()),
      required: !!el.required,
      selector: el.id ? ('#' + CSS.escape(el.id)) : '',
      boundingBox: { x: r.x, y: r.y, width: r.width, height: r.height }
    });
  });
  const forms = Array.from(document.forms).map((f, fi) => ({
    name: f.getAttribute('name') || f.getAttribute('aria-label') || f.id || ('form-' + fi),
    selector: f.id ? ('#' + CSS.escape(f.id)) : '',
    fields: Array.from(f.elements).filter(el => el.name || el.id).map(el => ({
      label: labelFor(el).slice(0, 80),
      name: el.name || '',
      type: el.type || (el.tagName || '').toLowerCase(),
      required: !!el.required,
      value: (el.type === 'password') ? '' : (el.value || '').toString().slice(0, 80),
      selector: el.id ? ('#' + CSS.escape(el.id)) : (el.name ? ('[name="' + el.name + '"]') : ''),
      options: (el.tagName || '').toLowerCase() === 'select'
        ? Array.from(el.options).map(o => (o.text || o.value || '').trim()).filter(Boolean)
        : []
    })),
    submitButtons: Array.from(f.querySelectorAll('button, input[type=submit]')).map(b => (b.innerText || b.value || '').trim())
  }));
  const html = (document.body && document.body.innerText || '').toLowerCase();
  const captcha = !!(document.querySelector('iframe[src*="recaptcha"], iframe[src*="hcaptcha"], .g-recaptcha, #cf-challenge, [data-hcaptcha-widget-id]')
    || /verify you are human|i'm not a robot|attention required/.test(html));
  const h1 = ((document.querySelector('h1') || {}).innerText || '') + ' ' + (document.title || '');
  const auth = !!(document.querySelector('input[type=password]')
    && /sign in|log in|sign-in|authenticate/.test((h1 + ' ' + html).toLowerCase())
    && !document.querySelector('input[type=file]'));
  const dialogs = Array.from(document.querySelectorAll('dialog[open], [role=dialog], [role=alertdialog]'))
    .map(d => (d.innerText || '').trim().slice(0, 160)).filter(Boolean);
  return {
    items, forms, captcha, auth, dialogs,
    title: document.title || '',
    headings: Array.from(document.querySelectorAll('h1,h2')).map(h => h.innerText.trim()).slice(0, 8),
    scrollX: window.scrollX, scrollY: window.scrollY,
    vw: window.innerWidth, vh: window.innerHeight
  };
}"""


def _ok(action: str, **kwargs: Any) -> BrowserActionResult:
    return BrowserActionResult(success=True, action=action, **kwargs)


def _fail(action: str, err: BrowserError) -> BrowserActionResult:
    rec = None
    if err.code in {CAPTCHA_REQUIRED, AUTHENTICATION_REQUIRED}:
        rec = "WAIT_FOR_USER"
    elif err.code == "VISUAL_FALLBACK_REQUIRED":
        rec = "VISUAL_FALLBACK"
    return BrowserActionResult(
        success=False,
        action=action,
        error=err.as_dict(),
        retryable=err.retryable,
        recommended_next_state=rec,
        data=err.details,
    )


class BrowserAgent:
    def __init__(
        self,
        *,
        policy: UrlPolicy,
        profile_dir: Path,
        headed: bool = True,
        persist_profile: bool = True,
        navigation_timeout_ms: int = 20000,
        channel: str = "",
        file_policy: Optional[FilePathPolicy] = None,
        computer=None,
        screen=None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        self.policy = policy
        self.on_status = on_status or (lambda m: print(f"[Browser] {m}"))
        downloads = Path(profile_dir).parent / "browser_downloads"
        if file_policy is not None:
            try:
                import os

                preferred = Path(os.path.expandvars("%USERPROFILE%")) / "Downloads" / "JarvisBrowser"
                file_policy.check(str(preferred), write=True, must_exist=False)
                downloads = preferred
            except Exception:
                pass
        self.driver = PlaywrightBrowserDriver(
            profile_dir=profile_dir,
            headed=headed,
            persist_profile=persist_profile,
            navigation_timeout_ms=navigation_timeout_ms,
            channel=channel,
            downloads_dir=downloads,
        )
        self.files = BrowserFileIO(file_policy, downloads)
        self.fallback = VisualFallback(computer, screen, self.on_status)
        self.on_event: Optional[Callable[[str, dict[str, Any]], None]] = None
        self._lock = threading.RLock()
        self._page = None
        self._tab_meta: dict[int, dict[str, Any]] = {}
        self._cache: Optional[BrowserPageState] = None
        self._history: list[str] = []
        self._pending_dialog: Optional[str] = None
        self._session_id = ""
        self._last_navigation = ""
        self._disconnected = False
        self._js_dialog_mode = "dismiss"
        self._last_dialog_type = ""

    def _fingerprint(self, url: str, title: str) -> str:
        return hashlib.sha256(f"{url}|{title}".encode("utf-8", errors="replace")).hexdigest()[:16]

    def _emit(self, name: str, payload: Optional[dict[str, Any]] = None) -> None:
        data = payload if payload is not None else self.get_session_state()
        if self.on_event is None:
            return
        try:
            self.on_event(name, data)
        except Exception as e:
            print(f"[Browser] event {name} listener error: {e}")

    # --- lifecycle ---

    def _import_playwright(self):
        return self.driver._import_playwright()

    def open(self) -> dict[str, Any]:
        self._disconnected = False
        self.on_status("Opening browser…")
        self.driver.launch()
        pages = self.driver.pages()
        self._page = pages[0] if pages else self.driver.new_page()
        self._bind_page(self._page)
        self._session_id = uuid4().hex[:12]
        result = _ok(
            "open",
            url=self._page.url,
            title=self._safe_title(),
            data={"browserType": self.driver.browser_type, "headed": self.driver.headed},
        ).as_dict()
        self._emit("browserOpened")
        return result

    def close(self) -> dict[str, Any]:
        self.on_status("Closing browser…")
        with self._lock:
            self._page = None
            self._cache = None
            self._session_id = ""
            self._last_navigation = ""
            self._disconnected = False
            self.driver.close()
        result = _ok("close", data={"closed": True}).as_dict()
        self._emit("browserClosed", {"open": False, "connectionStatus": "closed"})
        return result

    def _mark_disconnected(self) -> None:
        self._page = None
        self._cache = None
        self._disconnected = True
        try:
            self.driver.status = "disconnected"
            self.driver._context = None
            self.driver._browser = None
        except Exception:
            pass
        self._emit("browserClosed", {"open": False, "connectionStatus": "disconnected"})

    def _ensure(self, *, relaunch: bool = False):
        """Return a live page.

        `relaunch` belongs to commands that establish where the browser is, navigation
        above all: a user closing the window is ordinary, and the next "go to ..." should
        open one and carry on rather than failing until something calls browser.open.
        Commands that act on what is already on screen never relaunch, because a fresh
        blank window cannot hold the element they were sent to find.
        """
        if self._disconnected:
            return self._no_live_page(relaunch)
        if self._page is None:
            self.open()
            return self._page
        try:
            if bool(getattr(self._page, "is_closed", lambda: False)()):
                self._mark_disconnected()
                return self._no_live_page(relaunch)
            _ = self._page.url
        except Exception:
            self._mark_disconnected()
            return self._no_live_page(relaunch)
        return self._page

    def _no_live_page(self, relaunch: bool):
        if relaunch:
            try:
                self.open()
            except Exception as e:
                self._mark_disconnected()
                raise BrowserError(
                    "Browser session disconnected", SESSION_DISCONNECTED, retryable=False
                ) from e
            if self._page is not None:
                return self._page
        raise BrowserError(
            "Browser session disconnected", SESSION_DISCONNECTED, retryable=False
        )

    def _bind_page(self, page) -> None:
        try:
            page.on("dialog", self._on_js_dialog)
            page.on("download", self._on_download)
            page.on("popup", self._on_popup)
            page.on("close", lambda: None)
        except Exception:
            pass

    def _on_popup(self, page) -> None:
        try:
            self._bind_page(page)
            self._page = page
            self._invalidate()
            self._emit("tabOpened")
        except Exception:
            pass

    def _on_js_dialog(self, dialog) -> None:
        msg = ""
        dtype = ""
        try:
            msg = dialog.message or ""
            dtype = dialog.type or ""
        except Exception:
            pass
        self._last_dialog_type = dtype
        self._pending_dialog = f"{dtype}: {msg}".strip(": ")[:200]
        try:
            if self._js_dialog_mode == "accept" and dtype in {"alert", "confirm"}:
                dialog.accept()
            elif dtype == "alert":
                dialog.accept()
            else:
                dialog.dismiss()
        except Exception:
            pass
        self._js_dialog_mode = "dismiss"

    def _on_download(self, download) -> None:
        try:
            name = download.suggested_filename or "download"
            dest = self.files.downloads_dir / name
            download.save_as(str(dest))
            size = dest.stat().st_size if dest.exists() else 0
            self.files.record_download(
                filename=name,
                source_url=getattr(download, "url", "") or "",
                dest=dest,
                size=size,
                status="completed",
            )
            self.on_status(f"Download saved: {name}")
        except Exception as e:
            self.files.record_download(
                filename="unknown",
                source_url="",
                dest=self.files.downloads_dir / "failed",
                size=0,
                status=f"failed:{e}",
            )

    def _safe_title(self) -> str:
        try:
            return self._page.title() if self._page else ""
        except Exception:
            return ""

    def _safe_url(self) -> str:
        try:
            return self._page.url if self._page else ""
        except Exception:
            return ""

    def _invalidate(self) -> None:
        self._cache = None

    def status(self) -> dict[str, Any]:
        if self._page is None:
            return {"open": False, "url": None, "title": None, "tabs": 0, "browserType": self.driver.browser_type}
        tabs = self.list_tabs()
        return {
            "open": True,
            "url": self._safe_url(),
            "title": self._safe_title(),
            "tabs": len(tabs),
            "browserType": self.driver.browser_type,
        }

    def get_session_state(self) -> dict[str, Any]:
        """Cheap live state. No DOM snapshot, no UIA, no screenshot."""
        if self._page is None:
            return {
                "open": False,
                "sessionId": self._session_id,
                "url": "",
                "title": "",
                "tabs": 0,
                "browserType": self.driver.browser_type,
                "connectionStatus": "disconnected" if self._disconnected else "closed",
                "activeTabId": "",
                "lastNavigation": self._last_navigation,
                "pageFingerprint": "",
            }
        try:
            if bool(getattr(self._page, "is_closed", lambda: False)()):
                self._mark_disconnected()
                return {
                    "open": False,
                    "sessionId": self._session_id,
                    "url": "",
                    "title": "",
                    "tabs": 0,
                    "browserType": self.driver.browser_type,
                    "connectionStatus": "disconnected",
                    "activeTabId": "",
                    "lastNavigation": self._last_navigation,
                    "pageFingerprint": "",
                }
        except Exception:
            self._mark_disconnected()
            return {
                "open": False,
                "sessionId": self._session_id,
                "url": "",
                "title": "",
                "tabs": 0,
                "browserType": self.driver.browser_type,
                "connectionStatus": "disconnected",
                "activeTabId": "",
                "lastNavigation": self._last_navigation,
                "pageFingerprint": "",
            }
        url, title = self._safe_url(), self._safe_title()
        try:
            ntabs = len(self.driver.pages())
        except Exception:
            ntabs = 1
        return {
            "open": True,
            "sessionId": self._session_id,
            "url": url,
            "title": title,
            "tabs": ntabs,
            "browserType": self.driver.browser_type,
            "connectionStatus": "active",
            "activeTabId": self._tab_id(self._page),
            "lastNavigation": self._last_navigation,
            "pageFingerprint": self._fingerprint(url, title),
        }

    # --- navigation ---

    def navigate(self, url: str) -> dict[str, Any]:
        try:
            allowed = self.policy.check(url)
        except UrlPolicyError as e:
            return _fail("navigate", BrowserError(str(e), URL_BLOCKED, retryable=False)).as_dict()
        try:
            page = self._ensure(relaunch=True)
        except BrowserUnavailable as e:
            return _fail(
                "navigate",
                BrowserError(str(e), getattr(e, "code", None) or UNSUPPORTED_BROWSER, retryable=False),
            ).as_dict()
        except BrowserError as e:
            return _fail("navigate", e).as_dict()
        prev = self._safe_url()
        self.on_status(f"Navigating to {allowed}…")
        try:
            self.driver.goto(page, allowed)
        except BrowserError as e:
            if not _DEAD_TARGET.search(str(e)):
                return _fail("navigate", e).as_dict()
            # The window went away between the liveness check and the navigation itself.
            self._mark_disconnected()
            try:
                page = self._ensure(relaunch=True)
                self.driver.goto(page, allowed)
            except BrowserError as reopened:
                return _fail("navigate", reopened).as_dict()
        self._history.append(allowed)
        self._invalidate()
        cur = self._safe_url()
        title = self._safe_title()
        pause = self._interrupt_check()
        if pause:
            return pause
        verified = allowed.rstrip("/") in cur or cur.startswith(allowed)
        self._last_navigation = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = _ok(
            "navigate",
            url=cur,
            previous_url=prev,
            title=title,
            state_changed=cur != prev,
            verified=verified,
        ).as_dict()
        self._emit("navigationCompleted")
        return result

    def goto(self, url: str) -> dict[str, Any]:
        return self.navigate(url)

    def back(self) -> dict[str, Any]:
        page = self._ensure()
        prev = self._safe_url()
        page.go_back(wait_until="domcontentloaded")
        self._invalidate()
        self._last_navigation = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = _ok("back", url=self._safe_url(), previous_url=prev, title=self._safe_title(), state_changed=True, verified=True).as_dict()
        self._emit("navigationCompleted")
        return result

    def forward(self) -> dict[str, Any]:
        page = self._ensure()
        prev = self._safe_url()
        page.go_forward(wait_until="domcontentloaded")
        self._invalidate()
        self._last_navigation = time.strftime("%Y-%m-%dT%H:%M:%S")
        result = _ok("forward", url=self._safe_url(), previous_url=prev, title=self._safe_title(), state_changed=True, verified=True).as_dict()
        self._emit("navigationCompleted")
        return result

    def reload(self) -> dict[str, Any]:
        page = self._ensure()
        page.reload(wait_until="domcontentloaded")
        self._invalidate()
        return _ok("reload", url=self._safe_url(), title=self._safe_title(), verified=True).as_dict()

    # --- tabs ---

    def _tab_id(self, page) -> str:
        return f"tab-{id(page)}"

    def list_tabs(self) -> list[dict[str, Any]]:
        if self._page is None:
            return []
        out = []
        for page in self.driver.pages():
            try:
                title, url = page.title(), page.url
            except Exception:
                title, url = "", ""
            out.append(
                BrowserTab(
                    id=self._tab_id(page),
                    title=title,
                    url=url,
                    active=page is self._page,
                ).model_dump(mode="json")
            )
        return out

    def get_active_tab(self) -> dict[str, Any]:
        tabs = self.list_tabs()
        for t in tabs:
            if t.get("active"):
                return t
        return {"id": "", "title": "", "url": ""}

    def new_tab(self, url: str = "") -> dict[str, Any]:
        self._ensure(relaunch=True)
        page = self.driver.new_page()
        self._bind_page(page)
        self._page = page
        self._invalidate()
        if url:
            nav = self.navigate(url)
            nav["tabs"] = len(self.driver.pages())
            self._emit("tabOpened")
            return nav
        result = _ok("newTab", url=page.url, data={"tabs": len(self.driver.pages())}).as_dict()
        self._emit("tabOpened")
        return result

    def close_tab(self) -> dict[str, Any]:
        if self._page is None:
            return _ok("closeTab", data={"closed": False}).as_dict()
        pages = self.driver.pages()
        if len(pages) <= 1:
            return self.close()
        current = self._page
        current.close()
        remaining = self.driver.pages()
        self._page = remaining[-1] if remaining else None
        self._invalidate()
        result = _ok("closeTab", data={"closed": True, "tabs": len(remaining)}).as_dict()
        self._emit("tabClosed")
        return result

    def switch_tab(self, tab_id: str = "", query: str = "") -> dict[str, Any]:
        self._ensure()
        pages = self.driver.pages()
        target = None
        if tab_id:
            for page in pages:
                if self._tab_id(page) == tab_id:
                    target = page
                    break
        elif query:
            ranked = []
            for page in pages:
                try:
                    s = score_tab(page.title(), page.url, query)
                except Exception:
                    s = 0
                ranked.append((s, page))
            ranked.sort(key=lambda x: x[0], reverse=True)
            if ranked and ranked[0][0] >= 0.4:
                target = ranked[0][1]
        if target is None:
            return _fail("switchTab", BrowserError("Tab not found", TAB_NOT_FOUND, retryable=False)).as_dict()
        self._page = target
        try:
            target.bring_to_front()
        except Exception:
            pass
        self._invalidate()
        result = _ok("switchTab", url=self._safe_url(), title=self._safe_title(), verified=True).as_dict()
        self._emit("tabChanged")
        return result

    def find_tab(self, query: str) -> dict[str, Any]:
        tabs = self.list_tabs()
        ranked = []
        for t in tabs:
            s = score_tab(t.get("title") or "", t.get("url") or "", query)
            if s > 0:
                ranked.append({**t, "confidence": round(s, 2)})
        ranked.sort(key=lambda x: x["confidence"], reverse=True)
        return {
            "success": True,
            "action": "findTab",
            "query": query,
            "matches": ranked,
            "untrusted": True,
        }

    def get_current_url(self) -> dict[str, Any]:
        self._ensure()
        return _ok("getCurrentUrl", url=self._safe_url(), title=self._safe_title()).as_dict()

    def get_title(self) -> dict[str, Any]:
        self._ensure()
        return _ok("getTitle", url=self._safe_url(), title=self._safe_title()).as_dict()

    # --- page reading ---

    def _raw_extract(self) -> dict[str, Any]:
        page = self._ensure()
        try:
            combined = page.evaluate(EXTRACT_JS) or {}
        except Exception:
            combined = {}
        items = list(combined.get("items") or [])
        frames_meta = []
        try:
            frame_list = list(page.frames)
        except Exception:
            frame_list = []
        for frame in frame_list:
            try:
                frames_meta.append({"url": frame.url, "name": frame.name})
                if frame == page.main_frame:
                    for it in items:
                        it.setdefault("frameUrl", frame.url)
                    continue
                raw = frame.evaluate(EXTRACT_JS) or {}
                for it in raw.get("items") or []:
                    it["frameUrl"] = frame.url
                    items.append(it)
                for form in raw.get("forms") or []:
                    combined.setdefault("forms", []).append(form)
            except Exception:
                continue
        combined["items"] = items
        combined["frames"] = frames_meta
        return combined

    def _elements_from_raw(self, raw: dict[str, Any]) -> list[BrowserElement]:
        els = []
        for i, item in enumerate(raw.get("items") or []):
            eid = f"e{i + 1}"
            els.append(
                BrowserElement(
                    id=eid,
                    role=item.get("role") or "generic",
                    name=item.get("name") or "",
                    text=item.get("text") or "",
                    tag=item.get("tag") or "",
                    type=item.get("type") or "",
                    placeholder=item.get("placeholder") or "",
                    aria_label=item.get("ariaLabel") or "",
                    value="" if item.get("type") == "password" else (item.get("value") or ""),
                    href=item.get("href") or "",
                    checked=item.get("checked"),
                    disabled=bool(item.get("disabled")),
                    visible=bool(item.get("visible", True)),
                    editable=bool(item.get("editable")),
                    required=bool(item.get("required")),
                    selector=item.get("selector") or "",
                    frame_url=item.get("frameUrl") or "",
                    bounding_box=item.get("boundingBox") or {},
                )
            )
        return els

    def get_page_state(self) -> dict[str, Any]:
        raw = self._raw_extract()
        els = self._elements_from_raw(raw)
        forms = []
        for f in raw.get("forms") or []:
            forms.append(
                BrowserForm(
                    name=f.get("name") or "",
                    selector=f.get("selector") or "",
                    fields=[
                        FormField(
                            label=fld.get("label") or "",
                            name=fld.get("name") or "",
                            type=fld.get("type") or "text",
                            required=bool(fld.get("required")),
                            value="" if (fld.get("type") == "password") else (fld.get("value") or ""),
                            element_id=fld.get("selector") or "",
                            options=list(fld.get("options") or []),
                        )
                        for fld in f.get("fields") or []
                    ],
                    submit_buttons=f.get("submitButtons") or [],
                )
            )
        headings = raw.get("headings") or []
        url, title = self._safe_url(), self._safe_title() or raw.get("title") or ""
        fp_src = f"{url}|{title}|{'/'.join(headings)}"
        state = BrowserPageState(
            tab_id=self._tab_id(self._page) if self._page else "",
            url=url,
            title=title,
            load_state="complete",
            text_summary=" | ".join(headings)[:400],
            forms=forms,
            links=[e for e in els if e.tag == "a" or e.role == "link"],
            buttons=[e for e in els if e.tag == "button" or e.role == "button" or e.type == "submit"],
            inputs=[e for e in els if e.tag in {"input", "textarea", "select"} or e.editable],
            dialogs=raw.get("dialogs") or [],
            captcha_likely=bool(raw.get("captcha")),
            auth_likely=bool(raw.get("auth")),
            fingerprint=hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16],
            viewport={"width": int(raw.get("vw") or 0), "height": int(raw.get("vh") or 0)},
            scroll_position={"x": int(raw.get("scrollX") or 0), "y": int(raw.get("scrollY") or 0)},
        )
        self._cache = state
        payload = state.model_dump(mode="json")
        payload["untrusted"] = True
        payload["success"] = True
        payload["action"] = "getPageState"
        payload["frames"] = raw.get("frames") or []
        return payload

    def snapshot(self, limit: int = 80) -> dict[str, Any]:
        raw = self._raw_extract()
        els = self._elements_from_raw(raw)[:limit]
        nodes = []
        for e in els:
            d = e.model_dump(mode="json")
            d["ref"] = e.id
            nodes.append(d)
        return {
            "url": self._safe_url(),
            "title": self._safe_title(),
            "nodes": nodes,
            "count": len(nodes),
            "untrusted": True,
        }

    def get_text(self, max_chars: int = 8000) -> dict[str, Any]:
        page = self._ensure()
        try:
            text = page.inner_text("body")
        except Exception:
            text = ""
        text = (text or "").strip()
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars] + "\n[truncated]"
        return {
            "url": self._safe_url(),
            "title": self._safe_title(),
            "text": text,
            "truncated": truncated,
            "untrusted": True,
        }

    def get_visible_text(self, max_chars: int = 8000) -> dict[str, Any]:
        return self.get_text(max_chars=max_chars)

    def get_links(self) -> dict[str, Any]:
        state = self.get_page_state()
        return {"success": True, "action": "getLinks", "links": state.get("links"), "untrusted": True}

    def get_forms(self) -> dict[str, Any]:
        state = self.get_page_state()
        return {"success": True, "action": "getForms", "forms": state.get("forms"), "untrusted": True}

    def get_buttons(self) -> dict[str, Any]:
        state = self.get_page_state()
        return {"success": True, "action": "getButtons", "buttons": state.get("buttons"), "untrusted": True}

    def get_inputs(self) -> dict[str, Any]:
        state = self.get_page_state()
        return {"success": True, "action": "getInputs", "inputs": state.get("inputs"), "untrusted": True}

    def screenshot(self) -> dict[str, Any]:
        import base64

        page = self._ensure()
        raw = page.screenshot(type="png", full_page=False)
        path = self.driver.profile_dir.parent / "browser_last.png"
        path.write_bytes(raw)
        return {"path": str(path), "base64_png": base64.b64encode(raw).decode("ascii")}

    # --- interrupts ---

    def _interrupt_check(self) -> Optional[dict[str, Any]]:
        try:
            raw = self._raw_extract()
        except BrowserError as e:
            if e.code in {SESSION_DISCONNECTED, "BROWSER_NOT_RUNNING"}:
                return _fail("session", e).as_dict()
            raise
        if raw.get("captcha"):
            self.on_status("CAPTCHA detected — waiting for you.")
            return _fail(
                "captcha",
                BrowserError("Human verification required", CAPTCHA_REQUIRED, retryable=False),
            ).as_dict()
        if raw.get("auth"):
            self.on_status("Login required — waiting for you.")
            return _fail(
                "auth",
                BrowserError("Authentication required", AUTHENTICATION_REQUIRED, retryable=False),
            ).as_dict()
        return None

    def get_dialogs(self) -> dict[str, Any]:
        raw = self._raw_extract()
        dialogs = list(raw.get("dialogs") or [])
        if self._pending_dialog:
            dialogs.append(self._pending_dialog)
        return {"success": True, "action": "getDialogs", "dialogs": dialogs, "untrusted": True}

    def dismiss_dialog(self) -> dict[str, Any]:
        self._js_dialog_mode = "dismiss"
        self._pending_dialog = None
        page = self._ensure()
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return _ok("dismissDialog", url=self._safe_url(), verified=True).as_dict()

    def accept_dialog(self) -> dict[str, Any]:
        self._js_dialog_mode = "accept"
        page = self._ensure()
        try:
            page.locator("dialog[open] button, [role=dialog] button").first.click(timeout=1500)
            return _ok("acceptDialog", url=self._safe_url(), verified=True).as_dict()
        except Exception:
            pass
        return _ok(
            "acceptDialog",
            url=self._safe_url(),
            verified=True,
            data={"mode": "accept-next-js-dialog", "last": self._pending_dialog},
        ).as_dict()

    def list_frames(self) -> dict[str, Any]:
        page = self._ensure()
        frames = []
        try:
            frames = [{"url": f.url, "name": f.name} for f in page.frames]
        except Exception:
            pass
        return {"success": True, "action": "listFrames", "frames": frames}

    def wait_for_content_change(self, previous_fingerprint: str = "", timeout_ms: int = 4000) -> dict[str, Any]:
        page = self._ensure()
        prev = previous_fingerprint or ((self._cache.fingerprint if self._cache else "") or "")
        deadline = time.time() + max(0.2, timeout_ms / 1000)
        last_fp = prev
        while time.time() < deadline:
            state = self.get_page_state()
            last_fp = state.get("fingerprint") or ""
            if prev and last_fp != prev:
                return _ok(
                    "waitForContentChange",
                    url=self._safe_url(),
                    title=self._safe_title(),
                    state_changed=True,
                    verified=True,
                    data={"fingerprint": last_fp},
                ).as_dict()
            if not prev:
                prev = last_fp
            try:
                page.wait_for_timeout(80)
            except Exception:
                break
        if last_fp != previous_fingerprint and last_fp:
            return _ok(
                "waitForContentChange",
                url=self._safe_url(),
                state_changed=True,
                verified=True,
                data={"fingerprint": last_fp},
            ).as_dict()
        return _fail(
            "waitForContentChange",
            BrowserError("Page content did not change", PAGE_LOAD_TIMEOUT, retryable=True),
        ).as_dict()

    # --- element actions ---

    def _all_elements(self) -> list[BrowserElement]:
        return self._elements_from_raw(self._raw_extract())

    def find_element(self, query: str, role: str = "") -> dict[str, Any]:
        els = self._all_elements()
        ranked = resolve_candidates(els, query, role=role)
        return {
            "success": True,
            "action": "findElement",
            "query": query,
            "matches": [
                {"elementId": e.id, "role": e.role, "name": e.name or e.text, "confidence": round(s, 2), "selector": e.selector}
                for e, s in ranked[:8]
            ],
            "untrusted": True,
        }

    def _locator_for(self, el: BrowserElement):
        page = self._ensure()
        root = page
        if el.frame_url:
            try:
                for frame in page.frames:
                    if el.frame_url == frame.url or (el.frame_url and el.frame_url in (frame.url or "")):
                        root = frame
                        break
            except Exception:
                root = page
        if el.selector:
            loc = root.locator(el.selector)
            if loc.count():
                return loc.first
        if el.name:
            role = el.role if el.role in {"button", "link", "textbox", "checkbox", "radio"} else "button"
            loc = root.get_by_role(role, name=el.name[:80])
            if loc.count():
                return loc.first
            loc = root.get_by_text(el.name[:80], exact=False)
            if loc.count():
                return loc.first
        raise BrowserError("Could not bind locator for element", ELEMENT_NOT_FOUND)

    def click(
        self,
        ref: str = "",
        selector: str = "",
        name: str = "",
        role: str = "",
        visual_fallback: bool = True,
    ) -> dict[str, Any]:
        self.on_status(f"Looking for {name or selector or ref}…")
        try:
            pause = self._interrupt_check()
        except BrowserError as e:
            return _fail("click", e).as_dict()
        if pause:
            return pause
        try:
            page = self._ensure()
        except BrowserError as e:
            return _fail("click", e).as_dict()
        prev_fp = (self._cache.fingerprint if self._cache else "") or ""
        loc = None
        selector_failed = False
        try:
            if selector:
                try:
                    found = page.locator(selector)
                    selector_failed = found.count() == 0
                except Exception:
                    # An unparseable selector is a bad guess at a locator, not a page
                    # problem. Fall through to name resolution instead of surfacing a
                    # Playwright stack trace as the failure reason.
                    selector_failed = True
                if not selector_failed:
                    loc = found.first
            elif ref:
                loc = self._locator_from_ref(ref)
            elif name:
                els = self._all_elements()
                el, conf = pick_element(els, name, role=role)
                loc = self._locator_for(el)
            else:
                raise BrowserError("Provide name, ref, or selector", ELEMENT_NOT_FOUND, retryable=False)
            if loc is None and (selector_failed or name):
                self._invalidate()
                if name:
                    try:
                        els = self._all_elements()
                        el, _conf = pick_element(els, name, role=role)
                        loc = self._locator_for(el)
                    except BrowserError:
                        loc = None
                    if loc is None:
                        try:
                            loc = page.get_by_role("button", name=name).first
                            if loc.count() == 0:
                                loc = page.get_by_text(name, exact=False).first
                        except Exception:
                            loc = None
                if loc is None:
                    raise BrowserError(
                        f"DOM selector failed for {selector or name!r}; accessibility retry missed",
                        ELEMENT_NOT_FOUND,
                    )
            loc.click(timeout=5000)
        except BrowserError as e:
            if e.code == AMBIGUOUS_ELEMENT:
                return _fail("click", e).as_dict()
            if visual_fallback and e.code in {ELEMENT_NOT_FOUND, ELEMENT_NOT_INTERACTABLE} and name:
                return self._visual_click(name, e)
            return _fail("click", e).as_dict()
        except Exception as e:
            err = BrowserError(str(e), ELEMENT_NOT_INTERACTABLE)
            if visual_fallback and name:
                return self._visual_click(name, err)
            return _fail("click", err).as_dict()
        self._invalidate()
        page.wait_for_timeout(200)
        state = self.get_page_state()
        changed = state.get("fingerprint") != prev_fp
        return _ok(
            "click",
            url=self._safe_url(),
            title=self._safe_title(),
            state_changed=changed,
            verified=True,
            data={"clicked": name or selector or ref},
        ).as_dict()

    def _visual_click(self, name: str, original: BrowserError) -> dict[str, Any]:
        try:
            fb = self.fallback.click_named(name)
            if isinstance(fb, dict) and fb.get("verified"):
                return _ok(
                    "click",
                    url=self._safe_url(),
                    title=self._safe_title(),
                    verified=True,
                    data={"clicked": name, "visual_fallback": True, **fb},
                ).as_dict()
            return _fail(
                "click",
                BrowserError(
                    fb.get("message") or "Visual fallback required",
                    VISUAL_FALLBACK_REQUIRED,
                    retryable=True,
                    details=fb if isinstance(fb, dict) else {"name": name},
                ),
            ).as_dict()
        except BrowserError as e2:
            return _fail("click", e2).as_dict()
        except Exception:
            return _fail("click", original).as_dict()

    def _locator_from_ref(self, ref: str):
        els = self._all_elements()
        for i, el in enumerate(els):
            if el.id == ref or f"e{i + 1}" == ref:
                return self._locator_for(el)
        page = self._ensure()
        # fallback: nth interactive
        if ref.startswith("e") and ref[1:].isdigit():
            idx = int(ref[1:]) - 1
            loc = page.locator("a, button, input, textarea, select")
            if 0 <= idx < loc.count():
                return loc.nth(idx)
        raise BrowserError(f"Unknown snapshot ref: {ref}", ELEMENT_NOT_FOUND)

    def type_text(self, text: str, ref: str = "", selector: str = "", name: str = "") -> dict[str, Any]:
        return self.fill(text, ref=ref, selector=selector, name=name)

    def fill(self, text: str, ref: str = "", selector: str = "", name: str = "") -> dict[str, Any]:
        blob = f"{name} {selector} {ref}".lower()
        if "password" in blob:
            self.on_status("Refusing to handle password fields automatically.")
            return _fail(
                "fill",
                BrowserError("Password fields are not auto-filled", AUTHENTICATION_REQUIRED, retryable=False),
            ).as_dict()
        # A flow can turn into a sign-in wall between steps. Typing into whatever field
        # happens to be there is exactly the wrong response, so check before filling.
        try:
            pause = self._interrupt_check()
        except BrowserError as e:
            return _fail("fill", e).as_dict()
        if pause:
            return pause
        page = self._ensure()
        try:
            loc = self._target_locator(ref, selector, name, role="textbox")
            loc.fill(text)
            value = loc.input_value() if loc.count() else ""
            verified = value == text or (not text) or text in (value or "")
            return _ok("fill", url=self._safe_url(), verified=verified, data={"value_len": len(value or "")}).as_dict()
        except BrowserError as e:
            return _fail("fill", e).as_dict()
        except Exception as e:
            return _fail("fill", BrowserError(str(e), ELEMENT_NOT_INTERACTABLE)).as_dict()

    def clear(self, selector: str = "", name: str = "") -> dict[str, Any]:
        return self.fill("", selector=selector, name=name)

    def select(self, value: str, selector: str = "", name: str = "") -> dict[str, Any]:
        try:
            page = self._ensure()
            if selector or name:
                loc = self._target_locator("", selector, name, role="combobox")
            else:
                loc = page.locator("select").first
                if loc.count() == 0:
                    raise BrowserError("No dropdown found on this page", ELEMENT_NOT_FOUND)
            self._select_option(loc, value)
            current = ""
            try:
                current = loc.input_value()
            except Exception:
                pass
            return _ok("select", url=self._safe_url(), verified=True, data={"value": value, "selected": current}).as_dict()
        except BrowserError as e:
            return _fail("select", e).as_dict()
        except Exception as e:
            return _fail("select", BrowserError(str(e), ELEMENT_NOT_INTERACTABLE)).as_dict()

    def _select_option(self, loc, value: str) -> None:
        wanted = (value or "").strip()
        if not wanted:
            raise BrowserError("No option value provided", ELEMENT_NOT_FOUND, retryable=False)
        needle = wanted.lower()
        options: list[tuple[str, str]] = []
        try:
            n = loc.locator("option").count()
            for i in range(n):
                opt = loc.locator("option").nth(i)
                text = (opt.inner_text() or "").strip()
                val = opt.get_attribute("value") or ""
                options.append((text, val))
        except Exception:
            options = []
        for text, val in options:
            if needle == text.lower() or needle == val.lower() or needle in text.lower():
                if val != "":
                    loc.select_option(value=val, timeout=800)
                else:
                    loc.select_option(label=text, timeout=800)
                return
        try:
            loc.select_option(label=wanted, timeout=400)
            return
        except Exception:
            pass
        try:
            loc.select_option(value=wanted, timeout=400)
            return
        except Exception:
            pass
        raise BrowserError(f"Could not select option {wanted!r}", ELEMENT_NOT_INTERACTABLE)

    def check(self, selector: str = "", name: str = "", checked: bool = True) -> dict[str, Any]:
        try:
            loc = self._target_locator("", selector, name, role="checkbox")
            if checked:
                loc.check()
            else:
                loc.uncheck()
            return _ok("check" if checked else "uncheck", url=self._safe_url(), verified=True).as_dict()
        except BrowserError as e:
            return _fail("check", e).as_dict()
        except Exception as e:
            return _fail("check", BrowserError(str(e), ELEMENT_NOT_INTERACTABLE)).as_dict()

    def uncheck(self, selector: str = "", name: str = "") -> dict[str, Any]:
        return self.check(selector=selector, name=name, checked=False)

    def _target_locator(self, ref: str, selector: str, name: str, role: str = ""):
        page = self._ensure()
        if selector:
            loc = page.locator(selector)
            if loc.count() == 0:
                raise BrowserError(f"No element matches {selector}", ELEMENT_NOT_FOUND)
            return loc.first
        if ref:
            return self._locator_from_ref(ref)
        if name:
            el, _ = pick_element(self._all_elements(), name, role=role)
            return self._locator_for(el)
        raise BrowserError("Provide name, ref, or selector", ELEMENT_NOT_FOUND, retryable=False)

    def press(self, key: str) -> dict[str, Any]:
        page = self._ensure()
        page.keyboard.press(key)
        return _ok("press", url=self._safe_url(), data={"key": key}).as_dict()

    def scroll(self, direction: str = "down", amount: int = 400) -> dict[str, Any]:
        page = self._ensure()
        dy = -abs(amount) if direction in {"up", "top"} else abs(amount)
        if direction == "top":
            page.evaluate("window.scrollTo(0,0)")
        elif direction == "bottom":
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        else:
            page.mouse.wheel(0, dy)
        self._invalidate()
        return _ok("scroll", url=self._safe_url(), verified=True).as_dict()

    def scroll_to_element(self, name: str = "", selector: str = "") -> dict[str, Any]:
        try:
            page = self._ensure()
            loc = None
            if selector:
                loc = page.locator(selector)
            elif name:
                try:
                    loc = self._target_locator("", "", name)
                except BrowserError:
                    loc = None
                if loc is None or getattr(loc, "count", lambda: 0)() == 0:
                    loc = page.get_by_role("heading", name=name)
                if loc.count() == 0:
                    loc = page.get_by_text(name, exact=False)
                if loc.count() == 0:
                    slug = name.lower().replace(" ", "-")
                    loc = page.locator(f"#{slug}, [id*='{slug}' i]")
            else:
                raise BrowserError("Provide a section name or selector to scroll to", ELEMENT_NOT_FOUND, retryable=False)
            if loc.count() == 0:
                raise BrowserError(f"Could not find {name or selector!r} to scroll to", ELEMENT_NOT_FOUND)
            loc.first.scroll_into_view_if_needed()
            self._invalidate()
            return _ok("scrollToElement", url=self._safe_url(), verified=True).as_dict()
        except BrowserError as e:
            return _fail("scrollToElement", e).as_dict()
        except Exception as e:
            return _fail("scrollToElement", BrowserError(str(e), ELEMENT_NOT_FOUND)).as_dict()

    def wait(self, selector: str = "", timeout_ms: int = 5000) -> dict[str, Any]:
        page = self._ensure()
        try:
            if selector:
                page.wait_for_selector(selector, timeout=timeout_ms)
            else:
                page.wait_for_timeout(min(timeout_ms, 15000))
        except Exception as e:
            return _fail("wait", BrowserError(str(e), ELEMENT_NOT_FOUND)).as_dict()
        return _ok("wait", url=self._safe_url(), data={"waited": selector or timeout_ms}).as_dict()

    def wait_for_element(self, selector: str = "", name: str = "", timeout_ms: int = 8000) -> dict[str, Any]:
        try:
            if selector:
                return self.wait(selector=selector, timeout_ms=timeout_ms)
            page = self._ensure()
            page.get_by_text(name, exact=False).first.wait_for(timeout=timeout_ms)
            return _ok("waitForElement", url=self._safe_url(), verified=True).as_dict()
        except BrowserError as e:
            return _fail("waitForElement", e).as_dict()
        except Exception as e:
            return _fail("waitForElement", BrowserError(str(e), ELEMENT_NOT_FOUND)).as_dict()

    def wait_for_text(self, text: str, timeout_ms: int = 8000) -> dict[str, Any]:
        page = self._ensure()
        try:
            page.get_by_text(text, exact=False).first.wait_for(timeout=timeout_ms)
        except Exception as e:
            return _fail("waitForText", BrowserError(str(e), ELEMENT_NOT_FOUND)).as_dict()
        return _ok("waitForText", url=self._safe_url(), verified=True).as_dict()

    def wait_for_navigation(self, timeout_ms: int = 15000) -> dict[str, Any]:
        page = self._ensure()
        prev = self._safe_url()
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        self._invalidate()
        return _ok("waitForNavigation", url=self._safe_url(), previous_url=prev, state_changed=self._safe_url() != prev, verified=True).as_dict()

    def upload_file(self, path: str, selector: str = "input[type=file]", name: str = "") -> dict[str, Any]:
        try:
            authorized = self.files.authorize_upload(path)
            loc = self._target_locator("", selector, name)
            loc.set_input_files(str(authorized))
            return _ok(
                "uploadFile",
                url=self._safe_url(),
                verified=True,
                data={"filename": authorized.name, "path": str(authorized)},
            ).as_dict()
        except BrowserError as e:
            return _fail("uploadFile", e).as_dict()
        except Exception as e:
            return _fail("uploadFile", BrowserError(str(e), UPLOAD_FAILED, retryable=False)).as_dict()

    def get_recent_downloads(self) -> dict[str, Any]:
        return {"success": True, "action": "getRecentDownloads", "downloads": list(self.files.recent)}
