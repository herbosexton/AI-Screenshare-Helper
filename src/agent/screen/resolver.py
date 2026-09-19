"""Visual element scoring. Do not click at random when several targets match."""

from __future__ import annotations

import re
from typing import Any, Optional

from src.agent.screen.errors import AMBIGUOUS_ELEMENT, ELEMENT_NOT_FOUND, ScreenError


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


_COLOR = re.compile(r"\b(blue|red|green|orange|yellow|black|white|gray|grey|purple)\b")
_SIDE = re.compile(r"\b(left|right|top|bottom|middle|center)\b")
_ORDINAL = re.compile(r"\b(first|second|third|1st|2nd|3rd)\b")


def parse_query(query: str) -> dict[str, Any]:
    q = _norm(query)
    q = re.sub(r"^(the|a|an)\s+", "", q)
    q = re.sub(r"\s+button$", "", q)
    color = _COLOR.search(q)
    side = _SIDE.search(q)
    ordinal = _ORDINAL.search(q)
    label = q
    for rx in (_COLOR, _SIDE, _ORDINAL):
        label = rx.sub(" ", label)
    label = re.sub(r"\b(button|field|box|icon|popup|dialog|the|on)\b", " ", label)
    return {
        "raw": q,
        "label": " ".join(label.split()),
        "color": color.group(1) if color else "",
        "side": side.group(1) if side else "",
        "ordinal": ordinal.group(1) if ordinal else "",
    }


def score_element(el: Any, parsed: dict[str, Any]) -> float:
    label = parsed.get("label") or parsed.get("raw") or ""
    blob = _norm(" ".join([getattr(el, "name", "") or "", getattr(el, "kind", "") or "", getattr(el, "automation_id", "") or "", getattr(el, "process_name", "") or ""]))
    score = 0.0
    if not label:
        score = 0.2
    elif label == _norm(el.name):
        score += 0.8
    elif label in blob:
        score += 0.55
    elif any(p in blob for p in label.split() if len(p) > 2):
        score += 0.3
    else:
        return 0.0
    kind = _norm(el.kind)
    if "button" in (parsed.get("raw") or "") and kind in {"button", "hyperlink"}:
        score += 0.1
    if "edit" in (parsed.get("raw") or "") or "field" in (parsed.get("raw") or "") or "textbox" in (parsed.get("raw") or ""):
        if kind in {"edit", "document"}:
            score += 0.1
    b = el.bounds or {}
    left, right = int(b.get("left") or 0), int(b.get("right") or 0)
    top, bottom = int(b.get("top") or 0), int(b.get("bottom") or 0)
    cx = (left + right) / 2 if right > left else 0
    side = parsed.get("side") or ""
    if side == "left" and cx and cx < left + (right - left) * 0.4:
        score += 0.05
    if side == "right" and cx:
        score += 0.05
    if side == "middle" or side == "center":
        score += 0.02
    return min(1.0, score)


class VisualElementResolver:
    def resolve(
        self,
        elements: list[Any],
        query: str,
        *,
        min_score: float = 0.35,
    ) -> tuple[Any, float]:
        parsed = parse_query(query)
        ranked: list[tuple[Any, float]] = []
        for el in elements:
            s = score_element(el, parsed)
            if s >= min_score:
                ranked.append((el, s))
        ranked.sort(key=lambda item: item[1], reverse=True)
        if not ranked:
            raise ScreenError(
                f"Could not find {query!r} on screen",
                ELEMENT_NOT_FOUND,
                details={"query": query},
            )
        word = _norm(parsed.get("label") or parsed.get("raw") or query)
        related = [
            (e, s)
            for e, s in ranked
            if word and word in _norm(getattr(e, "name", "") or "")
        ]
        pool = related if len(related) > 1 else ranked
        if len(pool) > 1:
            best, bs = pool[0]
            second, ss = pool[1]
            if bs < 0.92 and (bs - ss) < 0.12:
                raise ScreenError(
                    "Multiple elements match; not clicking at random",
                    AMBIGUOUS_ELEMENT,
                    retryable=False,
                    details={
                        "query": query,
                        "candidates": [
                            {"name": getattr(a, "name", ""), "kind": getattr(a, "kind", ""), "confidence": round(s, 2)}
                            for a, s in pool[:5]
                        ],
                    },
                )
        return ranked[0]
