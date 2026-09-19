"""Full system verification for Phase 6."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def routing_checks():
    print("=== ROUTING CHECKS ===")
    from src.agent.router import FastCommandRouter
    from src.agent.utterance import classify_utterance
    from src.agent.orchestrator import AgentOrchestrator

    r = FastCommandRouter()
    passed = 0
    failed = 0

    # Fast-path commands that must NOT go to the planner
    fast_tests = [
        ("open chrome", "browser.open"),
        ("open google calendar", "browser.open_and_goto"),
        ("now open chatgpt", "browser.open_and_goto"),
        ("open tradingview", "browser.open_and_goto"),
        ("open notepad", "computer.open_application"),
        ("go to youtube", "browser.open_and_goto"),
        ("please open gmail", "browser.open_and_goto"),
    ]
    for text, expected in fast_tests:
        result = r.route(text, skip_tasks=True)
        action = result.action if result else "None"
        ok = expected in action
        print(f"  {'PASS' if ok else 'FAIL'}  fast: \"{text}\" -> {action}")
        passed += ok
        failed += not ok

    # Multi-step goals that MUST go to the planner
    multi_tests = [
        "Open Chrome, go to google.com, open another tab, go to tradingview, then switch back to chatgpt",
        "Open Chrome, go to google.com, open another tab, go to tradingview, thenopen another tab and open chatgbt",
        "Find my newest resume and compare it to the job page I have open",
    ]
    for text in multi_tests:
        k = classify_utterance(text)
        is_goal = AgentOrchestrator._is_agent_goal(k, text)
        ok = is_goal is True
        print(f"  {'PASS' if ok else 'FAIL'}  planner: \"{text[:60]}...\" -> {is_goal}")
        passed += ok
        failed += not ok

    # Simple commands that must NOT go to the planner
    simple_tests = [
        "Open Chrome",
        "Go back",
        "What page am I on?",
        "Open a browser and go to example.com",
    ]
    for text in simple_tests:
        k = classify_utterance(text)
        is_goal = AgentOrchestrator._is_agent_goal(k, text)
        ok = is_goal is False
        print(f"  {'PASS' if ok else 'FAIL'}  fast: \"{text}\" -> planner={is_goal}")
        passed += ok
        failed += not ok

    return passed, failed


def transcript_checks():
    print("\n=== TRANSCRIPT / CORRECTION CHECKS ===")
    from src.agent.transcript import TranscriptNormalizer

    n = TranscriptNormalizer()
    passed = 0
    failed = 0

    # 'pause' must not be corrected to 'phase'
    r = n.normalize("pause", tasks=[{"text": "finish phase 6"}])
    ok = "phase" not in r.normalized
    print(f"  {'PASS' if ok else 'FAIL'}  'pause' not corrected to 'phase': normalized='{r.normalized}'")
    passed += ok
    failed += not ok

    # 'chatgbt' should be corrected to 'chatgpt'
    r = n.normalize("open chatgbt")
    ok = "chatgpt" in r.corrected.lower()
    print(f"  {'PASS' if ok else 'FAIL'}  'chatgbt' -> 'chatgpt': corrected='{r.corrected}'")
    passed += ok
    failed += not ok

    # 'resume' must not be corrected
    r = n.normalize("resume", tasks=[{"text": "review resume draft"}])
    ok = r.normalized.strip() == "resume"
    print(f"  {'PASS' if ok else 'FAIL'}  'resume' stays 'resume': normalized='{r.normalized}'")
    passed += ok
    failed += not ok

    return passed, failed


def provider_checks():
    print("\n=== PROVIDER CHECKS ===")
    passed = 0
    failed = 0

    # Check hybrid routing
    from src.agent.providers.ollama import LocalOllamaProvider
    from src.agent.providers.openai import OpenAIProvider
    from src.agent.providers.hybrid import HybridProvider

    local = LocalOllamaProvider(auto_start=False)
    cloud = OpenAIProvider(api_key="test-key")
    hybrid = HybridProvider(local=local, cloud=cloud)

    # Chat goes local
    p, reason = hybrid._pick([{"role": "user", "content": "hello"}])
    ok = p is local
    print(f"  {'PASS' if ok else 'FAIL'}  Chat -> local: {reason}")
    passed += ok
    failed += not ok

    # json_mode goes cloud
    p, reason = hybrid._pick([{"role": "user", "content": "plan"}], json_mode=True)
    ok = p is cloud
    print(f"  {'PASS' if ok else 'FAIL'}  json_mode -> cloud: {reason}")
    passed += ok
    failed += not ok

    # Planner system prompt goes cloud
    msgs = [
        {"role": "system", "content": "Plan how a Windows desktop agent reaches the goal."},
        {"role": "user", "content": "open 3 tabs"},
    ]
    p, reason = hybrid._pick(msgs)
    ok = p is cloud
    print(f"  {'PASS' if ok else 'FAIL'}  Planner prompt -> cloud: {reason}")
    passed += ok
    failed += not ok

    # Cloud API key check
    key = os.environ.get("OPENAI_API_KEY", "")
    ok = len(key) > 10
    print(f"  {'PASS' if ok else 'FAIL'}  OPENAI_API_KEY set: {'yes' if ok else 'NO'}")
    passed += ok
    failed += not ok

    # Live cloud ping
    if key:
        try:
            real_cloud = OpenAIProvider()
            r = real_cloud.chat([{"role": "user", "content": "Say OK"}], max_tokens=5)
            ok = len(r.content) > 0
            print(f"  {'PASS' if ok else 'FAIL'}  Cloud GPT-4o responds: '{r.content}'")
        except Exception as e:
            ok = False
            print(f"  FAIL  Cloud GPT-4o error: {e}")
        passed += ok
        failed += not ok

    return passed, failed


def planner_checks():
    print("\n=== PLANNER CHECKS ===")
    from src.agent.phase6.planner import required_step_floor, PlanValidator, MAX_COVERAGE_FLOOR

    passed = 0
    failed = 0

    # Step floor for multi-action goals
    for text, expected_min in [
        ("Open Chrome, go to google, open another tab, go to tradingview, switch to chatgpt", 5),
        ("Find my resume and open it", 2),
        ("Open Chrome", 1),
    ]:
        floor = required_step_floor(text)
        ok = floor >= expected_min
        print(f"  {'PASS' if ok else 'FAIL'}  step_floor(\"{text[:50]}...\") = {floor} (need >= {expected_min})")
        passed += ok
        failed += not ok

    # MAX_COVERAGE_FLOOR raised
    ok = MAX_COVERAGE_FLOOR >= 20
    print(f"  {'PASS' if ok else 'FAIL'}  MAX_COVERAGE_FLOOR = {MAX_COVERAGE_FLOOR} (need >= 20)")
    passed += ok
    failed += not ok

    # MAX_STEPS raised
    ok = PlanValidator.MAX_STEPS >= 25
    print(f"  {'PASS' if ok else 'FAIL'}  PlanValidator.MAX_STEPS = {PlanValidator.MAX_STEPS} (need >= 25)")
    passed += ok
    failed += not ok

    return passed, failed


def control_checks():
    print("\n=== CONTROL COMMAND CHECKS ===")
    from src.agent.orchestrator import match_control_command

    passed = 0
    failed = 0

    for text, expected in [
        ("pause", "pause"),
        ("resume", "continue"),
        ("cancel", "cancel"),
        ("skip", "skip"),
        ("stop", "stop"),
        ("continue", "continue"),
        ("open chrome", None),
    ]:
        result = match_control_command(text)
        ok = result == expected
        print(f"  {'PASS' if ok else 'FAIL'}  control(\"{text}\") = {result} (expected {expected})")
        passed += ok
        failed += not ok

    return passed, failed


def main():
    total_pass = 0
    total_fail = 0

    for check in [routing_checks, transcript_checks, provider_checks, planner_checks, control_checks]:
        p, f = check()
        total_pass += p
        total_fail += f

    print(f"\n{'=' * 50}")
    print(f"TOTAL: {total_pass} passed, {total_fail} failed")
    if total_fail == 0:
        print("ALL CHECKS PASSED")
    else:
        print(f"{total_fail} CHECK(S) FAILED")
    return total_fail


if __name__ == "__main__":
    raise SystemExit(main())
