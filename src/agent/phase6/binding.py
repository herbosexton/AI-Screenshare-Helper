"""Late binding of step arguments from TaskContext.

A plan is written before the work happens, so "open my newest resume" cannot carry a
path: the path only exists after the search step runs. These arguments are declared
here as deferred, accepted by the validator without a value, and filled in from task
context at the moment the step executes.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse

from src.agent.phase6.models import TaskContext


# tool -> arguments the executor can supply from context when the plan omits them.
DEFERRED_ARGUMENTS: dict[str, set[str]] = {
    "files.open": {"path"},
    "files.read": {"path"},
    "files.extract_text": {"path"},
    "files.get_metadata": {"path"},
    "computer.open_file": {"path"},
    "browser.switchTab": {"tab_id", "query"},
}

# tool -> filters the planner tends to invent rather than take from the user. Asked for
# "my most recent PDF", a small model will still volunteer name_contains="*.pdf" or
# since_days=0 and match nothing. When a search comes back empty these come off and the
# search runs once more; the extension the user actually named stays on.
RELAXABLE_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "files.find_recent": ("name_contains", "since_days"),
    "files.find_by_name": ("extensions", "since_days"),
    "files.search": ("extensions", "since_days"),
    "files.list": ("name_contains",),
}


# Sites people name by brand rather than by URL. A planner that does not know the address
# invents one, and a guess a letter or two off a famous brand is precisely what
# typosquatters register: "chatgbt.com" is a real, live site, and it is not ChatGPT.
KNOWN_SITES: dict[str, str] = {
    "chatgpt": "https://chatgpt.com",
    "openai": "https://openai.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "amazon": "https://www.amazon.com",
    "reddit": "https://www.reddit.com",
    "linkedin": "https://www.linkedin.com",
    "wikipedia": "https://www.wikipedia.org",
    "facebook": "https://www.facebook.com",
    "netflix": "https://www.netflix.com",
}

_KNOWN_HOSTS = {urlparse(u).hostname or "" for u in KNOWN_SITES.values()}


def canonical_site(url: str) -> str:
    """Snap a near-miss of a well-known domain onto the real one, dropping invented paths.

    Only near-misses move. An exact host, a known host, and anything unrelated to these
    brands are all returned untouched, so this cannot redirect a site the user asked for.
    """
    raw = (url or "").strip()
    if not raw:
        return raw
    host = (urlparse(raw if "://" in raw else f"https://{raw}").hostname or "").lower()
    bare = host[4:] if host.startswith("www.") else host
    if not bare or host in _KNOWN_HOSTS or f"www.{bare}" in _KNOWN_HOSTS:
        return raw
    label = bare.split(".")[0]
    for brand, canonical in KNOWN_SITES.items():
        if label == brand or abs(len(label) - len(brand)) > 1:
            continue
        if SequenceMatcher(None, label, brand).ratio() >= 0.8:
            return canonical
    return raw


def site_url(text: str) -> str:
    """The address of a well-known site named in `text`, or "" if `text` names no such site.

    Tolerates a misspelling, because the name usually arrives by voice: "ChatGBT" is what
    a transcriber hears, and the page it refers to is still ChatGPT.
    """
    label = re.sub(r"^(?:https?://)?(?:www\.)?", "", (text or "").strip().lower())
    label = re.sub(r"[^a-z0-9]", "", label.split("/")[0].split(".")[0])
    if not label:
        return ""
    if label in KNOWN_SITES:
        return KNOWN_SITES[label]
    for brand, url in KNOWN_SITES.items():
        if abs(len(label) - len(brand)) <= 1 and SequenceMatcher(None, label, brand).ratio() >= 0.8:
            return url
    return ""


def retarget_arguments(registry: Any, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Carry a failed step's arguments to a substitute tool that names them differently.

    A fallback that drops what the step was aiming at is no fallback: swapping
    focus_window for switchTab and losing "ChatGPT" leaves a tool with nothing to find.
    Returns {} only when nothing survives translation.
    """
    model = getattr(registry.get(tool), "parameters_model", None)
    if model is None:
        return {}
    fields = set(model.model_fields)
    args, _ = canonical_arguments(dict(arguments or {}), fields)
    if "url" in fields and not args.get("url"):
        # The step named a site rather than an address, which is how people say it.
        for value in (arguments or {}).values():
            found = site_url(value) if isinstance(value, str) else ""
            if found:
                args["url"] = found
                break
    try:
        registry.validate_arguments(tool, args)
    except Exception:
        return {}
    return args


# Words naming a file format rather than a file. As a *name* filter these are the model
# echoing the goal's "PDF" back as though documents are called that.
_FORMAT_WORDS = frozenset(
    "pdf doc docx txt rtf csv xls xlsx ppt pptx md png jpg jpeg gif zip".split()
)
_GLOB = re.compile(r"[*?\[\]]")


def _planner_invented(value: Any, goal: str) -> bool:
    """Whether a filter came from the model rather than from something the user said."""
    if not isinstance(value, str):
        return True  # since_days=0 and friends: nobody asks for a zero-day window.
    word = value.strip().strip("*.").lower()
    if not word or _GLOB.search(value) or word in _FORMAT_WORDS:
        return True
    return word not in (goal or "").lower()


def relax_arguments(tool: str, arguments: dict[str, Any], goal: str = "") -> dict[str, Any]:
    """Drop the planner's self-imposed filters. Returns the arguments unchanged if none.

    A filter the user actually spoke is not self-imposed. Dropping name_contains="resume"
    turns a retry of "find my most recent resume" into "find my most recent anything", which
    succeeds on the wrong file — worse than the empty result it was sent to fix.
    """
    removable = RELAXABLE_ARGUMENTS.get(tool, ())
    return {
        k: v
        for k, v in (arguments or {}).items()
        if k not in removable or not _planner_invented(v, goal)
    }

# "<path to the file>", "{selected_file}", "path/to/resume.pdf" — the model describing a
# value it does not have rather than supplying one.
_PLACEHOLDER = re.compile(
    r"^\s*[<{\[]|^(path|file|filename|the file|selected[_ ]file|your resume|newest[_ ]\w+)\s*$"
    r"|^(path|\.)?[/\\]?to[/\\]",
    re.I,
)


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or text.lower() in {"null", "none", "unknown", "tbd"}:
        return True
    return bool(_PLACEHOLDER.match(text))


# Small models name arguments by what they mean rather than by the schema field:
# browser.click(query="Apply") instead of browser.click(name="Apply"). Silently dropping
# the key produces a click with no target, so the value is moved to the real field.
_ALIAS_CANDIDATES: dict[str, tuple[str, ...]] = {
    "query": ("name", "query"),
    "search": ("query", "name"),
    "keyword": ("query", "name"),
    "element": ("name", "query"),
    "label": ("name", "query"),
    "button": ("name", "query"),
    "link": ("name", "url"),
    "target": ("name", "path", "url"),
    "title": ("query", "name"),
    # focus_window takes title_contains, switchTab takes query. Having seen one, the model
    # reaches for it on the other, and "switch back to Google" costs a whole planner call.
    "title_contains": ("title_contains", "query", "name"),
    "window": ("title_contains", "name"),
    "tab": ("query", "tab_id"),
    "file": ("path",),
    "filename": ("path",),
    "file_path": ("path",),
    "filepath": ("path",),
    "directory": ("path", "directory"),
    "folder": ("path", "directory"),
    "app": ("name", "path"),
    "application": ("name", "path"),
    "address": ("url",),
    "website": ("url",),
    "site": ("url",),
    "content": ("text",),
    "value": ("text", "name"),
}


# jQuery's :contains() is not CSS. Playwright raises a raw SyntaxError on it, so the
# label inside is moved to the name argument, which is what the model meant anyway.
_JQUERY_CONTAINS = re.compile(r""":contains\(\s*['"]?(?P<label>[^'")]+)['"]?\s*\)""", re.I)


def canonical_arguments(arguments: dict[str, Any], fields: set[str]) -> tuple[dict[str, Any], set[str]]:
    """Move aliased argument names onto real schema fields. Returns (args, unresolved)."""
    args: dict[str, Any] = {}
    unresolved: set[str] = set()
    for key, value in (arguments or {}).items():
        if key == "selector" and isinstance(value, str) and "name" in fields:
            hit = _JQUERY_CONTAINS.search(value)
            if hit:
                args.setdefault("name", hit.group("label").strip())
                continue
        if key in fields:
            args[key] = value
            continue
        for candidate in _ALIAS_CANDIDATES.get(key.lower(), ()):
            if candidate in fields and candidate not in args and candidate not in arguments:
                args[candidate] = value
                break
        else:
            unresolved.add(key)
    return args, unresolved


def bind_arguments(tool: str, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
    """Fill deferred arguments from task context. Never overwrites a real value."""
    args = dict(arguments or {})
    goal = str(context.facts.get("goal") or "")
    if tool in {"files.find_recent", "files.find_by_name"} and not str(args.get("name_contains") or "").strip():
        if re.search(r"\b(resume|r[eé]sum[eé]|curriculum vitae|\bcv\b)", goal, re.I):
            args["name_contains"] = "resume"
    deferred = DEFERRED_ARGUMENTS.get(tool)
    if not deferred:
        return args

    if "path" in deferred:
        if is_placeholder(args.get("path")) and context.selected_file:
            args["path"] = context.selected_file

    if tool == "browser.switchTab":
        if is_placeholder(args.get("tab_id")) and is_placeholder(args.get("query")):
            args.pop("tab_id", None)
            target = context.current_page or context.current_url
            if not target and context.candidate_tabs:
                first = context.candidate_tabs[0]
                target = str(first.get("title") or first.get("url") or "")
            if target:
                args["query"] = target

    return {k: v for k, v in args.items() if not is_placeholder(v) or k not in deferred}


def missing_required(registry: Any, tool: str, arguments: dict[str, Any]) -> set[str]:
    """Required schema fields still without a value after binding."""
    model = getattr(registry.get(tool), "parameters_model", None)
    if model is None:
        return set()
    return {
        name
        for name, field in model.model_fields.items()
        if field.is_required() and name not in arguments
    }
