from __future__ import annotations

import time
from dataclasses import dataclass, field

from release_proof.domain.models import AnalysisLimits


@dataclass
class ExecutionBudget:
    limits: AnalysisLimits
    steps: int = 0
    tool_calls: int = 0
    no_progress: int = 0
    seen_action_keys: set[str] = field(default_factory=set)
    started_at: float = field(default_factory=time.monotonic)
    stop_reason: str | None = None

    def record_step(self, *, added_evidence: int = 0) -> bool:
        self.steps += 1
        self.no_progress = 0 if added_evidence > 0 else self.no_progress + 1
        return self.can_continue()

    def record_tool(self, action_key: str) -> bool:
        if action_key in self.seen_action_keys:
            self.stop_reason = "duplicate_tool_action"
            return False
        self.seen_action_keys.add(action_key)
        self.tool_calls += 1
        return self.can_continue()

    def can_continue(self) -> bool:
        if self.stop_reason:
            return False
        if self.steps >= self.limits.max_steps:
            self.stop_reason = "step_limit"
        elif self.tool_calls >= self.limits.max_tool_calls:
            self.stop_reason = "tool_call_limit"
        elif self.no_progress >= self.limits.max_no_progress:
            self.stop_reason = "no_progress_limit"
        elif time.monotonic() - self.started_at >= self.limits.max_elapsed_seconds:
            self.stop_reason = "elapsed_time_limit"
        return self.stop_reason is None

