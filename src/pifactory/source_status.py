from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .utils import clean_space, utc_now_iso


@dataclass
class SourceAudit:
    """Thread-safe source/query audit collector.

    A successful call that returns zero records is recorded as ``success`` with
    ``records=0``. Network, authentication, syntax and parsing failures are
    recorded separately so a green workflow cannot silently masquerade as a
    genuinely empty intelligence window.
    """

    entries: list[dict[str, Any]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add(
        self,
        *,
        source: str,
        query: str = "",
        mode: str = "",
        status: str,
        records: int = 0,
        pages: int = 0,
        endpoint: str = "",
        error: Any = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "source": clean_space(source),
            "query": clean_space(query),
            "mode": clean_space(mode),
            "status": clean_space(status),
            "records": int(records or 0),
            "pages": int(pages or 0),
            "endpoint": clean_space(endpoint),
            "error": clean_space(error)[:800] if error else None,
            "at": utc_now_iso(),
        }
        if details:
            row["details"] = details
        with self._lock:
            self.entries.append(row)

    def summary(self) -> dict[str, Any]:
        sources: dict[str, dict[str, Any]] = {}
        for row in self.entries:
            source = row["source"] or "unknown"
            item = sources.setdefault(
                source,
                {
                    "source": source,
                    "queries": 0,
                    "successful_queries": 0,
                    "zero_result_queries": 0,
                    "failed_queries": 0,
                    "skipped_queries": 0,
                    "records_reported": 0,
                    "statuses": [],
                },
            )
            item["queries"] += 1
            status = row.get("status")
            item["statuses"].append(status)
            item["records_reported"] += int(row.get("records") or 0)
            if status == "success":
                item["successful_queries"] += 1
                if int(row.get("records") or 0) == 0:
                    item["zero_result_queries"] += 1
            elif status == "skipped":
                item["skipped_queries"] += 1
            else:
                item["failed_queries"] += 1
        ordered = sorted(sources.values(), key=lambda x: x["source"].casefold())
        for item in ordered:
            if item["successful_queries"] and item["records_reported"]:
                item["health"] = "degraded" if item["failed_queries"] else "healthy"
            elif item["successful_queries"]:
                item["health"] = "empty"
            elif item["skipped_queries"] and not item["failed_queries"]:
                item["health"] = "skipped"
            else:
                item["health"] = "failed"
        return {
            "generated_at": utc_now_iso(),
            "overall": {
                "healthy": sum(x["health"] == "healthy" for x in ordered),
                "degraded": sum(x["health"] == "degraded" for x in ordered),
                "empty": sum(x["health"] == "empty" for x in ordered),
                "failed": sum(x["health"] == "failed" for x in ordered),
                "skipped": sum(x["health"] == "skipped" for x in ordered),
            },
            "sources": ordered,
            "entries": list(self.entries),
        }
