from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ProviderRuntimeState:
    provider: str
    status: str = "healthy"
    disabled_reason: str = ""
    cooldown_until: float = 0.0
    requests: int = 0
    successes: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    models: dict[str, dict[str, Any]] = field(default_factory=dict)
    account: dict[str, Any] = field(default_factory=dict)

    def available(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.status in {"authentication_failed", "quota_exhausted", "disabled"}:
            return False
        if self.cooldown_until > now:
            return False
        if self.status == "cooldown" and self.cooldown_until <= now:
            self.status = "healthy"
            self.disabled_reason = ""
        return True

    def model_available(self, model: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        row = self.models.get(model) or {}
        return float(row.get("cooldown_until") or 0.0) <= now and row.get("status") not in {
            "authentication_failed",
            "quota_exhausted",
            "disabled",
        }

    def mark_success(self, model: str, usage: dict[str, Any] | None = None) -> None:
        self.requests += 1
        self.successes += 1
        self.status = "healthy"
        self.disabled_reason = ""
        usage = usage or {}
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        row = self.models.setdefault(model, {})
        row["status"] = "healthy"
        row["requests"] = int(row.get("requests") or 0) + 1
        row["successes"] = int(row.get("successes") or 0) + 1
        row["prompt_tokens"] = int(row.get("prompt_tokens") or 0) + prompt
        row["completion_tokens"] = int(row.get("completion_tokens") or 0) + completion
        row["total_tokens"] = int(row.get("total_tokens") or 0) + total

    def mark_failure(self, model: str, category: str, *, cooldown_seconds: int = 60) -> None:
        self.requests += 1
        self.failures += 1
        row = self.models.setdefault(model, {})
        row["requests"] = int(row.get("requests") or 0) + 1
        row["failures"] = int(row.get("failures") or 0) + 1
        row["last_failure_category"] = category
        if category in {"authentication_failed", "quota_exhausted"}:
            self.status = category
            self.disabled_reason = category
            row["status"] = category
            return
        if category == "rate_limited":
            row["status"] = "cooldown"
            row["cooldown_until"] = time.time() + max(1, cooldown_seconds)
            return
        if category in {"provider_unavailable", "network_error", "timeout"}:
            self.status = "cooldown"
            self.disabled_reason = category
            self.cooldown_until = time.time() + max(1, cooldown_seconds)
            row["status"] = "cooldown"
            row["cooldown_until"] = self.cooldown_until
            return
        row["status"] = "failed"

    def safe_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["cooldown_remaining_seconds"] = max(0, round(self.cooldown_until - time.time(), 1))
        output.pop("cooldown_until", None)
        for row in output.get("models", {}).values():
            until = float(row.pop("cooldown_until", 0.0) or 0.0)
            row["cooldown_remaining_seconds"] = max(0, round(until - time.time(), 1))
        return output
