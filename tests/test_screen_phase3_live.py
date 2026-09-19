"""Live Phase 3 acceptance: real window, real mouse, optional vision fallback.

These tests must FAIL if the click/dialog/error/change path does not work.
They must NOT skip solely because UI Automation timed out.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.agent.computer.controller import ComputerController
from src.agent.providers.ollama import LocalOllamaProvider
from src.agent.screen.understanding import ScreenUnderstandingService
from src.agent.screen.vision import LocalVisionProvider, ollama_ps
from src.capture.screen import ScreenCapture

HARNESS = Path(__file__).resolve().parent / "fixtures" / "phase3_live_harness.py"
TITLE = {
    "click": "JarvisPhase3LiveClick",
    "dialog": "JarvisPhase3Dialog",
    "error": "JarvisPhase3Error",
    "change": "JarvisPhase3Change",
}


def _live_service() -> ScreenUnderstandingService:
    provider = LocalOllamaProvider(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:7b",
        vision_model="llava",
        timeout_s=25,
        auto_start=True,
    )
    vision = LocalVisionProvider(provider, timeout_s=40.0, model="llava")
    return ScreenUnderstandingService(ScreenCapture({"monitors": "all"}), ComputerController(), vision)


class LiveHarness:
    def __init__(self, tmp_path: Path, mode: str):
        self.mode = mode
        self.status_path = tmp_path / f"p3_{mode}_status.json"
        self.cmd_path = tmp_path / f"p3_{mode}_cmd.json"
        self.cmd_path.write_text("", encoding="utf-8")
        self.proc: subprocess.Popen | None = None

    def start(self) -> dict:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(HARNESS),
                "--mode",
                self.mode,
                "--status",
                str(self.status_path),
                "--cmd",
                str(self.cmd_path),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        deadline = time.time() + 8
        last = {}
        while time.time() < deadline:
            if self.status_path.exists():
                try:
                    last = json.loads(self.status_path.read_text(encoding="utf-8"))
                    if last.get("ready") and last.get("hwnd"):
                        time.sleep(0.2)
                        return last
                except json.JSONDecodeError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"Harness exited early: {self.proc.returncode}")
            time.sleep(0.05)
        raise TimeoutError(f"Harness did not become ready: {last}")

    def status(self) -> dict:
        # A missing or unreadable snapshot means we raced the writer, not that the
        # harness has no state. Never let that masquerade as "the click did nothing".
        deadline = time.time() + 1.5
        while time.time() < deadline:
            try:
                return json.loads(self.status_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, FileNotFoundError, OSError):
                time.sleep(0.05)
        return {}

    def command(self, **cmd) -> dict:
        before = self.status()
        self.cmd_path.write_text(json.dumps(cmd), encoding="utf-8")
        deadline = time.time() + 5
        while time.time() < deadline:
            st = self.status()
            if cmd.get("op") == "move":
                old_r = before.get("window_rect") or []
                new_r = st.get("window_rect") or []
                if old_r and new_r and new_r != old_r:
                    time.sleep(0.2)
                    return st
            elif cmd.get("op") == "set_b" and "Submitted" in (st.get("title") or ""):
                time.sleep(0.15)
                return st
            elif cmd.get("op") not in {"move", "set_b"}:
                return st
            time.sleep(0.05)
        return self.status()

    def close(self) -> None:
        try:
            self.cmd_path.write_text(json.dumps({"op": "quit"}), encoding="utf-8")
            if self.proc:
                self.proc.wait(timeout=4)
        except Exception:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()


@pytest.fixture
def harness_factory(tmp_path):
    running: list[LiveHarness] = []

    def _make(mode: str) -> LiveHarness:
        h = LiveHarness(tmp_path, mode)
        h.start()
        running.append(h)
        return h

    yield _make
    for h in running:
        h.close()


def test_live_named_continue_click(harness_factory):
    h = harness_factory("click")
    svc = _live_service()
    t0 = time.perf_counter()
    result = svc.visual_click("Continue", window_title=TITLE["click"])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.35)
    st = h.status()
    print(
        f"[LIVE] visual_click {elapsed_ms:.1f} ms source={result.get('source')} "
        f"uia_timeout={result.get('uia_timed_out')} xy=({result.get('x')},{result.get('y')}) "
        f"clicked={st.get('clicked')} verified={result.get('verified')}"
    )
    assert result.get("ok") is True, result
    assert st.get("clicked", 0) >= 1, f"physical click missed the Continue button: {result} status={st}"
    assert "Clicked" in (st.get("title") or "") or st.get("label") == "Clicked OK"


def test_live_stale_target_relocated_before_click(harness_factory):
    h = harness_factory("click")
    svc = _live_service()
    found = svc.find_element("Continue", window_title=TITLE["click"])
    assert found.get("ok") is True, found
    old_x, old_y = int(found["x"]), int(found["y"])
    old_rect = list(h.status().get("window_rect") or [])
    t0 = time.perf_counter()
    moved = h.command(op="move", dx=430, dy=110)
    new_rect = list(moved.get("window_rect") or h.status().get("window_rect") or [])
    assert new_rect and new_rect != old_rect, f"window did not move: {old_rect} -> {new_rect}"
    result = svc.visual_click("Continue", window_title=TITLE["click"])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    time.sleep(0.35)
    st = h.status()
    print(
        f"[LIVE] stale_relocated {elapsed_ms:.1f} ms old=({old_x},{old_y}) "
        f"new=({result.get('x')},{result.get('y')}) relocated={result.get('relocated')} "
        f"clicked={st.get('clicked')}"
    )
    assert result.get("ok") is True, result
    assert st.get("clicked", 0) >= 1, f"relocated click missed Continue: {result} status={st}"
    assert (int(result["x"]), int(result["y"])) != (old_x, old_y)


def test_live_dialog_understanding(harness_factory):
    h = harness_factory("dialog")
    svc = _live_service()
    t0 = time.perf_counter()
    dlg = svc.read_dialog(window_title=TITLE["dialog"])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    blob = " ".join(
        [
            str(dlg.get("title") or ""),
            str(dlg.get("message") or ""),
            " ".join(str(b) for b in (dlg.get("buttons") or [])),
        ]
    ).lower()
    print(f"[LIVE] dialog {elapsed_ms:.1f} ms source={dlg.get('source')} data={dlg}")
    assert dlg.get("ok") is True, dlg
    assert "save" in blob and "report.txt" in blob
    buttons = [str(b).lower() for b in (dlg.get("buttons") or [])]
    if not buttons:
        assert "cancel" in blob
    else:
        assert any("save" in b for b in buttons)
        assert any("cancel" in b for b in buttons)
    h.status()  # harness still alive


def test_live_error_understanding(harness_factory):
    h = harness_factory("error")
    svc = _live_service()
    t0 = time.perf_counter()
    err = svc.read_error(window_title=TITLE["error"])
    elapsed_ms = (time.perf_counter() - t0) * 1000
    msg = (err.get("message") or "").lower()
    print(f"[LIVE] error {elapsed_ms:.1f} ms source={err.get('source')} message={err.get('message')}")
    assert err.get("ok") is True, err
    assert "payment gateway timeout" in msg or "504" in msg
    h.status()


def test_live_before_after_change(harness_factory):
    h = harness_factory("change")
    svc = _live_service()
    svc.remember_before()
    t0 = time.perf_counter()
    h.command(op="set_b")
    cmp_ = svc.compare()
    elapsed_ms = (time.perf_counter() - t0) * 1000
    blob = " ".join(
        [
            str(cmp_.get("summary") or ""),
            " ".join(cmp_.get("added_text") or []),
            str(h.status().get("title") or ""),
        ]
    )
    print(f"[LIVE] change {elapsed_ms:.1f} ms cmp={cmp_}")
    assert cmp_.get("screenChanged") is True, cmp_
    assert "Submitted" in blob or "submitted" in (cmp_.get("summary") or "").lower()


def test_live_vision_cold_warm_profile():
    provider = LocalOllamaProvider(
        base_url="http://127.0.0.1:11434/v1",
        vision_model="llava",
        timeout_s=40,
        auto_start=True,
    )
    ready = provider.ensure_ready()
    assert ready.get("ok"), ready
    vision = LocalVisionProvider(provider, timeout_s=40.0, model="llava")
    before_ps = ollama_ps(provider.base_url)
    from src.capture.screen import ScreenCapture

    cap = ScreenCapture({"monitors": "all"})
    shot = cap.capture_monitor(0)
    assert shot and shot.get("image") is not None
    t_pre = time.perf_counter()
    b64, size = ScreenCapture.encode_for_vision(shot["image"])
    pre_ms = (time.perf_counter() - t_pre) * 1000
    prompt = "Name the active window in one short sentence. Do not invent details."
    t0 = time.perf_counter()
    cold = vision.analyze(b64, prompt, timeout_s=40.0)
    cold_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    warm = vision.analyze(b64, prompt + " Warm.", timeout_s=40.0)
    warm_ms = (time.perf_counter() - t1) * 1000
    after_ps = ollama_ps(provider.base_url)
    print(
        f"[LIVE] vision_profile preprocess={pre_ms:.1f}ms image={size} "
        f"cold_wall={cold_ms:.1f}ms warm_wall={warm_ms:.1f}ms "
        f"cold_stats={cold.get('stats')} warm_stats={warm.get('stats')} "
        f"ps_before={before_ps.get('models')} ps_after={after_ps.get('models')}"
    )
    assert cold.get("text")
    assert warm.get("text")
    assert cold_ms < 40000
    assert warm_ms < 40000
    report = Path(__file__).resolve().parents[1] / "docs" / "PHASE_3_VISION_PROFILE.json"
    report.write_text(
        json.dumps(
            {
                "preprocess_ms": round(pre_ms, 1),
                "image_size": list(size),
                "cold_wall_ms": round(cold_ms, 1),
                "warm_wall_ms": round(warm_ms, 1),
                "cold_stats": cold.get("stats"),
                "warm_stats": warm.get("stats"),
                "ps_before": before_ps,
                "ps_after": after_ps,
                "timeout_s": 40,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
