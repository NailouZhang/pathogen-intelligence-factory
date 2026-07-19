from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .utils import clean_space


# Dates describing when the scholarly work itself became/will become public.
# These fields may be used to decide whether a paper belongs to a weekly issue.
REAL_PUBLICATION_DATE_FIELDS = (
    "first_publication_date",
    "online_date",
    "published_date",
    "print_date",
)

# Dates describing database ingestion, indexing, re-indexing or metadata updates.
# They are audit/provenance only and MUST NOT make a paper pass the date gate.
METADATA_INDEX_DATE_FIELDS = (
    "created_date",
    "indexed_date",
)


@dataclass(frozen=True)
class ParsedDateSpan:
    raw: str
    start: date
    end: date
    precision: str


@dataclass(frozen=True)
class PublicationDateDecision:
    accepted: bool
    canonical_date: str | None
    canonical_basis: str | None
    status: str
    reason: str
    real_dates: dict[str, str]
    metadata_dates: dict[str, str]
    real_date_precisions: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def date_window(days: int, end: date | None = None, timezone_name: str = "Asia/Shanghai") -> tuple[date, date]:
    """Return an inclusive N-calendar-day window in the requested timezone."""
    end = end or datetime.now(ZoneInfo(timezone_name)).date()
    width = max(1, days)
    return end - timedelta(days=width - 1), end


def publication_search_end(end: date, future_days: int) -> date:
    """Bound how far future-dated issue/print metadata may be retrieved."""
    return end + timedelta(days=max(0, future_days))


def parse_date_span(value: Any) -> ParsedDateSpan | None:
    """Parse YYYY, YYYY-MM or YYYY-MM-DD without silently turning partial dates into Jan 1.

    A partial date is represented as an interval. Weekly admission requires at
    least month precision; year-only dates are not precise enough to prove that
    a record is new this week.
    """
    text = clean_space(value)
    if not text:
        return None
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", text)
    if not match:
        return None
    year = int(match.group(1))
    month_text = match.group(2)
    day_text = match.group(3)
    try:
        if month_text is None:
            return ParsedDateSpan(match.group(0), date(year, 1, 1), date(year, 12, 31), "year")
        month = int(month_text)
        if day_text is None:
            last_day = calendar.monthrange(year, month)[1]
            return ParsedDateSpan(match.group(0), date(year, month, 1), date(year, month, last_day), "month")
        day = int(day_text)
        parsed = date(year, month, day)
        return ParsedDateSpan(match.group(0), parsed, parsed, "day")
    except ValueError:
        return None


def _collect_date_spans(record: dict[str, Any], fields: tuple[str, ...]) -> dict[str, ParsedDateSpan]:
    output: dict[str, ParsedDateSpan] = {}
    for field in fields:
        span = parse_date_span(record.get(field))
        if span:
            output[field] = span
    return output


def assess_publication_date(
    record: dict[str, Any],
    start: date,
    end: date,
    *,
    future_days: int = 0,
    allow_month_precision: bool = True,
) -> PublicationDateDecision:
    """Select one canonical publication date, then apply the weekly gate.

    v15 policy chooses the first usable field in this fixed order:
    ``first_publication_date -> online_date -> published_date -> print_date``.
    Created/indexed dates remain provenance only.  Other publication fields do
    not independently revive or reject a record once the canonical field has
    been selected.
    """
    future_end = publication_search_end(end, future_days)
    real_spans = _collect_date_spans(record, REAL_PUBLICATION_DATE_FIELDS)
    metadata_spans = _collect_date_spans(record, METADATA_INDEX_DATE_FIELDS)
    real_dates = {field: span.raw for field, span in real_spans.items()}
    metadata_dates = {field: span.raw for field, span in metadata_spans.items()}
    precisions = {field: span.precision for field, span in real_spans.items()}

    if not real_spans:
        return PublicationDateDecision(
            False, None, None, "rejected", "missing_real_publication_date",
            real_dates, metadata_dates, precisions,
        )

    usable = {
        field: span
        for field, span in real_spans.items()
        if span.precision == "day" or (allow_month_precision and span.precision == "month")
    }
    if not usable:
        return PublicationDateDecision(
            False, None, None, "rejected", "real_publication_date_too_imprecise",
            real_dates, metadata_dates, precisions,
        )

    basis = next((field for field in REAL_PUBLICATION_DATE_FIELDS if field in usable), None)
    if basis is None:
        return PublicationDateDecision(
            False, None, None, "rejected", "missing_usable_canonical_publication_date",
            real_dates, metadata_dates, precisions,
        )
    span = usable[basis]
    if span.end < start:
        return PublicationDateDecision(
            False, span.raw, basis, "rejected", "canonical_publication_date_before_window",
            real_dates, metadata_dates, precisions,
        )
    if span.start > future_end:
        return PublicationDateDecision(
            False, span.raw, basis, "rejected", "canonical_publication_date_beyond_future_grace",
            real_dates, metadata_dates, precisions,
        )
    if span.start > end:
        status = "future_scheduled"
    elif span.precision == "month":
        status = "in_window_month_precision"
    else:
        status = "in_window"
    return PublicationDateDecision(
        True, span.raw, basis, status, "accepted_canonical_publication_date",
        real_dates, metadata_dates, precisions,
    )
