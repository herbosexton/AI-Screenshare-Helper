"""AgentPlanner — one structured plan, not a tool-call-per-turn conversation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import ValidationError

from src.agent.phase6.binding import (
    DEFERRED_ARGUMENTS,
    canonical_arguments,
    canonical_site,
    is_placeholder,
)
from src.agent.phase6.models import AgentStep, AgentStepStatus, AgentTask
from src.agent.phase6.tool_catalog import is_state_changing


PLANNER_SYSTEM = """Plan how a Windows desktop agent reaches the user's goal.

Reply with ONE JSON object, nothing else:
{"goal":"<restatement>","steps":[{"id":"step_1","description":"<short action>","tool":"<tool or null>","arguments":{}}]}

Rules:
- Cover the whole goal. Every action the user asked for needs its own step.
- tool must be a name from the list, or null for a reasoning/comparison step.
- One step per action the user asked for. Each description is one concrete action, under 10 words.
- Omit "arguments" when you do not know the values. Never guess a path, and never
  add a filter the user did not ask for.
- No login or CAPTCHA steps; the user handles those.
- No prose, no markdown, no reasoning.

Worked example. "Find my newest resume, open it, then switch back to Chrome and tell me
what page I'm on" is four actions. Search only authorized folders for resume/CV names —
not the newest arbitrary file. Focus the user's existing desktop Chrome; do not launch
a managed browser. Read the current page after Chrome is focused:
{"goal":"Open the newest resume and report the Chrome page","steps":[
{"id":"step_1","description":"Find newest resume","tool":"files.find_recent","arguments":{"name_contains":"resume"}},
{"id":"step_2","description":"Open the resume","tool":"files.open"},
{"id":"step_3","description":"Focus existing Chrome","tool":"computer.focus_window","arguments":{"title_contains":"Chrome"}},
{"id":"step_4","description":"Read the current page","tool":"browser.getCurrentUrl"}]}

Worked example. "Look at the job page I have open, find my newest resume, compare the resume to the job requirements, and tell me what I'm missing" uses the existing desktop Chrome page, finds a resume/CV by name, reads its text, then compares. Do not only open the file. Do not launch a managed browser:
{"goal":"Compare the newest resume to the open job page","steps":[
{"id":"step_1","description":"Read the open job page","tool":"browser.getPageState"},
{"id":"step_2","description":"Find newest resume","tool":"files.find_recent","arguments":{"name_contains":"resume"}},
{"id":"step_3","description":"Read the resume","tool":"files.read"},
{"id":"step_4","description":"Compare and list gaps","tool":null}]}"""


# Verbs that open a real action clause. Used only to notice that a goal asking for three
# things came back as a one-step plan. A miss costs nothing and a false hit costs one
# extra ask, so the set stays narrow and the check stays advisory.
_ACTION_VERBS = frozenset(
    """open find search go navigate switch close read compare tell show save download
    click type look bring start check get put move copy send apply fill select take make
    create summarize list play pull set focus maximize minimize scroll press upload
    return""".split()
)

_CLAUSE_SPLIT = re.compile(
    r",\s*(?:and\s+)?(?:then\s+)?|\s+and\s+then\s+|\s+then\s+|\s+and\s+", re.I
)

# Never demand more steps than the validator will accept.
MAX_COVERAGE_FLOOR = 20


def required_step_floor(goal: str) -> int:
    """Fewest steps that could cover the goal, counted from its action clauses."""
    actions = 0
    for clause in _CLAUSE_SPLIT.split(goal or ""):
        words = [w.strip("'\"") for w in clause.lower().split()[:2]]
        # Two words, because "let's find ..." and "please open ..." both lead with filler.
        if any(w in _ACTION_VERBS for w in words):
            actions += 1
    return max(1, min(actions, MAX_COVERAGE_FLOOR))


@dataclass
class Plan:
    goal: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    raw: str = ""


class PlanValidationError(Exception):
    def __init__(self, code: str, message: str, raw: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        # What the model actually said. Without it a rejection reports only that the plan
        # was wrong, never how, and every diagnosis starts by re-running the planner.
        self.raw = raw


class PlanValidator:
    """Never execute planner output blindly."""

    MAX_STEPS = 25

    def __init__(self, registry):
        self.registry = registry

    def _resolve_tool(self, name: str) -> str:
        """Accept an unqualified name only when exactly one real tool matches."""
        # Models sometimes copy the signature straight out of the prompt: "browser.click(query)".
        name = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
        # A step offering a choice of tools takes the first: one step runs one tool.
        if name.startswith("[") and name.endswith("]"):
            name = name[1:-1].split(",")[0].strip().strip("'\"")
        if self.registry.get(name) is not None:
            return name
        leaf = name.split(".")[-1]
        matches = [t.name for t in self.registry.list_tools() if t.name.split(".")[-1] == leaf]
        return matches[0] if len(matches) == 1 else name

    def _check_arguments(self, sid: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Reject bad values. Allow values the executor binds from task context later."""
        model = getattr(self.registry.get(tool), "parameters_model", None)
        if model is None:
            return arguments
        arguments, unresolved = canonical_arguments(arguments, set(model.model_fields))
        if unresolved:
            raise PlanValidationError(
                "BAD_ARGUMENTS",
                f"Step {sid} passes {', '.join(sorted(unresolved))} to {tool}, which takes "
                f"{', '.join(sorted(model.model_fields))}.",
            )
        arguments = _clamp_numbers(model, arguments)
        if isinstance(arguments.get("url"), str):
            arguments["url"] = canonical_site(arguments["url"])
        try:
            model.model_validate(arguments)
            return arguments
        except ValidationError as e:
            missing = {str(err["loc"][0]) for err in e.errors() if err.get("type") == "missing"}
            bad = [err for err in e.errors() if err.get("type") != "missing"]
            unbindable = missing - DEFERRED_ARGUMENTS.get(tool, set())
            if bad:
                detail = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in bad)
                raise PlanValidationError(
                    "BAD_ARGUMENTS", f"Step {sid} arguments rejected by {tool}: {detail}"
                ) from e
            if unbindable:
                raise PlanValidationError(
                    "BAD_ARGUMENTS",
                    f"Step {sid} is missing {', '.join(sorted(unbindable))} for {tool}.",
                ) from e
            return arguments

    def validate(self, plan: Plan, *, goal: str = "") -> list[AgentStep]:
        if not plan.steps:
            raise PlanValidationError("EMPTY_PLAN", "Planner returned no steps.")
        if len(plan.steps) > self.MAX_STEPS:
            raise PlanValidationError(
                "PLAN_TOO_LONG", f"Planner returned {len(plan.steps)} steps."
            )

        seen: set[str] = set()
        steps: list[AgentStep] = []
        for order, raw in enumerate(plan.steps):
            if isinstance(raw, str):
                raw = _step_from_text(raw)
            if not isinstance(raw, dict):
                raise PlanValidationError("BAD_STEP", "Step is not an object.")
            sid = str(raw.get("id") or f"step_{order + 1}")
            if sid in seen:
                raise PlanValidationError("DUPLICATE_STEP_ID", f"Duplicate step id {sid}.")
            seen.add(sid)

            description = str(raw.get("description") or "").strip()
            if not description:
                raise PlanValidationError("MISSING_DESCRIPTION", f"Step {sid} has no description.")
            if _LOOKS_SERIALIZED.search(description):
                # Unparsed JSON standing in for a step. Its tool is in there somewhere and
                # is not being read, so running this plan skips whatever it asked for.
                raise PlanValidationError(
                    "BAD_STEP", f"Step {sid} has JSON in place of a description."
                )

            tool = raw.get("tool")
            tool = None if tool in (None, "", "null", "none") else str(tool)
            arguments = raw.get("arguments")
            if arguments is None:
                arguments = raw.get("tool_arguments") or {}
            if not isinstance(arguments, dict):
                # A string or list where an object belongs means the model had no real
                # values to give. Drop it rather than throw the whole plan away.
                arguments = {}

            if tool is not None:
                tool = self._resolve_tool(tool)
                if self.registry.get(tool) is None:
                    raise PlanValidationError("UNKNOWN_TOOL", f"Step {sid} uses unknown tool {tool!r}.")
                # A null or placeholder argument is the model saying "I don't know this
                # value yet", which is the same as omitting it.
                arguments = {k: v for k, v in arguments.items() if not is_placeholder(v)}
                arguments = self._check_arguments(sid, tool, arguments)

            deps_raw = raw.get("depends_on") or raw.get("dependencies") or []
            if isinstance(deps_raw, str):
                deps_raw = [deps_raw]
            deps = [str(d) for d in deps_raw if str(d)]
            for dep in deps:
                if dep == sid:
                    raise PlanValidationError("CYCLE", f"Step {sid} depends on itself.")
                if dep not in seen:
                    raise PlanValidationError(
                        "BAD_DEPENDENCY", f"Step {sid} depends on {dep!r}, which is not an earlier step."
                    )

            steps.append(
                AgentStep(
                    id=sid,
                    order=order,
                    description=description[:160],
                    status=AgentStepStatus.PENDING,
                    preferred_tool=tool,
                    tool_arguments=arguments,
                    dependencies=deps,
                    state_changing=bool(tool and is_state_changing(tool)),
                )
            )

        acting = sum(1 for s in steps if s.preferred_tool is not None)
        if not acting:
            raise PlanValidationError(
                "NO_ACTIONABLE_STEP", "Plan has no tool-backed step; nothing would happen."
            )
        if acting * 2 < len(steps):
            # A tool-less step is for reasoning, and real goals need one or two at most.
            # A plan that is mostly reasoning is a garbled plan: those steps complete on
            # their own, so it finishes clean while doing almost none of the work.
            raise PlanValidationError(
                "NO_ACTIONABLE_STEP",
                f"Only {acting} of {len(steps)} steps use a tool; most of the plan does nothing.",
            )
        return steps


def _clamp_numbers(model: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """A number outside the allowed range is a units mistake, not a different intent."""
    schema = model.model_json_schema().get("properties") or {}
    out = dict(arguments)
    for key, value in arguments.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        spec = schema.get(key) or {}
        low, high = spec.get("minimum"), spec.get("maximum")
        if low is not None and value < low:
            out[key] = low
        elif high is not None and value > high:
            out[key] = high
    return out


def extract_plan_json(text: str) -> Plan:
    raw = (text or "").strip()
    if not raw:
        raise PlanValidationError("EMPTY_OUTPUT", "Planner returned nothing.")
    body = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    data: Any = None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(body[start : end + 1])
            except json.JSONDecodeError as e:
                raise PlanValidationError("UNPARSEABLE_PLAN", f"Planner output is not JSON: {e}") from e
    if not isinstance(data, dict):
        raise PlanValidationError("UNPARSEABLE_PLAN", "Planner output is not a JSON object.")
    steps = _normalize_steps(data.get("steps"))
    if steps is None:
        raise PlanValidationError("MISSING_STEPS", "Planner output has no steps array.")
    return Plan(goal=str(data.get("goal") or ""), steps=steps, raw=raw)


def _normalize_steps(steps: Any) -> Optional[list[dict[str, Any]]]:
    """Absorb the shapes small models reach for. Structure only — never tool semantics."""
    if isinstance(steps, dict):
        # {"step_1": {...}} instead of a list.
        steps = [
            {"id": k, **v} if isinstance(v, dict) else {"id": k, "description": str(v)}
            for k, v in steps.items()
        ]
    if not isinstance(steps, list):
        return None
    out: list[dict[str, Any]] = []
    for raw in steps:
        if isinstance(raw, list) and len(raw) == 1:
            raw = raw[0]
        if isinstance(raw, str):
            raw = _step_from_text(raw)
        out.append(raw)
    return out


# A description that is really a piece of the step's own structure rather than an action:
# `{"tool": ...}`, but also the field names arriving as separate steps — `step_3`,
# `description_open another tab`, `tool_browser.new_tab`, `arguments_null`. Those four are
# one step that came apart, and each one left behind runs nothing while reporting success.
_LOOKS_SERIALIZED = re.compile(
    r'"\s*:|^\s*[{\[]|^\s*(?:step|id|tool|arguments?|description)\s*[_:-]|_null\s*$',
    re.I,
)

# `{id":"5"` — a key that lost its opening quote. Idempotent on keys already quoted.
_UNQUOTED_KEY = re.compile(r'([{,]\s*)"?([A-Za-z_][A-Za-z0-9_]*)"?\s*:')


def _step_from_text(text: str) -> dict[str, Any]:
    """Recover a step the model serialized as a string instead of an object.

    Wrapping it as a bare description is what a malformed step used to become: a plan that
    counted five steps, validated, and quietly dropped the tool of the last one, so the
    action the user asked for never ran and the task still reported success.
    """
    start = text.find("{")
    if start != -1:
        body = text[start:]
        for candidate in (body, _UNQUOTED_KEY.sub(r'\1"\2":', body)):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {"description": text}


def tool_signatures(tool_schemas: list[dict[str, Any]]) -> list[str]:
    """`browser.goto(url)` — compact and unambiguous about argument names."""
    lines: list[str] = []
    for schema in tool_schemas:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if not fn:
            continue
        params = (fn.get("parameters") or {}).get("properties") or {}
        lines.append(f"- {fn['name']}({', '.join(list(params)[:6])})")
    return sorted(lines)


class AgentPlanner:
    """Builds a structured plan. Called once per task, plus bounded replans."""

    def __init__(self, provider, registry, *, timeout_s: float = 45.0):
        self.provider = provider
        self.registry = registry
        self.validator = PlanValidator(registry)
        # Bounded, and deliberately separate from the chat timeout: planning generates
        # more tokens than a spoken reply, and on CPU-only inference that is 15-25 s.
        self.timeout_s = timeout_s

    # Two repairs, not one. A 7B model on a five-action goal gets it wrong in a different
    # way each time, and a repair pass costs ~10 s against the ~45 s first attempt.
    MAX_REPAIRS = 2

    def plan(
        self,
        task: AgentTask,
        *,
        tool_schemas: list[dict[str, Any]],
        replan: bool = False,
        failure_note: str = "",
        perf: Optional[Any] = None,
    ) -> list[AgentStep]:
        prompt = self._build_prompt(task, tool_schemas, replan=replan, failure_note=failure_note)
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        # Two errors, two jobs: the first says what went wrong for the caller, the latest
        # says what to fix next. Repairing against a stale error asks the model to correct
        # something it already corrected, and it undoes the fix to comply.
        first_error: Optional[PlanValidationError] = None
        recent_error: Optional[PlanValidationError] = None
        best: Optional[list[AgentStep]] = None
        shortfall = ""
        # On a replan the goal is the whole request but the plan is only the work left,
        # so counting clauses would demand steps that are already done.
        floor = 1 if replan else required_step_floor(task.normalized_goal or task.original_request)

        for attempt in range(self.MAX_REPAIRS + 1):
            if attempt:
                # Repair, not replan: same goal, plus exactly what was wrong last time.
                messages = messages[:2] + [
                    {
                        "role": "user",
                        "content": self._repair_prompt(
                            recent_error, tool_schemas, shortfall, floor
                        ),
                    }
                ]
            steps = self._attempt(task, messages, replan=replan, perf=perf)
            if isinstance(steps, PlanValidationError):
                first_error = first_error or steps
                recent_error = steps
                shortfall = ""  # A rejected plan has no coverage to complain about.
                print(f"[Phase6] plan rejected ({steps.code}): {steps.message}")
                if steps.raw:
                    print(f"[Phase6]   model said: {steps.raw}")
                if perf is not None:
                    perf.set("plan_repair_attempts", attempt + 1)
                continue
            if best is None or len(steps) > len(best):
                best = steps
            if len(best) >= floor:
                if attempt and perf is not None:
                    perf.set("plan_repaired", True)
                return best
            # A well-formed plan that answers part of the goal. Worth asking once more,
            # never worth failing over: a short plan still beats no plan.
            shortfall = (
                f"That plan had {len(steps)} step(s), but the goal asks for at least {floor} "
                "separate actions and every one of them needs its own step. "
                "Return the whole plan."
            )
            print(f"[Phase6] plan covers {len(steps)} of {floor} actions; asking again")
            if perf is not None:
                perf.set("plan_undercovered", True)

        if best is not None:
            return best
        raise first_error  # type: ignore[misc]

    # Codes where the model ran out of room rather than picked the wrong tool.
    _LENGTH_CODES = {"UNPARSEABLE_PLAN", "PLAN_TOO_LONG", "EMPTY_OUTPUT"}

    def _repair_prompt(
        self,
        error: Optional[PlanValidationError],
        tool_schemas: list[dict[str, Any]],
        shortfall: str = "",
        floor: int = 1,
    ) -> str:
        # An incomplete plan is well-formed, so repeating the tool list teaches nothing.
        if shortfall:
            return shortfall
        message = error.message if error else ""
        if error and error.code in self._LENGTH_CODES:
            # Ask for the count the goal actually needs. A fixed "at most 3" contradicts the
            # coverage rule for a five-action goal, and the model resolves the contradiction
            # by dropping tools until nothing is left to run.
            want = max(1, min(floor, self.validator.MAX_STEPS))
            return (
                f"That plan was rejected: {message}\n"
                f"Return ONE complete JSON object with exactly {want} step(s). "
                'Every step needs a "tool" from the list unless it is pure comparison. '
                "Keep every description under 8 words and omit arguments you are unsure of."
            )
        if error and error.code == "NO_ACTIONABLE_STEP":
            return (
                f"That plan was rejected: {message}\n"
                'Every step that does something needs a "tool" chosen from:\n'
                + "\n".join(tool_signatures(tool_schemas))
            )
        # The tool list is repeated because that is what the model got wrong.
        return (
            f"That plan was rejected: {message}\n"
            'Return a corrected JSON object. Every "tool" must be exactly one of:\n'
            + "\n".join(tool_signatures(tool_schemas))
            + '\nWrite the bare name, e.g. "browser.click", not "browser.click(query)".'
        )

    def _attempt(self, task, messages, *, replan: bool, perf):
        task.planner_calls += 1
        t0 = time.perf_counter()
        response = self._chat(messages)
        elapsed = (time.perf_counter() - t0) * 1000
        if perf is not None:
            perf.add("Planner LLM", elapsed)
            perf.set("planner_input_tokens", max(1, len(json.dumps(messages)) // 4))
            perf.set("planner_output_tokens", max(1, len(response.content or "") // 4))
        print(f"[Phase6] planner replied in {elapsed:.0f} ms (replan={replan})")

        try:
            plan = extract_plan_json(response.content or "")
            if plan.goal and not task.normalized_goal:
                task.normalized_goal = plan.goal[:160]
            return self.validator.validate(plan, goal=task.normalized_goal)
        except PlanValidationError as e:
            e.raw = " ".join((response.content or "").split())[:240]
            return e

    # A validated 5-step plan measures ~180 tokens; a 20-step plan needs ~900.
    # 1200 leaves room for verbose argument objects on big goals while still
    # cutting off a truly runaway model inside the timeout.
    MAX_PLAN_TOKENS = 1200

    def _chat(self, messages: list[dict[str, Any]]):
        """Ask for constrained JSON where the provider supports it."""
        try:
            return self.provider.chat(
                messages,
                tools=None,
                json_mode=True,
                max_tokens=self.MAX_PLAN_TOKENS,
                timeout_s=self.timeout_s,
            )
        except TypeError:
            return self.provider.chat(messages, tools=None)

    def _build_prompt(
        self,
        task: AgentTask,
        tool_schemas: list[dict[str, Any]],
        *,
        replan: bool,
        failure_note: str,
    ) -> str:
        # Signatures, not prose. Argument names are what the planner actually gets wrong,
        # and they cost a fraction of the tokens a description does.
        parts = [
            f"Goal: {task.normalized_goal or task.original_request}",
            "",
            "Tools:",
            "\n".join(tool_signatures(tool_schemas)) or "(none)",
        ]
        state = task.context.compact()
        if state:
            parts += ["", "Current state:", json.dumps(state, default=str)[:1200]]
        if replan:
            done = task.completed_descriptions()
            parts += [
                "",
                "Already completed (do not repeat):",
                json.dumps(done[-6:], default=str),
                "",
                f"Why replanning: {failure_note[:300]}",
                "Plan only the REMAINING work.",
            ]
        return "\n".join(parts)
