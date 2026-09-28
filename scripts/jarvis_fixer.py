"""JARVIS self-correction fixer.

JARVIS writes incidents to data/incidents/. This script hands each open incident to a local
Cursor agent working in an isolated git worktree, re-runs the tests itself, and waits for
the user to approve before anything reaches main.

    python scripts/jarvis_fixer.py watch            # keep fixing new incidents
    python scripts/jarvis_fixer.py once             # fix the oldest open incident, then exit
    python scripts/jarvis_fixer.py list
    python scripts/jarvis_fixer.py show <id>
    python scripts/jarvis_fixer.py approve <id>     # merge fix branch into main (clean tree only)
    python scripts/jarvis_fixer.py reject <id>

Needs CURSOR_API_KEY in the environment or in .env. The key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from src.agent.selfheal.incidents import (  # noqa: E402
    APPROVED,
    FIX_FAILED,
    FIX_READY,
    FIXABLE,
    FIXING,
    REJECTED,
    IncidentStore,
)

DATA = REPO / "data"
WORKTREES = REPO.parent / f"{REPO.name}-fixes"
BASE_BRANCH = "main"
DEFAULT_MODEL = "composer-2.5"
TEST_CMD = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"]
TEST_TIMEOUT_S = 1200
RULE_FILE = REPO / ".cursor" / "rules" / "jarvis-debug-bridge.mdc"


def git(*args: str, cwd: Path = REPO, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check)


def load_api_key() -> str:
    key = os.environ.get("CURSOR_API_KEY", "").strip()
    if key:
        return key
    env_file = REPO / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "CURSOR_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


def branch_for(incident_id: str) -> str:
    return f"jarvis-fix/{incident_id}"


def build_prompt(incident_md: str) -> str:
    rule = RULE_FILE.read_text(encoding="utf-8") if RULE_FILE.exists() else ""
    return f"""You are fixing a live JARVIS failure in this repository (a git worktree on its own branch).

Follow the debug-bridge protocol exactly:
{rule}

Also read docs/debug/CURSOR_CHATGPT_DEBUG_BRIDGE.md and docs/debug/ROUTING_PRECEDENCE.md first.

Required steps:
1. Reproduce the failure below in a unit test driven through the real entry point
   (JarvisOrchestrator.handle_user_message or the smallest real component). Run it and confirm it FAILS.
2. Name the earliest wrong decision (use ROUTING_PRECEDENCE.md). If unproven, list two hypotheses
   and prove one with the test or logs.
3. Fix the architectural cause. No one-off phrase regexes, no site-specific special cases.
4. Run `python -m pytest -q tests` and make sure everything passes.
5. Append a dated entry at the top of docs/debug/JARVIS_ROOT_CAUSE_LOG.md (never rewrite history),
   with LIVE marked NOT YET RUN.
6. Commit on the current branch with a focused message. Do NOT push, do NOT switch branches,
   do NOT touch main.

Privacy: tests and docs must not contain real names, emails, phone numbers, addresses, or resume text.
Use sanitized placeholders.

Finish with a short plain-text summary: earliest wrong decision, root cause, files changed, test names.

--- INCIDENT ---
{incident_md}
"""


def ensure_worktree(incident_id: str) -> Path:
    path = WORKTREES / incident_id
    if path.exists():
        return path
    WORKTREES.mkdir(parents=True, exist_ok=True)
    branch = branch_for(incident_id)
    exists = git("rev-parse", "--verify", "--quiet", branch, check=False).returncode == 0
    if exists:
        git("worktree", "add", str(path), branch)
    else:
        git("worktree", "add", "-b", branch, str(path), BASE_BRANCH)
    return path


def run_tests(cwd: Path) -> tuple[bool, str]:
    try:
        proc = subprocess.run(TEST_CMD, cwd=cwd, capture_output=True, text=True, timeout=TEST_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, f"tests timed out after {TEST_TIMEOUT_S}s"
    tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()][-1:] or ["(no output)"]
    return proc.returncode == 0, tail[0]


async def _run_agent_async(worktree: Path, prompt: str, model: str, api_key: str) -> tuple[str, str, str]:
    from cursor_sdk import AsyncClient, LocalAgentOptions

    async with await AsyncClient.launch_bridge(workspace=str(worktree)) as client:
        async with await client.agents.create(
            model=model, api_key=api_key, local=LocalAgentOptions(cwd=str(worktree))
        ) as agent:
            run = await agent.send(prompt)
            print(f"[Fixer] agent={agent.agent_id} run={run.id} started")
            chunks: list[str] = []
            async for text in run.iter_text():
                chunks.append(text)
            result = await run.wait()
            return str(result.status), "".join(chunks)[-4000:], f"{agent.agent_id}/{run.id}"


def run_agent(worktree: Path, prompt: str, model: str, api_key: str) -> tuple[str, str, str]:
    # The sync SDK bridge launcher uses select() on a pipe, which Windows does not support.
    from cursor_sdk import CursorAgentError, CursorSDKError

    try:
        return asyncio.run(_run_agent_async(worktree, prompt, model, api_key))
    except (CursorAgentError, CursorSDKError) as exc:
        return "startup_error", f"{type(exc).__name__}: {exc}", ""


def fix_incident(store: IncidentStore, incident_id: str, model: str, api_key: str) -> None:
    inc = store.load(incident_id)
    if inc is None or inc.status not in FIXABLE:
        return
    store.set_status(inc, FIXING, f"model={model}")
    print(f"[Fixer] {inc.id}: fixing {inc.utterance!r} rules={inc.rules}")
    try:
        worktree = ensure_worktree(inc.id)
    except subprocess.CalledProcessError as exc:
        store.set_status(inc, FIX_FAILED, f"worktree failed: {exc.stderr.strip()[:300]}")
        return
    base = git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
    md = (store.root / inc.id / "incident.md").read_text(encoding="utf-8")

    status, summary, run_ref = run_agent(worktree, build_prompt(md), model, api_key)
    commits = git("rev-list", "--count", f"{BASE_BRANCH}..HEAD", cwd=worktree).stdout.strip()
    dirty = bool(git("status", "--porcelain", cwd=worktree).stdout.strip())
    tests_ok, tests_line = run_tests(worktree)

    fix = {
        "branch": branch_for(inc.id),
        "worktree": str(worktree),
        "base_sha": base,
        "head_sha": git("rev-parse", "HEAD", cwd=worktree).stdout.strip(),
        "commits": commits,
        "uncommitted_changes": dirty,
        "agent_status": status,
        "agent_run": run_ref,
        "automated_tests": f"{'PASS' if tests_ok else 'FAIL'} — {tests_line}",
        "agent_summary": summary.strip()[-1500:],
    }
    ready = status == "finished" and tests_ok and commits not in ("", "0") and not dirty
    store.set_status(inc, FIX_READY if ready else FIX_FAILED, f"agent={status} tests={tests_ok}", **fix)
    verdict = "ready for approval" if ready else "NOT ready"
    print(f"[Fixer] {inc.id}: {verdict}. AUTOMATED={fix['automated_tests']}  LIVE=NOT YET RUN")
    if ready:
        print(f"[Fixer] Review: git -C \"{REPO}\" diff {BASE_BRANCH}...{fix['branch']}")
        print(f"[Fixer] Approve: python scripts/jarvis_fixer.py approve {inc.id}")


def next_fixable(store: IncidentStore):
    pending = [i for i in store.all() if i.status in FIXABLE]
    return min(pending, key=lambda i: i.created_at) if pending else None


def cmd_watch(store: IncidentStore, model: str, once: bool, interval: float) -> int:
    api_key = load_api_key()
    if not api_key:
        print("[Fixer] CURSOR_API_KEY is missing. Add it to the environment or to .env.")
        return 2
    print(f"[Fixer] watching {store.root} (model={model})")
    while True:
        inc = next_fixable(store)
        if inc is not None:
            fix_incident(store, inc.id, model, api_key)
        elif once:
            print("[Fixer] no open incidents.")
        if once:
            return 0
        time.sleep(interval)


def cmd_list(store: IncidentStore) -> int:
    incidents = store.all()
    if not incidents:
        print("No incidents.")
    for inc in incidents:
        print(f"{inc.id}  {inc.status:<10} LIVE={inc.live_test:<11} x{inc.occurrences}  {inc.rules}  {inc.utterance!r}")
    return 0


def cmd_show(store: IncidentStore, incident_id: str) -> int:
    path = store.root / incident_id / "incident.md"
    if not path.exists():
        print(f"Unknown incident {incident_id}")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_approve(store: IncidentStore, incident_id: str) -> int:
    inc = store.load(incident_id)
    if inc is None or inc.status != FIX_READY:
        print(f"{incident_id} is not FIX_READY.")
        return 1
    if git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != BASE_BRANCH:
        print(f"Main tree is not on {BASE_BRANCH}; refusing to merge.")
        return 1
    if git("status", "--porcelain", "--untracked-files=no").stdout.strip():
        print("Main tree has uncommitted changes; commit or stash them first.")
        return 1
    merge = git("merge", "--no-ff", "-m", f"Merge {branch_for(inc.id)} (self-heal {inc.id})", branch_for(inc.id), check=False)
    if merge.returncode != 0:
        git("merge", "--abort", check=False)
        print(f"Merge failed and was aborted:\n{merge.stdout}{merge.stderr}")
        return 1
    tests_ok, line = run_tests(REPO)
    inc.live_test = "NOT YET RUN"
    store.set_status(inc, APPROVED, "merged by user", merged_sha=git("rev-parse", "HEAD").stdout.strip(),
                     main_tests=f"{'PASS' if tests_ok else 'FAIL'} — {line}")
    git("worktree", "remove", "--force", str(WORKTREES / inc.id), check=False)
    print(f"Merged. AUTOMATED on main: {'PASS' if tests_ok else 'FAIL'} — {line}")
    print(f"LIVE: NOT YET RUN. Restart JARVIS and say: {inc.utterance!r}")
    print("JARVIS will mark it VERIFIED (LIVE=PASS) or REOPENED (LIVE=FAIL) on that replay.")
    return 0 if tests_ok else 1


def cmd_reject(store: IncidentStore, incident_id: str) -> int:
    inc = store.load(incident_id)
    if inc is None:
        print(f"Unknown incident {incident_id}")
        return 1
    git("worktree", "remove", "--force", str(WORKTREES / inc.id), check=False)
    git("branch", "-D", branch_for(inc.id), check=False)
    store.set_status(inc, REJECTED, "rejected by user")
    print(f"Rejected {inc.id}; worktree and branch removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["watch", "once", "list", "show", "approve", "reject"])
    ap.add_argument("incident_id", nargs="?")
    ap.add_argument("--model", default=os.environ.get("JARVIS_FIXER_MODEL", DEFAULT_MODEL))
    ap.add_argument("--interval", type=float, default=20.0)
    args = ap.parse_args(argv)
    store = IncidentStore(DATA / "incidents")

    if args.command in ("watch", "once"):
        return cmd_watch(store, args.model, args.command == "once", args.interval)
    if args.command == "list":
        return cmd_list(store)
    if not args.incident_id:
        ap.error(f"{args.command} needs an incident id")
    return {"show": cmd_show, "approve": cmd_approve, "reject": cmd_reject}[args.command](store, args.incident_id)


if __name__ == "__main__":
    raise SystemExit(main())
