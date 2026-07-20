from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StageRecord:
    name: str
    configured_limit_seconds: float
    started_at_monotonic: float
    ended_at_monotonic: float | None = None
    stop_reason: str = ""
    checks: int = 0

    @property
    def elapsed_seconds(self) -> float:
        end = self.ended_at_monotonic if self.ended_at_monotonic is not None else time.monotonic()
        return max(0.0, end - self.started_at_monotonic)


@dataclass
class RuntimeBudget:
    """Wall-clock budget shared by the complete profile pipeline.

    Stage limits are independent ceilings, but no stage may consume the finalization
    reserve.  The class is deliberately deterministic and side-effect free except
    for timing/audit bookkeeping, making it safe to use in tests and production.
    """

    profile_runtime_minutes: int
    finalization_reserve_minutes: int
    stage_limits_minutes: dict[str, int]
    started_at_monotonic: float = field(default_factory=time.monotonic)
    stages: dict[str, StageRecord] = field(default_factory=dict)
    global_stop_reason: str = ""

    @property
    def hard_deadline(self) -> float:
        return self.started_at_monotonic + max(1, self.profile_runtime_minutes) * 60

    @property
    def expensive_deadline(self) -> float:
        reserve = max(0, self.finalization_reserve_minutes) * 60
        return max(self.started_at_monotonic, self.hard_deadline - reserve)

    def remaining_seconds(self) -> float:
        return max(0.0, self.hard_deadline - time.monotonic())

    def remaining_expensive_seconds(self) -> float:
        return max(0.0, self.expensive_deadline - time.monotonic())

    def start_stage(self, name: str) -> None:
        if name not in self.stages:
            self.stages[name] = StageRecord(
                name=name,
                configured_limit_seconds=max(0, int(self.stage_limits_minutes.get(name, 0))) * 60,
                started_at_monotonic=time.monotonic(),
            )

    def finish_stage(self, name: str, reason: str = "completed") -> None:
        self.start_stage(name)
        row = self.stages[name]
        if row.ended_at_monotonic is None:
            row.ended_at_monotonic = time.monotonic()
        if not row.stop_reason:
            row.stop_reason = reason

    def stage_elapsed_seconds(self, name: str) -> float:
        self.start_stage(name)
        return self.stages[name].elapsed_seconds

    def can_start_expensive(self, stage: str) -> tuple[bool, str]:
        self.start_stage(stage)
        row = self.stages[stage]
        row.checks += 1
        now = time.monotonic()
        if now >= self.expensive_deadline:
            self.global_stop_reason = self.global_stop_reason or "finalization_reserve_entered"
            row.stop_reason = row.stop_reason or self.global_stop_reason
            return False, self.global_stop_reason
        limit = row.configured_limit_seconds
        if limit > 0 and row.elapsed_seconds >= limit:
            reason = f"{stage}_time_budget_exhausted"
            row.stop_reason = row.stop_reason or reason
            return False, reason
        return True, "within_budget"

    def should_finalize(self) -> bool:
        return time.monotonic() >= self.expensive_deadline

    def audit(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "policy_version": "v16-wall-clock-stage-budget-1",
            "profile_runtime_minutes": self.profile_runtime_minutes,
            "finalization_reserve_minutes": self.finalization_reserve_minutes,
            "expensive_stage_stop_minute": max(0, self.profile_runtime_minutes - self.finalization_reserve_minutes),
            "elapsed_seconds": round(max(0.0, now - self.started_at_monotonic), 3),
            "remaining_seconds": round(max(0.0, self.hard_deadline - now), 3),
            "remaining_expensive_seconds": round(max(0.0, self.expensive_deadline - now), 3),
            "global_stop_reason": self.global_stop_reason,
            "stages": {
                name: {
                    "configured_limit_minutes": round(row.configured_limit_seconds / 60, 3),
                    "elapsed_seconds": round(row.elapsed_seconds, 3),
                    "stop_reason": row.stop_reason or ("running" if row.ended_at_monotonic is None else "completed"),
                    "checks": row.checks,
                }
                for name, row in self.stages.items()
            },
        }
