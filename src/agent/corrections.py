"""Post-task corrections repair the last result locally. They do not replan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from src.agent.phase6.artifacts import (
    CLOSED,
    OPEN_STATES,
    PERSISTING,
    UNKNOWN,
    capture_opened_file,
    find_artifact,
    inspect_artifact,
    verify_after_task,
)
from src.agent.phase6.recent import recent_task


RECENT_TASK_CORRECTION = "RECENT_TASK_CORRECTION"
RECENT_ARTIFACT_SHOW = "RECENT_ARTIFACT_SHOW"
RECENT_ARTIFACT_STATUS = "RECENT_ARTIFACT_STATUS"
RECENT_ARTIFACT_REOPEN = "RECENT_ARTIFACT_REOPEN"
RECENT_ANALYSIS_FOLLOWUP = "RECENT_ANALYSIS_FOLLOWUP"
SHOW_GAPS = "SHOW_GAPS"
SHOW_MATCHES = "SHOW_MATCHES"
SHOW_PARTIALS = "SHOW_PARTIALS"
SHOW_CONFIRMATION_ITEMS = "SHOW_CONFIRMATION_ITEMS"
SUMMARIZE_COMPARISON = "SUMMARIZE_COMPARISON"
SHOW_EVIDENCE = "SHOW_EVIDENCE"

_PERSISTENCE = re.compile(
    r"\b(you (didn'?t|did not) leave it open|you closed it|you (didn'?t|did not) leave .+ open)\b",
    re.I,
)
_WRONG_FILE = re.compile(
    r"\b(wrong (resume|file|one|document)|not the right (resume|file|one)|"
    r"second newest|2nd newest|use the second|opened the wrong)\b",
    re.I,
)
_WRONG_PAGE = re.compile(
    r"\b(that'?s not the page|that is not the page|that page isn'?t|wrong page|"
    r"not the page i['’]?m on)\b",
    re.I,
)
_WRONG_SWITCH = re.compile(
    r"\b(didn'?t switch|did not switch|didn'?t go back|did not go back)\b",
    re.I,
)
_AMBIGUOUS = re.compile(r"^\s*(that'?s|that is) (wrong|not right|incorrect)\.?\s*$", re.I)
_ARTIFACT_REF = re.compile(r"\b(resume|cv|file|document|pdf|it|that)\b", re.I)
_CANT_SEE = re.compile(
    r"\b((i )?(don'?t|do not|can'?t|cannot) see|where did (it|the) go|disappeared)\b",
    re.I,
)
_ANALYSIS = re.compile(
    r"\b(compare|missing|requirements?|gap|versus|vs\.?|matched|matches|covered|partial)\b",
    re.I,
)
_NEW_COMPARE_SOURCE = re.compile(
    r"\bcompare\b.+\b(this|that)\s+(one|job|page|listing|role)\b|"
    r"\b(this|that)\s+(one|job|page|listing|role)\b.+\b(resume|cv)\b",
    re.I,
)
_SHOW_GAPS = re.compile(
    r"\b(gaps?|what am i missing|which requirements am i missing|weak spots?|"
    r"what doesn'?t (my )?resume cover|not shown)\b",
    re.I,
)
_SHOW_MATCHES = re.compile(
    r"\b(what matched|strong matches|matched well|what (were|are) the (strong )?matches)\b",
    re.I,
)
_SHOW_PARTIALS = re.compile(r"\b(partials?|partial matches)\b", re.I)
_SHOW_CONFIRM = re.compile(r"\b(needs? confirmation|couldn'?t (you )?determine|uncertain)\b", re.I)
_SHOW_EVIDENCE = re.compile(
    r"\b(why did you (say|mark)|why (is|was|did you mark) .{0,48}(covered|partial|a match|not shown)|"
    r"why .{0,40}(python|degree|azure|cloud|agentic|covered|partial)|"
    r"where (does|did) (that|it) (show|appear)|source evidence|show (the )?evidence)\b",
    re.I,
)
_SUMMARIZE = re.compile(r"\b(summarize|recap|overview|the comparison)\b", re.I)
_SHOW = re.compile(
    r"\b(show me|bring (it |the )?(up|back|forward)|bring (it|the resume|the file)|"
    r"open (it|the resume) again)\b",
    re.I,
)
_STATUS = re.compile(
    r"\b(where('?s| is)|is (it|the resume|the file) still open)\b",
    re.I,
)


@dataclass
class TaskCorrection:
    kind: str
    raw: str
    referent: str = ""
    claimed_outcome: str = ""


def _recent_artifact_available() -> bool:
    ctx = recent_task()
    if ctx is None:
        return False
    return bool(ctx.selected_resume_path or ctx.filename or ctx.hwnd or find_artifact())


def followup_scores(text: str) -> dict[str, Any]:
    """Recent context is memory, not an automatic intent override."""
    from src.agent.fast_coverage import analyze_fast_coverage

    report = analyze_fast_coverage(text)
    actions = list(report.detected_actions)
    domains = list(report.detected_domains)
    segs = list(report.command_segments)
    analysis = bool(_ANALYSIS.search(text or ""))
    new_compare = bool(_NEW_COMPARE_SOURCE.search(text or ""))
    self_contained = 0
    if len(actions) >= 2:
        self_contained += 2
    if len(segs) >= 3:
        self_contained += 2
    if len(domains) >= 2:
        self_contained += 2
    if analysis and (len(actions) >= 2 or len(segs) >= 2):
        self_contained += 2
    if new_compare:
        self_contained = max(self_contained, 3)
    followup = 0
    if _recent_artifact_available():
        followup += 1
    if _ARTIFACT_REF.search(text or ""):
        followup += 1
    if _CANT_SEE.search(text or "") or _SHOW.search(text or "") or _STATUS.search(text or ""):
        followup += 2
    if _PERSISTENCE.search(text or "") or _WRONG_FILE.search(text or "") or _AMBIGUOUS.match((text or "").strip()):
        followup += 2
    if recent_comparison() is not None and classify_analysis_intent(text or ""):
        followup += 2
    override = self_contained >= 3
    return {
        "command_segments": segs,
        "action_verbs": actions,
        "detected_domains": domains,
        "self_contained_goal_score": self_contained,
        "followup_dependency_score": followup,
        "new_goal_override": override,
        "task_correction_allowed": (not override) and followup >= 2,
        "recent_artifact_context_present": _recent_artifact_available(),
        "recent_task_context_present": recent_task() is not None,
    }


def new_goal_override(text: str) -> bool:
    return bool(followup_scores(text).get("new_goal_override"))


def classify_task_correction(text: str) -> Optional[TaskCorrection]:
    t = (text or "").strip()
    if not t:
        return None
    scores = followup_scores(t)
    print(
        f"[FollowupGate] original_utterance={t!r} recent_artifact_exists={scores['recent_artifact_context_present']} "
        f"command_segments={len(scores['command_segments'])} action_verbs={scores['action_verbs']} "
        f"detected_domains={scores['detected_domains']} self_contained_goal_score={scores['self_contained_goal_score']} "
        f"followup_dependency_score={scores['followup_dependency_score']} "
        f"new_goal_override={str(scores['new_goal_override']).lower()} "
        f"task_correction_allowed={str(scores['task_correction_allowed']).lower()}"
    )
    if scores["new_goal_override"]:
        return None
    if _AMBIGUOUS.match(t):
        return TaskCorrection(kind="ambiguous", raw=t)
    if _PERSISTENCE.search(t):
        return TaskCorrection(
            kind="persistence",
            raw=t,
            referent="selected_resume",
            claimed_outcome="resume_persisted",
        )
    if _WRONG_FILE.search(t):
        return TaskCorrection(kind="wrong_file", raw=t, referent="selected_resume")
    if _WRONG_PAGE.search(t):
        return TaskCorrection(kind="wrong_page", raw=t, referent="final_browser_page")
    if _WRONG_SWITCH.search(t):
        return TaskCorrection(kind="wrong_switch", raw=t, referent="chrome")
    if _recent_artifact_available() and _ARTIFACT_REF.search(t):
        if _STATUS.search(t) and not _CANT_SEE.search(t) and not _SHOW.search(t):
            return TaskCorrection(kind="artifact_status", raw=t, referent="selected_resume")
        if _CANT_SEE.search(t) or _SHOW.search(t):
            return TaskCorrection(kind="artifact_show", raw=t, referent="selected_resume")
    return None


def apply_task_correction(corr: TaskCorrection, orch) -> Optional[dict[str, Any]]:
    ctx = recent_task()
    if ctx is None:
        return None
    print(
        f"[TaskCorrection] type={corr.kind} referent={corr.referent or '-'} "
        f"claimed={corr.claimed_outcome or '-'} task_id={ctx.task_id} planner_calls=0"
    )
    if corr.kind == "ambiguous":
        return _result(
            "Do you mean the resume I opened or the Chrome page I reported?",
            corr,
            repair_action="clarify",
        )
    if corr.kind == "persistence":
        return _repair_persistence(orch, ctx, corr)
    if corr.kind == "wrong_file":
        return _repair_wrong_file(orch, ctx, corr)
    if corr.kind == "wrong_page":
        return _repair_wrong_page(orch, ctx, corr)
    if corr.kind == "wrong_switch":
        return _repair_switch(orch, ctx, corr)
    if corr.kind == "artifact_status":
        return _artifact_status(orch, ctx, corr)
    if corr.kind == "artifact_show":
        return _artifact_show(orch, ctx, corr)
    return None


def recent_comparison():
    ctx = recent_task()
    if ctx is None or not ctx.comparison_result:
        return None
    return ctx


def classify_analysis_intent(text: str) -> str:
    t = (text or "").strip()
    if not t or _NEW_COMPARE_SOURCE.search(t):
        return ""
    if _SHOW_EVIDENCE.search(t):
        return SHOW_EVIDENCE
    if _SHOW_GAPS.search(t):
        return SHOW_GAPS
    if _SHOW_MATCHES.search(t):
        return SHOW_MATCHES
    if _SHOW_PARTIALS.search(t):
        return SHOW_PARTIALS
    if _SHOW_CONFIRM.search(t):
        return SHOW_CONFIRMATION_ITEMS
    if _SUMMARIZE.search(t):
        return SUMMARIZE_COMPARISON
    return ""


class RecentAnalysisFollowupResolver:
    """Answer follow-ups from the stored ComparisonResult. planner_calls=0."""

    def classify(self, text: str) -> Optional[TaskCorrection]:
        if new_goal_override(text):
            return None
        if recent_comparison() is None:
            return None
        intent = classify_analysis_intent(text)
        if not intent:
            return None
        return TaskCorrection(kind=intent, raw=text, referent="comparison_result")

    def apply(self, corr: TaskCorrection) -> Optional[dict[str, Any]]:
        ctx = recent_comparison()
        if ctx is None:
            return None
        from src.agent.phase6.compare import (
            ComparisonResult,
            format_confirmation,
            format_detailed,
            format_evidence,
            format_gaps,
            format_matches,
            format_partials,
            format_spoken,
        )

        result = ComparisonResult.from_dict(ctx.comparison_result)
        if result is None:
            return None
        if corr.kind == SHOW_GAPS:
            message = format_gaps(result)
            spoken = "Here are the gaps from the comparison I already ran."
        elif corr.kind == SHOW_MATCHES:
            message = format_matches(result)
            spoken = "Here are the strong matches from that comparison."
        elif corr.kind == SHOW_PARTIALS:
            message = format_partials(result)
            spoken = "Here are the partial matches."
        elif corr.kind == SHOW_CONFIRMATION_ITEMS:
            message = format_confirmation(result)
            spoken = "These needed confirmation from the resume alone."
        elif corr.kind == SHOW_EVIDENCE:
            message = format_evidence(result, corr.raw)
            spoken = message
        else:
            message = format_detailed(result)
            spoken = format_spoken(result)
        print(
            f"[AnalysisFollowup] recent_analysis_followup={corr.kind} "
            f"planner_calls=0 task_id={ctx.task_id}"
        )
        return {
            "ok": True,
            "message": message,
            "spoken": spoken,
            "local": True,
            "planner_admitted": False,
            "planner_admission_reason": "NOT_ADMITTED",
            "planner_calls": 0,
            "utterance_type": RECENT_ANALYSIS_FOLLOWUP,
            "conceptual_route": "LOCAL_ACTION",
            "analysis_followup": corr.kind,
            "recent_analysis_followup": True,
        }


def classify_analysis_followup(text: str) -> Optional[TaskCorrection]:
    return RecentAnalysisFollowupResolver().classify(text)


def apply_analysis_followup(corr: TaskCorrection) -> Optional[dict[str, Any]]:
    return RecentAnalysisFollowupResolver().apply(corr)


def _computer(orch):
    return getattr(orch, "computer", None)


def _result(
    message: str,
    corr: TaskCorrection,
    *,
    repair_action: str = "",
    repair_verified: str = "",
    ok: bool = True,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "message": message,
        "local": True,
        "planner_admitted": False,
        "planner_admission_reason": "NOT_ADMITTED",
        "planner_calls": 0,
        "utterance_type": {
            "artifact_show": RECENT_ARTIFACT_SHOW,
            "artifact_status": RECENT_ARTIFACT_STATUS,
            "persistence": RECENT_TASK_CORRECTION,
        }.get(corr.kind, RECENT_TASK_CORRECTION),
        "conceptual_route": "LOCAL_ACTION",
        "correction_detected": True,
        "correction_type": corr.kind,
        "referent": corr.referent,
        "claimed_outcome": corr.claimed_outcome,
        "repair_action": repair_action,
        "repair_verified": repair_verified,
        "state_change": repair_action not in {"", "clarify", "explain"},
    }


def _repair_persistence(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    computer = _computer(orch)
    art = find_artifact("resume") or find_artifact()
    name = (art.filename if art else "") or ctx.selected_resume_path.replace("\\", "/").split("/")[-1] or "the resume"
    report = inspect_artifact(computer, art) if art else None
    state = report.state if report else CLOSED
    print(
        f"[TaskCorrection] persistence_check state={state} path={name} "
        f"hwnd={getattr(art, 'hwnd', 0) or '-'}"
    )
    if state in OPEN_STATES:
        return _result(
            "It's still open in the background. I can bring it forward if you want.",
            corr,
            repair_action="explain",
            repair_verified=state,
        )
    path = ctx.selected_resume_path
    if not path:
        return _result(
            "You're right — I can't find the resume I opened. Which file should I open?",
            corr,
            repair_action="ask",
            repair_verified=CLOSED,
        )
    opened = _reopen_path(orch, path)
    computer = _computer(orch)
    art = find_artifact("resume") or find_artifact()
    report = verify_after_task(computer, art, delay_s=0.2) if art else None
    verified = bool(report and report.state in OPEN_STATES)
    if opened and verified:
        return _result(
            f"You're right. The resume had closed. I reopened {name} and verified it's still open.",
            corr,
            repair_action="reopen",
            repair_verified=PERSISTING,
        )
    return _result(
        f"You're right. The resume had closed, and I could not reopen {name}.",
        corr,
        repair_action="reopen",
        repair_verified=UNKNOWN,
        ok=False,
    )


def _visibility_kind(report, art) -> str:
    handler = str((report.process_name if report else "") or (art.extra.get("process_name") if art else "") or "").lower()
    state = report.state if report else CLOSED
    if state == "MINIMIZED":
        return "MINIMIZED"
    if state == CLOSED:
        return "CLOSED"
    if state == UNKNOWN:
        return "UNKNOWN"
    if report and report.is_foreground:
        return "VISIBLE_FOREGROUND"
    if "chrome" in handler:
        return "OPEN_BROWSER_TAB"
    return "OPEN_BACKGROUND"


def _handler_label(report, art, ctx) -> str:
    raw = str(
        (report.process_name if report else "")
        or (art.extra.get("default_handler") if art else "")
        or ctx.process_name
        or ""
    ).lower()
    if "acrobat" in raw:
        return "Adobe Acrobat"
    if "winword" in raw or "word" in raw:
        return "Word"
    if "msedge" in raw or "edge" in raw:
        return "Microsoft Edge"
    if "chrome" in raw:
        return "Chrome"
    return ""


def _focus_artifact(orch, art) -> bool:
    computer = _computer(orch)
    if computer is None or art is None:
        return False
    if art.hwnd and hasattr(computer, "focus_window"):
        try:
            computer.focus_window(handle=art.hwnd)
            return True
        except Exception:
            pass
    title = (art.filename or "").rsplit(".", 1)[0]
    if title and hasattr(computer, "focus_window"):
        try:
            computer.focus_window(title_contains=title)
            return True
        except Exception:
            pass
    return False


def _activate_resume_tab(art, ctx) -> bool:
    hwnd = int((art.hwnd if art else 0) or ctx.hwnd or 0)
    title = str((art.extra.get("resume_tab_title") if art else "") or ctx.resume_tab_title or ctx.filename or "")
    if not hwnd or not title:
        return False
    try:
        from src.agent.browser.discovery import extract_tab_title
        from src.agent.browser.tabs import ActiveTabResolver

        needle = extract_tab_title(title) or title
        return bool(ActiveTabResolver().activate_tab_named(hwnd, needle))
    except Exception:
        return False


def _artifact_status(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    computer = _computer(orch)
    art = find_artifact("resume") or find_artifact()
    report = inspect_artifact(computer, art) if art else None
    kind = _visibility_kind(report, art)
    name = (art.filename if art else "") or ctx.filename or "the resume"
    app = _handler_label(report, art, ctx)
    print(
        f"[ArtifactFollowup] intent={RECENT_ARTIFACT_STATUS} filename={name} "
        f"state={kind} hwnd={getattr(art, 'hwnd', 0) or ctx.hwnd or '-'} planner_calls=0"
    )
    if kind == "VISIBLE_FOREGROUND":
        where = f" in {app}" if app else ""
        return _result(f"{name} is open{where} in front of you.", corr, repair_action="explain", repair_verified=kind)
    if kind == "MINIMIZED":
        return _result(f"It's minimized.", corr, repair_action="explain", repair_verified=kind)
    if kind == "OPEN_BROWSER_TAB":
        return _result("It's open in another Chrome tab.", corr, repair_action="explain", repair_verified=kind)
    if kind == "OPEN_BACKGROUND":
        where = f" in {app}" if app else ""
        return _result(
            f"It's still open{where} in the background.",
            corr,
            repair_action="explain",
            repair_verified=kind,
        )
    if kind == CLOSED:
        return _result("It's not open anymore.", corr, repair_action="explain", repair_verified=CLOSED)
    return _result("I couldn't confirm where the resume is.", corr, repair_action="explain", repair_verified=UNKNOWN)


def _artifact_show(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    computer = _computer(orch)
    art = find_artifact("resume") or find_artifact()
    report = inspect_artifact(computer, art) if art else None
    kind = _visibility_kind(report, art)
    name = (art.filename if art else "") or ctx.filename or "the resume"
    print(
        f"[ArtifactFollowup] intent={RECENT_ARTIFACT_SHOW} filename={name} "
        f"state={kind} hwnd={getattr(art, 'hwnd', 0) or ctx.hwnd or '-'} planner_calls=0"
    )
    if kind == "VISIBLE_FOREGROUND":
        return _result(f"{name} is already in front of you.", corr, repair_action="explain", repair_verified=kind)
    if kind == "MINIMIZED":
        ok = _focus_artifact(orch, art)
        return _result(
            "It was minimized. I restored the resume." if ok else "It was minimized, but I could not restore it.",
            corr,
            repair_action="restore",
            repair_verified=kind,
            ok=ok,
        )
    if kind == "OPEN_BROWSER_TAB":
        _activate_resume_tab(art, ctx)
        ok = _focus_artifact(orch, art)
        return _result(
            "It was open in another tab. I switched to it." if ok else "It was open in another tab, but I could not switch to it.",
            corr,
            repair_action="activate_tab",
            repair_verified=kind,
            ok=ok,
        )
    if kind == "OPEN_BACKGROUND":
        ok = _focus_artifact(orch, art)
        return _result(
            "It was still open in the background. I brought it forward." if ok else "It was still open in the background, but I could not bring it forward.",
            corr,
            repair_action="bring_forward",
            repair_verified=kind,
            ok=ok,
        )
    path = ctx.selected_resume_path
    if not path:
        return _result("I can't find the resume I opened. Which file should I open?", corr, repair_action="ask", ok=False)
    opened = _reopen_path(orch, path)
    art = find_artifact("resume") or find_artifact()
    report = verify_after_task(_computer(orch), art, delay_s=0.2) if art else None
    verified = bool(report and report.state in OPEN_STATES)
    if kind == UNKNOWN:
        msg = f"I couldn't confirm that the resume was still open, so I reopened {name}."
    else:
        msg = f"The resume had closed. I reopened {name}."
    if opened and verified:
        _focus_artifact(orch, art)
        return _result(msg, corr, repair_action="reopen", repair_verified=PERSISTING)
    return _result(
        f"I couldn't confirm that the resume was still open, and I could not reopen {name}.",
        corr,
        repair_action="reopen",
        repair_verified=UNKNOWN,
        ok=False,
    )


def _repair_wrong_file(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    files = list(ctx.candidate_files or [])
    if len(files) < 2:
        return _result(
            "I only had one resume candidate. Which file should I open instead?",
            corr,
            repair_action="ask",
        )
    chosen = files[1]
    path = str(chosen.get("path") or "")
    name = str(chosen.get("name") or path.replace("\\", "/").split("/")[-1] or "that file")
    if not path:
        return _result("I could not find the second newest resume.", corr, repair_action="ask", ok=False)
    if not _reopen_path(orch, path):
        return _result(f"I could not open {name}.", corr, repair_action="reopen", ok=False)
    art = find_artifact()
    computer = _computer(orch)
    report = verify_after_task(computer, art, delay_s=0.2) if art else None
    verified = bool(report and report.state in OPEN_STATES)
    ctx.selected_resume_path = path
    ctx.selected_resume = name
    return _result(
        f"I opened the second newest instead: {name}."
        + (" It's still open." if verified else ""),
        corr,
        repair_action="open_second",
        repair_verified=report.state if report else UNKNOWN,
        ok=verified or True,
    )


def _repair_wrong_page(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    from src.agent.respond import format_current_page, page_identity

    browser = getattr(orch, "browser", None)
    url = ""
    title = ""
    if browser is not None:
        agent = getattr(browser, "agent", None)
        if agent is not None:
            url = str(getattr(agent, "url", "") or "")
            title = str(getattr(agent, "title", "") or "")
    if hasattr(orch, "registry") and orch.registry.get("browser.getCurrentUrl"):
        try:
            hit = orch.registry.execute("browser.getCurrentUrl", {}, purpose="correction")
            data = getattr(hit, "data", None) or {}
            if isinstance(data, dict):
                url = str(data.get("url") or url)
                title = str(data.get("title") or title)
        except Exception:
            pass
    if not url and not title:
        try:
            from src.agent.router import FastIntent

            spoken = orch._run_fast(FastIntent("browser.current_page"), getattr(orch, "_corr_perf", None) or _NullPerf())
            if spoken.get("message"):
                return _result(spoken["message"], corr, repair_action="refresh_page", repair_verified="page")
        except Exception:
            pass
    site = page_identity(url, title)
    ctx.final_browser_url = url
    ctx.final_browser_page = site or title
    line = format_current_page({"url": url, "title": title}) if url or title else ""
    if not line:
        if site and title and site.lower() not in title.lower():
            line = f"You're on {site} — {title}."
        elif site:
            line = f"You're on {site}."
        elif title:
            line = f"You're on {title}."
        else:
            line = "I could not read the current page."
    return _result(line, corr, repair_action="refresh_page", repair_verified="page")


def _repair_switch(orch, ctx, corr: TaskCorrection) -> dict[str, Any]:
    computer = _computer(orch)
    if computer is not None and hasattr(computer, "focus_window"):
        try:
            computer.focus_window(title_contains="Chrome")
        except Exception:
            return _result("I could not switch back to Chrome.", corr, repair_action="focus_chrome", ok=False)
    return _result("I switched back to Chrome.", corr, repair_action="focus_chrome", repair_verified="chrome")


def _reopen_path(orch, path: str) -> bool:
    registry = getattr(orch, "registry", None)
    if registry is None:
        return False
    try:
        result = registry.execute("files.open", {"path": path}, purpose="correction")
    except Exception:
        try:
            result = registry.execute("computer.open_file", {"path": path}, purpose="correction")
        except Exception:
            return False
    if not getattr(result, "success", False):
        return False
    computer = _computer(orch)
    capture_opened_file(computer, path, task_id="correction")
    return True


class _NullPerf:
    def span(self, *_a, **_k):
        return _NullSpan()

    def set(self, *_a, **_k):
        return None

    meta: dict = {}


class _NullSpan:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False
