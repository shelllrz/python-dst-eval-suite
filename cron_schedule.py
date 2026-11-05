from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = ["CronExpressionError", "CronExpression", "next_cron_fire"]


class CronExpressionError(ValueError):
    """Invalid cron expression, invalid 'now', or an expression that never fires."""


# (name, minimum, maximum) for each of the 5 cron fields.
_FIELD_SPECS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),  # 0 and 7 = Sunday
)

# Maximum interval between possible fires is on 02/29 (up to 8 years); extra margin included.
_SEARCH_HORIZON_DAYS = 366 * 9


def _parse_int(text: str, name: str) -> int:
    if not (text.isascii() and text.isdigit()):
        raise CronExpressionError(f"Invalid value in field {name}: {text!r}")
    return int(text)


def _parse_field(text: str, name: str, lo: int, hi: int) -> frozenset[int]:
    if not text:
        raise CronExpressionError(f"Field {name} is empty")

    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronExpressionError(f"Empty list element in field {name}")

        base, has_step, step_text = part.partition("/")
        step = 1
        if has_step:
            step = _parse_int(step_text, name)
            if step < 1:
                raise CronExpressionError(f"Step must be >= 1 in field {name}")

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            left, _, right = base.partition("-")
            start, end = _parse_int(left, name), _parse_int(right, name)
        else:
            start = _parse_int(base, name)
            end = hi if has_step else start  # "a/n" is equivalent to "a-max/n"

        if not (lo <= start <= end <= hi):
            raise CronExpressionError(
                f"Range {start}-{end} out of bounds {lo}-{hi} in field {name}"
            )
        values.update(range(start, end + 1, step))

    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    """Validated and expanded 5-field cron expression."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]  # 0 = Sunday ... 6 = Saturday
    dom_wildcard: bool = False
    dow_wildcard: bool = False

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        if not isinstance(expression, str):
            raise CronExpressionError("Cron expression must be a string")

        fields = expression.split()
        if len(fields) != len(_FIELD_SPECS):
            raise CronExpressionError(
                f"Expected 5 cron fields, received {len(fields)}: {expression!r}"
            )

        parsed = [
            _parse_field(text, name, lo, hi)
            for text, (name, lo, hi) in zip(fields, _FIELD_SPECS)
        ]
        minutes, hours, dom, months, dow = parsed
        dow = frozenset(v % 7 for v in dow)  # normalize 7 -> 0 (Sunday)

        return cls(
            minutes=minutes,
            hours=hours,
            days_of_month=dom,
            months=months,
            days_of_week=dow,
            dom_wildcard=fields[2].startswith("*"),
            dow_wildcard=fields[4].startswith("*"),
        )

    def matches_date(self, day: date) -> bool:
        """Indicates whether the date satisfies month, day of month, and day of week.

        Classic cron semantics: if day of month and day of week are both
        restricted (neither starts with '*'), either matching is sufficient (OR);
        otherwise, both must match (AND).
        """
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days_of_month
        dow_ok = (day.weekday() + 1) % 7 in self.days_of_week  # Sunday = 0
        if self.dom_wildcard or self.dow_wildcard:
            return dom_ok and dow_ok
        return dom_ok or dow_ok


def _resolve_wall_time(naive: datetime, tz: ZoneInfo) -> list[datetime]:
    """Converts a wall clock time into real instants (aware, in `tz`).

    - Normal: one instant.
    - Fold (ambiguous): two instants, fold=0 and fold=1.
    - Gap (non-existent): one instant, shifted forward by the gap duration,
      preserving minutes (02:30 -> 03:30).
    """
    first = naive.replace(tzinfo=tz, fold=0)
    second = naive.replace(tzinfo=tz, fold=1)
    offset_first, offset_second = first.utcoffset(), second.utcoffset()

    if offset_first == offset_second:
        return [first]
    if offset_first > offset_second:  # fold: both occurrences exist
        return [first, second]

    # Gap: fold=0 uses the offset prior to transition; the UTC instant calculated
    # with it, when converted back, falls into the shifted forward time.
    shifted_utc = (naive - offset_first).replace(tzinfo=timezone.utc)
    return [shifted_utc.astimezone(tz)]


def _normalize_now(now: datetime, tz: ZoneInfo) -> datetime:
    """Returns `now` as an aware UTC instant.

    - Aware: converted respecting its offset/fold.
    - Naive: interpreted as wall time in `tz`; rejected if ambiguous (fold) or
      non-existent (gap).
    """
    # Defensive type validation to reject non-datetime objects (e.g. strings)
    if not isinstance(now, datetime):
        raise TypeError(f"'now' must be a datetime instance, got {type(now).__name__}")

    if now.tzinfo is not None and now.utcoffset() is not None:
        return now.astimezone(timezone.utc)

    first = now.replace(tzinfo=tz, fold=0)
    second = now.replace(tzinfo=tz, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise CronExpressionError(
            f"Naive 'now' {now.replace(fold=0).isoformat()} is ambiguous or "
            f"non-existent in {tz.key}; provide an aware datetime"
        )
    return first.astimezone(timezone.utc)


def next_cron_fire(expression: str, now: datetime, zone_key: str) -> datetime:
    """Next actual firing instant, strictly greater than `now`.

    The result is an aware datetime in `zone_key`; in ambiguous times,
    the second occurrence is returned with fold=1.
    """
    cron = CronExpression.parse(expression)
    tz = ZoneInfo(zone_key)

    now_utc = _normalize_now(now, tz)
    start_day = now_utc.astimezone(tz).date() - timedelta(days=1)

    hours = sorted(cron.hours)
    minutes = sorted(cron.minutes)

    best: datetime | None = None
    best_utc: datetime | None = None
    last_offset: int | None = None

    for offset in range(_SEARCH_HORIZON_DAYS):
        if last_offset is not None and offset > last_offset:
            break

        day = start_day + timedelta(days=offset)
        if not cron.matches_date(day):
            continue

        for hour in hours:
            for minute in minutes:
                naive = datetime(day.year, day.month, day.day, hour, minute)
                for candidate in _resolve_wall_time(naive, tz):
                    # Comparison always in UTC: datetimes with the same tzinfo
                    # would be compared by wall time (ignoring fold).
                    candidate_utc = candidate.astimezone(timezone.utc)
                    if candidate_utc > now_utc and (
                        best_utc is None or candidate_utc < best_utc
                    ):
                        best, best_utc = candidate, candidate_utc

        if best is not None and last_offset is None:
            # Gaps may push firings to the next day: examine one more day.
            last_offset = offset + 1

    if best is None:
        raise CronExpressionError(
            f"Expression {expression!r} does not fire within {_SEARCH_HORIZON_DAYS} days"
        )
    return best