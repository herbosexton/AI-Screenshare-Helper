"""Application-level browser models. No Playwright types leak to the LLM."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BrowserTab(BaseModel):
    id: str
    title: str = ""
    url: str = ""
    active: bool = False
    created_at: datetime = Field(default_factory=_now)


class BrowserElement(BaseModel):
    id: str
    role: str = "generic"
    name: str = ""
    text: str = ""
    tag: str = ""
    type: str = ""
    placeholder: str = ""
    aria_label: str = ""
    value: str = ""
    href: str = ""
    checked: Optional[bool] = None
    disabled: bool = False
    visible: bool = True
    editable: bool = False
    required: bool = False
    selector: str = ""
    frame_url: str = ""
    bounding_box: dict[str, float] = Field(default_factory=dict)


class FormField(BaseModel):
    label: str = ""
    name: str = ""
    type: str = "text"
    required: bool = False
    value: str = ""
    element_id: str = ""
    options: list[str] = Field(default_factory=list)


class BrowserForm(BaseModel):
    name: str = ""
    selector: str = ""
    fields: list[FormField] = Field(default_factory=list)
    submit_buttons: list[str] = Field(default_factory=list)


class BrowserPageState(BaseModel):
    tab_id: str = ""
    url: str = ""
    title: str = ""
    load_state: str = "unknown"
    text_summary: str = ""
    forms: list[BrowserForm] = Field(default_factory=list)
    links: list[BrowserElement] = Field(default_factory=list)
    buttons: list[BrowserElement] = Field(default_factory=list)
    inputs: list[BrowserElement] = Field(default_factory=list)
    dialogs: list[str] = Field(default_factory=list)
    captcha_likely: bool = False
    auth_likely: bool = False
    fingerprint: str = ""
    viewport: dict[str, int] = Field(default_factory=dict)
    scroll_position: dict[str, int] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_now)
    untrusted: bool = True


class BrowserActionResult(BaseModel):
    success: bool
    action: str
    url: str = ""
    previous_url: str = ""
    title: str = ""
    state_changed: bool = False
    verified: bool = False
    error: Optional[dict[str, Any]] = None
    retryable: bool = False
    recommended_next_state: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
