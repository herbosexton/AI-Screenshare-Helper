"""Controlled native Win32 UI for Phase 3 live acceptance tests.

Run:
  python tests/fixtures/phase3_live_harness.py --mode click --status STATUS.json --cmd CMD.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import win32api
import win32con
import win32gui


IDC_CONTINUE = 101
IDC_SAVE = 201
IDC_CANCEL = 202
IDC_LABEL = 301
WM_APP_COMMAND = win32con.WM_APP + 7

TITLES = {
    "click": "JarvisPhase3LiveClick",
    "dialog": "JarvisPhase3Dialog — Do you want to save changes to report.txt?",
    "error": "JarvisPhase3Error — Payment gateway timeout (code 504)",
    "change": "JarvisPhase3Change [Ready]",
}

MESSAGES = {
    "click": "Click the Continue button to proceed.",
    "dialog": "Do you want to save changes to report.txt?",
    "error": "ERROR: Payment gateway timeout (code 504).",
    "change": "Status: Ready",
}


class Harness:
    def __init__(self, mode: str, status_path: Path, cmd_path: Path):
        self.mode = mode
        self.status_path = status_path
        self.cmd_path = cmd_path
        self.hwnd = 0
        self.label = 0
        self.btn_continue = 0
        self.btn_save = 0
        self.btn_cancel = 0
        self.clicked = 0
        self.state = "A"
        self.hinst = win32api.GetModuleHandle(None)
        self.class_name = f"JarvisP3Harness_{mode}_{int(time.time())}"
        self._pending: dict | None = None
        self._alive = True

    def _write_status(self) -> None:
        def rect(hwnd: int) -> list[int]:
            if not hwnd:
                return [0, 0, 0, 0]
            return list(win32gui.GetWindowRect(hwnd))

        title = win32gui.GetWindowText(self.hwnd) if self.hwnd else ""
        payload = {
            "ready": True,
            "mode": self.mode,
            "hwnd": int(self.hwnd),
            "title": title,
            "clicked": self.clicked,
            "state": self.state,
            "window_rect": rect(self.hwnd),
            "button_rect": rect(self.btn_continue or self.btn_save),
            "label": win32gui.GetWindowText(self.label) if self.label else "",
        }
        # Atomic: the test polls this file continuously and must never read a
        # half-written or truncated snapshot.
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, self.status_path)

    def _apply_cmd(self, cmd: dict) -> None:
        op = (cmd.get("op") or "").lower()
        if op == "quit":
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
            return
        if op == "move" and self.hwnd:
            l, t, r, b = win32gui.GetWindowRect(self.hwnd)
            dx = int(cmd.get("dx") or 420)
            dy = int(cmd.get("dy") or 90)
            win32gui.MoveWindow(self.hwnd, l + dx, t + dy, r - l, b - t, True)
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            except Exception:
                pass
            self._write_status()
            return
        if op == "set_b" and self.hwnd:
            self.state = "B"
            win32gui.SetWindowText(self.hwnd, "JarvisPhase3Change [Submitted]")
            if self.label:
                win32gui.SetWindowText(self.label, "Status: Submitted")
            self._write_status()

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_COMMAND:
            ctrl = win32api.LOWORD(wparam)
            if ctrl in {IDC_CONTINUE, IDC_SAVE, IDC_CANCEL}:
                self.clicked += 1
                if self.label and ctrl == IDC_CONTINUE:
                    win32gui.SetWindowText(self.label, "Clicked OK")
                if self.hwnd and ctrl == IDC_CONTINUE:
                    win32gui.SetWindowText(self.hwnd, "JarvisPhase3LiveClick [Clicked]")
                self._write_status()
            return 0
        if msg == WM_APP_COMMAND:
            cmd = self._pending
            self._pending = None
            if cmd:
                self._apply_cmd(cmd)
            else:
                self._write_status()
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            self._alive = False
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def run(self) -> int:
        wc = win32gui.WNDCLASS()
        wc.hInstance = self.hinst
        wc.lpszClassName = self.class_name
        wc.lpfnWndProc = self._wnd_proc
        wc.hbrBackground = win32con.COLOR_WINDOW + 1
        wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(wc)

        title = TITLES[self.mode]
        style = win32con.WS_OVERLAPPED | win32con.WS_CAPTION | win32con.WS_SYSMENU | win32con.WS_VISIBLE
        self.hwnd = win32gui.CreateWindow(
            self.class_name,
            title,
            style,
            120,
            80,
            640,
            360,
            0,
            0,
            self.hinst,
            None,
        )
        child = win32con.WS_CHILD | win32con.WS_VISIBLE
        self.label = win32gui.CreateWindow(
            "STATIC",
            MESSAGES[self.mode],
            child,
            24,
            24,
            580,
            80,
            self.hwnd,
            IDC_LABEL,
            self.hinst,
            None,
        )
        if self.mode == "click":
            self.btn_continue = win32gui.CreateWindow(
                "BUTTON",
                "Continue",
                child | win32con.BS_DEFPUSHBUTTON,
                40,
                140,
                220,
                56,
                self.hwnd,
                IDC_CONTINUE,
                self.hinst,
                None,
            )
        elif self.mode == "dialog":
            self.btn_save = win32gui.CreateWindow(
                "BUTTON", "Save", child | win32con.BS_DEFPUSHBUTTON, 40, 140, 140, 48, self.hwnd, IDC_SAVE, self.hinst, None
            )
            self.btn_cancel = win32gui.CreateWindow(
                "BUTTON", "Cancel", child, 200, 140, 140, 48, self.hwnd, IDC_CANCEL, self.hinst, None
            )
        elif self.mode == "change":
            win32gui.CreateWindow(
                "BUTTON", "Continue", child, 40, 140, 180, 48, self.hwnd, IDC_CONTINUE, self.hinst, None
            )

        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNORMAL)
        win32gui.UpdateWindow(self.hwnd)
        try:
            win32gui.SetForegroundWindow(self.hwnd)
        except Exception:
            pass
        self._write_status()

        import threading

        def _poll() -> None:
            while self._alive:
                try:
                    if self.cmd_path.exists():
                        raw = self.cmd_path.read_text(encoding="utf-8").strip()
                        if raw:
                            self.cmd_path.write_text("", encoding="utf-8")
                            self._pending = json.loads(raw)
                            win32gui.PostMessage(self.hwnd, WM_APP_COMMAND, 0, 0)
                    self._write_status()
                except Exception:
                    pass
                time.sleep(0.12)

        threading.Thread(target=_poll, daemon=True, name="p3-harness-poll").start()
        win32gui.PumpMessages()
        self._alive = False
        return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["click", "dialog", "error", "change"], default="click")
    p.add_argument("--status", required=True)
    p.add_argument("--cmd", required=True)
    args = p.parse_args(argv)
    return Harness(args.mode, Path(args.status), Path(args.cmd)).run()


if __name__ == "__main__":
    sys.exit(main())
