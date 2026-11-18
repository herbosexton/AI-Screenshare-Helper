from __future__ import annotations

from dataclasses import dataclass

from campaign_clips.models import StreamStatus


@dataclass(frozen=True)
class TriggerDecision:
    should_clip: bool
    reason: str


class ViewerSpikeTrigger:
    """Clip when viewers jump by a relative amount between polls."""

    def __init__(self, min_viewers: int = 25, spike_ratio: float = 1.35):
        self.min_viewers = min_viewers
        self.spike_ratio = spike_ratio
        self._last_viewers: dict[str, int] = {}

    def evaluate(self, stream: StreamStatus) -> TriggerDecision:
        prev = self._last_viewers.get(stream.user_id)
        self._last_viewers[stream.user_id] = stream.viewer_count
        if prev is None:
            return TriggerDecision(False, "baseline")
        if stream.viewer_count < self.min_viewers:
            return TriggerDecision(False, "below_min_viewers")
        if prev <= 0:
            return TriggerDecision(False, "invalid_previous")
        if stream.viewer_count >= int(prev * self.spike_ratio):
            return TriggerDecision(
                True,
                f"viewer_spike:{prev}->{stream.viewer_count}",
            )
        return TriggerDecision(False, "no_spike")


class GameMatchTrigger:
    """Only allow clips while the partner is on the campaign game (substring match)."""

    def __init__(self, game_name: str):
        self.game_name = game_name.strip().lower()

    def evaluate(self, stream: StreamStatus) -> TriggerDecision:
        if not self.game_name:
            return TriggerDecision(True, "no_game_filter")
        haystacks = [
            (stream.game_name or "").strip().lower(),
            (stream.title or "").strip().lower(),
        ]
        for current in haystacks:
            if not current:
                continue
            if self.game_name in current or current in self.game_name:
                return TriggerDecision(True, f"game_match:{current}")
        return TriggerDecision(
            False, f"game_mismatch:{stream.game_name or stream.title}"
        )


class IntervalTrigger:
    """Simple cadence trigger — useful for smoke tests on live partners."""

    def __init__(self, every_n_polls: int = 12):
        self.every_n_polls = max(1, every_n_polls)
        self._counts: dict[str, int] = {}

    def evaluate(self, stream: StreamStatus) -> TriggerDecision:
        n = self._counts.get(stream.user_id, 0) + 1
        self._counts[stream.user_id] = n
        if n % self.every_n_polls == 0:
            return TriggerDecision(True, f"interval:{n}")
        return TriggerDecision(False, f"interval_wait:{n}/{self.every_n_polls}")


def combine_all(*decisions: TriggerDecision) -> TriggerDecision:
    """Require every decision to allow clipping; merge reasons."""
    if not decisions:
        return TriggerDecision(False, "no_triggers")
    if all(d.should_clip for d in decisions):
        return TriggerDecision(True, "+".join(d.reason for d in decisions))
    blocked = [d.reason for d in decisions if not d.should_clip]
    return TriggerDecision(False, "+".join(blocked))
