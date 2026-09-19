"""ScreenIntentRouter — visual questions never enter the general planner."""

from __future__ import annotations

import re


def _n(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace("what's", "what is").replace("whats", "what is")
    t = re.sub(r"[.!?]+$", "", t)
    return " ".join(t.split())


class ScreenIntentRouter:
    def route(self, text: str):
        from src.agent.router import FastIntent

        t = _n(text)
        if not t:
            return None

        if re.search(
            r"\b(can you see (?:my |the )?screen|do you have screen access|"
            r"is screen (?:capture|access) (?:on|working|available))\b",
            t,
        ) or t in {"can you see my screen", "can you see my screen right now"}:
            return FastIntent("screen.capture_status", confidence=0.99)

        if re.search(
            r"\b(what do you see on (?:my |the )?screen|what is on (?:my |the )?screen|"
            r"describe (?:my |the )?screen|what are you looking at)\b",
            t,
        ):
            return FastIntent("screen.describe", confidence=0.98)

        if re.search(r"\bwhat(?:'s| is)? on (?:the |my )?(left|right) side\b", t):
            side = re.search(r"\b(left|right)\b", t)
            return FastIntent("screen.describe", args={"region": side.group(1) if side else ""}, confidence=0.9)

        if re.search(
            r"\b(what(?:'s| is)? on (?:my )?(?:other|second|left|right) monitor|"
            r"look at my (?:left|right|other|second) monitor|"
            r"what do you see on (?:the )?monitor)\b",
            t,
        ):
            return FastIntent("screen.describe", args={"monitor": t}, confidence=0.92)

        if re.search(
            r"\b(what application am i in|what(?:'s| is)? (?:the )?app|"
            r"what window am i (?:looking at|in)|what(?:'s| is) (?:the )?active window|"
            r"what window is (?:this|open)|what app am i looking at)\b",
            t,
        ):
            return FastIntent("computer.active_window", confidence=0.96)

        m = re.search(
            r"\b(?:where is|find|locate) (?:the )?(?:blue |red |green )?(.+?)$",
            t,
        )
        if m and "job" not in t:
            name = re.sub(r"\s+button$", "", m.group(1).strip())
            if name:
                return FastIntent("screen.find_element", args={"name": name}, confidence=0.9)

        m = re.search(r"\bwhich button says (.+)$", t)
        if m:
            return FastIntent("screen.find_element", args={"name": m.group(1).strip()}, confidence=0.9)

        if re.search(r"\b(do you see a popup|what is this (?:popup|dialog) asking|what(?:'s| is) this dialog)\b", t):
            return FastIntent("screen.dialog", confidence=0.9)

        if re.search(r"\b(what(?:'s| is)? (?:the |that )?error|can you see the error|what error do you see)\b", t):
            return FastIntent("screen.error", confidence=0.9)

        if re.search(r"\b(what changed|what(?:'s| is) different now|did (?:the |that )?click work|did the window change)\b", t):
            return FastIntent("screen.verify", args={"question": t}, confidence=0.88)

        if re.search(r"\b(is (?:the )?(?:page|screen|window) still loading|which field is selected)\b", t):
            return FastIntent("screen.status_detail", args={"question": t}, confidence=0.85)

        if re.search(r"\bcan you see the file i opened\b", t):
            return FastIntent("computer.active_window", confidence=0.85)

        m = re.match(r"^(?:please )?(?:click|press|tap)(?:\s+(?:on|the))?\s+(.+)$", t)
        if m:
            target = re.sub(r"\s+button$", "", m.group(1).strip())
            target = re.sub(r"^(the|a|an)\s+", "", target)
            if target:
                return FastIntent("screen.click", args={"name": target}, confidence=0.9)

        return None
