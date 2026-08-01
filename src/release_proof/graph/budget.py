from __future__ import annotations

import time
from dataclasses import dataclass, field

from release_proof.domain.models import AnalysisLimits


@dataclass
class ExecutionBudget:
    """Run-scoped guard used by the real evidence collection path.

    A tool is admitted only after its canonical action key has been checked.  The
    observation produced by that admitted call is then recorded as a progress step.
    This keeps call-count, duplicate, elapsed-time, and no-progress decisions in one
    place instead of duplicating slightly different counters in each collector.
    """

    limits: AnalysisLimits
    steps: int = 0
    tool_calls: int = 0
    no_progress: int = 0
    seen_action_keys: set[str] = field(default_factory=set)
    started_at: float = field(default_factory=time.monotonic)
    stop_reason: str | None = None

    def record_step(self, *, added_evidence: int = 0) -> bool:
        if self.stop_reason:
            return False
        if self.steps >= self.limits.max_steps:
            self.stop_reason = "step_limit"
            return False
        self.steps += 1
        self.no_progress = 0 if added_evidence > 0 else self.no_progress + 1
        return self._check_after_progress()

    def record_tool(self, action_key: str) -> bool:
        if self.stop_reason:
            return False
        if self.steps >= self.limits.max_steps:
            self.stop_reason = "step_limit"
            return False
        if self._elapsed():
            self.stop_reason = "elapsed_time_limit"
            return False
        if action_key in self.seen_action_keys:
            self.stop_reason = "duplicate_tool_action"
            return False
        if self.tool_calls >= self.limits.max_tool_calls:
            self.stop_reason = "tool_call_limit"
            return False
        self.seen_action_keys.add(action_key)
        self.tool_calls += 1
        return True

    def can_continue(self) -> bool:
        if self.stop_reason:
            return False
        if self._elapsed():
            self.stop_reason = "elapsed_time_limit"
        return self.stop_reason is None

    @property
    def elapsed_ms(self) -> int:
        return round((time.monotonic() - self.started_at) * 1000)

    def snapshot(self) -> dict[str, int | str]:
        payload: dict[str, int | str] = {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "no_progress": self.no_progress,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.stop_reason:
            payload["stop_reason"] = self.stop_reason
        return payload

    def _check_after_progress(self) -> bool:
        if self.no_progress >= self.limits.max_no_progress:
            self.stop_reason = "no_progress_limit"
        elif self._elapsed():
            self.stop_reason = "elapsed_time_limit"
        return self.stop_reason is None

    def _elapsed(self) -> bool:
        return time.monotonic() - self.started_at >= self.limits.max_elapsed_seconds

