"""Dashboard data: clock helpers, weather, and daily tasks."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import httpx


DEFAULT_TASKS = [
    {"id": "1", "text": "Review morning emails", "done": False},
    {"id": "2", "text": "Check calendar & priorities", "done": False},
    {"id": "3", "text": "Continue Jarvis agent work", "done": False},
]


class DailyTaskStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._day = date.today().isoformat()
        self.tasks: list[dict[str, Any]] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self.tasks = [dict(t) for t in DEFAULT_TASKS]
            self.save()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("day") != date.today().isoformat():
                # New day — reset completion, keep text list if present
                prior = data.get("tasks") or DEFAULT_TASKS
                self.tasks = [
                    {"id": str(t.get("id", i)), "text": t.get("text", ""), "done": False}
                    for i, t in enumerate(prior, start=1)
                    if t.get("text")
                ] or [dict(t) for t in DEFAULT_TASKS]
                self._day = date.today().isoformat()
                self.save()
            else:
                self.tasks = data.get("tasks") or [dict(t) for t in DEFAULT_TASKS]
                self._day = data.get("day", self._day)
        except Exception:
            self.tasks = [dict(t) for t in DEFAULT_TASKS]
            self.save()

    def save(self) -> None:
        payload = {"day": date.today().isoformat(), "tasks": self.tasks}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def toggle(self, task_id: str) -> None:
        for t in self.tasks:
            if t["id"] == task_id:
                t["done"] = not t.get("done", False)
                break
        self.save()

    def add(self, text: str) -> dict[str, Any]:
        task = {
            "id": str(int(datetime.now().timestamp() * 1000)),
            "text": text.strip(),
            "done": False,
        }
        self.tasks.append(task)
        self.save()
        return task

    def remaining(self) -> list[dict[str, Any]]:
        return [t for t in self.tasks if not t.get("done")]

    def complete(self, task_id: str, done: bool = True) -> Optional[dict[str, Any]]:
        for t in self.tasks:
            if str(t.get("id")) == str(task_id):
                t["done"] = done
                self.save()
                return t
        return None

    def delete(self, task_id: str) -> Optional[dict[str, Any]]:
        for i, t in enumerate(self.tasks):
            if str(t.get("id")) == str(task_id):
                removed = self.tasks.pop(i)
                self.save()
                return removed
        return None


def fetch_weather(
    latitude: float,
    longitude: float,
    *,
    temperature_unit: str = "fahrenheit",
    location_label: str = "",
) -> dict[str, Any]:
    """Fetch current weather from Open-Meteo (no API key)."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
        "temperature_unit": temperature_unit,
        "wind_speed_unit": "mph",
        "timezone": "auto",
    }
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    current = data.get("current") or {}
    code = int(current.get("weather_code") or 0)
    return {
        "location": location_label or f"{latitude:.2f}, {longitude:.2f}",
        "temperature": current.get("temperature_2m"),
        "humidity": current.get("relative_humidity_2m"),
        "wind_mph": current.get("wind_speed_10m"),
        "weather_code": code,
        "condition": weather_code_label(code),
        "unit": "°F" if temperature_unit == "fahrenheit" else "°C",
    }


def geocode_city(city: str) -> Optional[dict[str, Any]]:
    url = "https://geocoding-api.open-meteo.com/v1/search"
    with httpx.Client(timeout=8.0) as client:
        r = client.get(url, params={"name": city, "count": 1})
        r.raise_for_status()
        results = (r.json() or {}).get("results") or []
    if not results:
        return None
    hit = results[0]
    label = ", ".join(
        p for p in [hit.get("name"), hit.get("admin1"), hit.get("country_code")] if p
    )
    return {
        "latitude": hit["latitude"],
        "longitude": hit["longitude"],
        "label": label,
    }


def weather_code_label(code: int) -> str:
    mapping = {
        0: "Clear",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Fog",
        51: "Drizzle",
        61: "Rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Snow",
        80: "Showers",
        95: "Thunderstorm",
    }
    return mapping.get(code, "Local conditions")


def format_now() -> dict[str, str]:
    now = datetime.now()
    return {
        "time": now.strftime("%I:%M %p").lstrip("0"),
        "seconds": now.strftime("%S"),
        "date": now.strftime("%d %b, %A"),
        "iso_day": now.date().isoformat(),
    }


def _normalize_utterance(text: str) -> str:
    t = text.strip().lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u2032", "'").replace("`", "'")
    t = t.replace("what's", "what is").replace("whats", "what is")
    t = t.replace("how's", "how is").replace("hows", "how is")
    t = t.replace("to-do", "todo")
    t = re.sub(r"[^\w\s]", " ", t)
    t = " ".join(t.split())
    return re.sub(r"\bwhat s\b", "what is", t)


_TASK_WORD = re.compile(r"\b(task|tasks|todo|todos)\b")
_TASK_QUESTION = re.compile(
    r"\b(what|list|show|tell|read|which|remind|remaining)\b|"
    r"\b(today|have to do|need to do|left to do|supposed to do)\b"
)
_TASK_ACTION = re.compile(
    r"\b(open|find|search|delete|create|write|email|move|copy|rename|download|index)\b"
)


def _is_daily_task_question(t: str) -> bool:
    if t in {"tasks", "my tasks", "todo", "todos", "my todo", "my todos"}:
        return True
    if _TASK_ACTION.search(t):
        return False
    if "on my list" in t or "on the list" in t:
        return True
    if _TASK_WORD.search(t) and _TASK_QUESTION.search(t):
        return True
    # Voice often drops "what" / "tasks": "that I have to do today"
    if re.search(r"\b(have to do|need to do|left to do|supposed to do|gotta do)\b", t) and re.search(
        r"\btoday\b", t
    ):
        return True
    if t.startswith("what do i have to do") or t.startswith("what do i need to do"):
        return True
    return False


def hud_spoken_reply(
    text: str,
    tasks: DailyTaskStore,
    weather: Optional[dict[str, Any]] = None,
    *,
    stt_confidence: float = 1.0,
) -> Optional[str]:
    """Instant spoken answers for HUD facts — no LLM round-trip."""
    t = _normalize_utterance(text)
    if not t:
        return None

    from src.agent.local_intent import apply_resolved_intent, resolve_intent

    if re.search(r"\b(saying|says|what is it saying|what does it say)\b", t) and not re.search(
        r"\b(open|launch|go to|navigate)\b", t
    ):
        try:
            from src.agent.observe import speak_page_about

            return speak_page_about(text)
        except Exception as e:
            print(f"[Jarvis] HUD page-about reply skipped: {e}")

    from src.agent.router import page_entity_kind

    kind = page_entity_kind(t)
    if kind:
        try:
            from src.agent.observe import speak_page_entity

            return speak_page_entity(text, kind=kind)
        except Exception as e:
            print(f"[Jarvis] HUD entity reply skipped: {e}")

    if re.search(r"\b(the website|the site|this website|what website|what site|what page am i)\b", t) and not re.search(
        r"\b(open|launch|go to|navigate|search|find|click)\b", t
    ):
        try:
            from src.agent.observe import speak_current_page

            return speak_current_page(text)
        except Exception as e:
            print(f"[Jarvis] HUD page reply skipped: {e}")

    if re.search(r"\bhow many (monitors|screens|displays)\b", t):
        try:
            from src.agent.screen.coords import list_monitor_states

            n = len(list_monitor_states() or [])
        except Exception:
            n = 0
        if n <= 0:
            return "I could not count your monitors."
        if n == 1:
            return "You have one monitor."
        return f"You have {n} monitors."

    resolved = resolve_intent(text, tasks=tasks.tasks, hud_active=True, stt_confidence=stt_confidence)
    applied = apply_resolved_intent(resolved, tasks, weather)
    if applied is not None:
        return applied

    if _is_daily_task_question(t):
        remaining = tasks.remaining()
        if not remaining:
            return "You have no remaining tasks today."
        names = ", ".join(item["text"] for item in remaining if item.get("text"))
        n = len(remaining)
        noun = "task" if n == 1 else "tasks"
        return f"You have {n} {noun} remaining: {names}."

    time_phrases = (
        "what time is it",
        "what is the time",
        "current time",
        "what is the current time",
    )
    if t in {"time", "the time"} or any(p in t for p in time_phrases):
        now = format_now()
        return f"It is {now['time']}."

    weather_phrases = (
        "what is the weather",
        "how is the weather",
        "what is the temperature",
        "how is it outside",
    )
    if t in {"weather", "temperature"} or any(p in t for p in weather_phrases):
        if not weather or weather.get("temperature") is None:
            return "I do not have a weather reading yet."
        loc = weather.get("location") or "your area"
        cond = weather.get("condition") or "local conditions"
        temp = weather.get("temperature")
        unit = weather.get("unit") or "°F"
        return f"It is {temp}{unit} and {cond.lower()} in {loc}."

    return None
