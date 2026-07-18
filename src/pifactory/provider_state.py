from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


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

    @classmethod
    def from_dict(cls, provider: str, data: dict[str, Any] | None) -> "ProviderRuntimeState":
        data = data if isinstance(data, dict) else {}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        payload = {key: value for key, value in data.items() if key in allowed}
        payload["provider"] = provider
        return cls(**payload)

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
            "authentication_failed", "quota_exhausted", "disabled",
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

    def persisted_dict(self) -> dict[str, Any]:
        return asdict(self)

    def safe_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output["cooldown_remaining_seconds"] = max(0, round(self.cooldown_until - time.time(), 1))
        output.pop("cooldown_until", None)
        for row in output.get("models", {}).values():
            until = float(row.pop("cooldown_until", 0.0) or 0.0)
            row["cooldown_remaining_seconds"] = max(0, round(until - time.time(), 1))
        return output


class ProviderStateStore:
    """Daily, cross-process provider quota/cooldown state with file locking."""

    def __init__(self, path: str | Path | None, timezone_name: str = "Asia/Shanghai") -> None:
        self.path = Path(path).expanduser() if path else None
        self.timezone_name = timezone_name

    def _day(self) -> str:
        return datetime.now(ZoneInfo(self.timezone_name)).date().isoformat()

    def _read_locked(self, handle: Any) -> dict[str, Any]:
        handle.seek(0)
        raw = handle.read()
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def load(self, providers: list[str]) -> dict[str, ProviderRuntimeState]:
        defaults = {name: ProviderRuntimeState(name) for name in providers}
        if self.path is None:
            return defaults
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            payload = self._read_locked(handle)
            if payload.get("day") != self._day():
                payload = {"schema_version": 1, "day": self._day(), "providers": {}}
            stored = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
            states = {name: ProviderRuntimeState.from_dict(name, stored.get(name)) for name in providers}
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return states

    def save(self, states: dict[str, ProviderRuntimeState]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            payload = self._read_locked(handle)
            if payload.get("day") != self._day():
                payload = {"schema_version": 1, "day": self._day(), "providers": {}}
            payload["schema_version"] = 1
            payload["day"] = self._day()
            payload["updated_at_epoch"] = time.time()
            payload["providers"] = {name: state.persisted_dict() for name, state in states.items()}
            handle.seek(0)
            handle.truncate()
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
