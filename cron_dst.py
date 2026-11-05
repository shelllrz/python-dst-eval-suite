"""Simplified, deterministic, and DST-aware cron scheduler.

Supported cron syntax: five fields (minute hour day-of-month month day-of-week),
supporting numbers, '*', lists, ranges, and steps: for example,
'*/15 8-17/2 1,15 * 1-5'. Month/day names and descriptors like @daily are not
supported. Day of week follows cron convention: 0 or 7 = Sunday, 1 = Monday, ..., 6 =
Saturday. When both day-of-month and day-of-week are restricted, the traditional
cron OR rule applies; if either of them is exactly '*', only the other restricts.

Explicit DST policy:
- A non-existent cron time during a spring gap is shifted forward by the gap
  size, preserving minutes (e.g., 02:30 -> 03:30 during a one-hour jump).
- An ambiguous cron time during an autumn fold corresponds to two actual
  occurrences. The function returns the first occurrence that is still strictly
  greater than the current instant; a call between occurrences may therefore
  return the second.
- Aware `now` identifies a real instant and is converted to the requested zone.
  Naive `now` is interpreted as local time and must be unique and valid;
  ambiguous or non-existent current times are rejected. To select one of the folds
  of `now`, provide an aware datetime with `fold` set.

The search iterates over civil dates and resolves each time via a round-trip in UTC;
it does not add hours to the wall clock. Dependencies: Python 3.11+ standard library
only (datetime, dataclasses, zoneinfo, unittest).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_UTC = timezone.utc
_GREGORIAN_CYCLE_DAYS = 146_097  # 400 Gregorian years


class CronExpressionError(ValueError):
    """Malformed cron expression or value out of supported bounds."""


def _parse_field(
    text: str,
    minimum: int,
    maximum: int,
    field_name: str,
    *,
    sunday_alias: bool = False,
) -> frozenset[int]:
    """Parse a cron field supporting lists, ranges, and steps."""
    if not text:
        raise CronExpressionError(f"field {field_name!r} is empty")

    values: set[int] = set()
    for item in text.split(","):
        if not item:
            raise CronExpressionError(f"empty item in field {field_name!r}")

        if item.count("/") > 1:
            raise CronExpressionError(f"invalid step in field {field_name!r}: {item!r}")
        if "/" in item:
            base, step_text = item.split("/", 1)
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronExpressionError(f"invalid step in field {field_name!r}: {item!r}")
            step = int(step_text)
        else:
            base, step = item, 1

        if base == "*":
            start, end = minimum, maximum
        elif base.count("-") == 1:
            start_text, end_text = base.split("-", 1)
            if not start_text.isdigit() or not end_text.isdigit():
                raise CronExpressionError(f"invalid range in field {field_name!r}: {item!r}")
            start, end = int(start_text), int(end_text)
            if start > end:
                raise CronExpressionError(f"inverted range in field {field_name!r}: {item!r}")
        else:
            if not base.isdigit():
                raise CronExpressionError(f"invalid value in field {field_name!r}: {item!r}")
            start = int(base)
            # In cron, `5/10` means 5 and then every 10 up to the limit.
            end = maximum if "/" in item else start

        if start < minimum or end > maximum:
            raise CronExpressionError(
                f"value out of range {minimum}-{maximum} in field {field_name!r}: {item!r}"
            )

        for raw_value in range(start, end + 1, step):
            value = 0 if sunday_alias and raw_value == 7 else raw_value
            values.add(value)

    if not values:
        raise CronExpressionError(f"field {field_name!r} selects no values")
    return frozenset(values)


@dataclass(frozen=True)
class CronExpression:
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    dom_is_star: bool
    dow_is_star: bool

    @classmethod
    def parse(cls, expression: str) -> "CronExpression":
        """Parse a five-field cron expression."""
        if not isinstance(expression, str):
            raise CronExpressionError("cron expression must be a string")
        fields = expression.split()
        if len(fields) != 5:
            raise CronExpressionError(
                "expression must have five fields: minute hour day-of-month month day-of-week"
            )

        minute_text, hour_text, dom_text, month_text, dow_text = fields
        return cls(
            minutes=_parse_field(minute_text, 0, 59, "minute"),
            hours=_parse_field(hour_text, 0, 23, "hour"),
            days_of_month=_parse_field(dom_text, 1, 31, "day-of-month"),
            months=_parse_field(month_text, 1, 12, "month"),
            days_of_week=_parse_field(
                dow_text, 0, 7, "day-of-week", sunday_alias=True
            ),
            dom_is_star=(dom_text == "*"),
            dow_is_star=(dow_text == "*"),
        )

    def matches_date(self, day: date) -> bool:
        """Indicate whether the date satisfies month and cron day rules."""
        if day.month not in self.months:
            return False

        dom_matches = day.day in self.days_of_month
        # datetime.weekday(): Monday=0; cron: Sunday=0.
        cron_weekday = (day.weekday() + 1) % 7
        dow_matches = cron_weekday in self.days_of_week

        if self.dom_is_star and self.dow_is_star:
            return True
        if self.dom_is_star:
            return dow_matches
        if self.dow_is_star:
            return dom_matches
        return dom_matches or dow_matches


def _possible_instants(wall_time: datetime, zone: ZoneInfo) -> list[datetime]:
    """Return valid aware datetimes for a naive wall time, deduplicated by UTC."""
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        assumed = wall_time.replace(tzinfo=zone, fold=fold)
        instant = assumed.astimezone(_UTC)
        normalized = instant.astimezone(zone)
        if normalized.replace(tzinfo=None) == wall_time:
            candidates[instant] = normalized
    return [candidates[instant] for instant in sorted(candidates)]


def _resolve_scheduled_wall_time(wall_time: datetime, zone: ZoneInfo) -> list[datetime]:
    """Resolve a cron wall time, returning one or two real occurrences.

    For a gap, round-trip both fold assumptions through UTC and select the
    forward projection. This shifts by the transition size, e.g. 02:30 -> 03:30.
    """
    valid = _possible_instants(wall_time, zone)
    if valid:
        return valid  # one normal occurrence, or both occurrences of a fold

    forward_projections: dict[datetime, datetime] = {}
    for fold in (0, 1):
        assumed = wall_time.replace(tzinfo=zone, fold=fold)
        instant = assumed.astimezone(_UTC)
        normalized = instant.astimezone(zone)
        normalized_wall = normalized.replace(tzinfo=None)
        if normalized_wall > wall_time:
            forward_projections[instant] = normalized

    if not forward_projections:
        raise ValueError(
            f"could not project non-existent wall time {wall_time!s} in {zone.key}"
        )

    # Forward projection preserves the position within the gap; using the smallest
    # projected civil time avoids shifting more than the transition requires.
    return [
        min(
            forward_projections.values(),
            key=lambda value: (value.replace(tzinfo=None), value.astimezone(_UTC)),
        )
    ]


def _resolve_naive_now(now: datetime, zone: ZoneInfo) -> datetime:
    """Interpret naive `now` as local time only when it maps to one instant."""
    if not isinstance(now, datetime):
        raise TypeError("now must be a datetime instance")
    if now.tzinfo is not None and now.utcoffset() is not None:
        return now.astimezone(zone)

    wall_time = now.replace(tzinfo=None)
    valid = _possible_instants(wall_time, zone)
    if not valid:
        raise ValueError(f"now is a non-existent local time in {zone.key}: {wall_time}")
    if len(valid) > 1:
        raise ValueError(
            f"now is ambiguous in {zone.key}; provide an aware datetime with fold=0 or fold=1"
        )
    return valid[0]


def next_cron_fire(
    expression: str,
    now: datetime,
    zone_key: str,
) -> datetime:
    """Calculate the next actual execution instant, strictly after `now`.

    Args:
        expression: Simplified cron expression with five fields.
        now: Aware datetime (any zone) or unambiguous local naive datetime.
        zone_key: IANA zone name, e.g., ``America/New_York``.

    Returns:
        Next occurrence as an aware datetime in the requested IANA zone.

    Raises:
        CronExpressionError: if the cron expression is invalid.
        ValueError: if naive `now` is ambiguous/non-existent or if no
            occurrence is found within a 400-year Gregorian cycle.
        zoneinfo.ZoneInfoNotFoundError: if the IANA zone does not exist in the system.
    """
    if not isinstance(zone_key, str):
        raise TypeError("zone_key must be an IANA string")
    cron = CronExpression.parse(expression)
    zone = ZoneInfo(zone_key)
    local_now = _resolve_naive_now(now, zone)
    now_utc = local_now.astimezone(_UTC)

    # The Gregorian calendar repeats every 400 years (146,097 days). Limited search
    # prevents infinite loops even for combinations without possible dates.
    for day_offset in range(_GREGORIAN_CYCLE_DAYS + 1):
        try:
            civil_day = local_now.date() + timedelta(days=day_offset)
        except OverflowError:
            break

        if not cron.matches_date(civil_day):
            continue

        for hour in sorted(cron.hours):
            for minute in sorted(cron.minutes):
                wall_time = datetime.combine(civil_day, time(hour, minute))
                for occurrence in _resolve_scheduled_wall_time(wall_time, zone):
                    if occurrence.astimezone(_UTC) > now_utc:
                        return occurrence

    raise ValueError(
        "cron expression has no upcoming occurrence within the 400-year Gregorian cycle"
    )


# ---------------------------------------------------------------------------
# Automated tests: python cron_dst.py
# ---------------------------------------------------------------------------
import unittest


class CronExpressionTests(unittest.TestCase):
    def test_parse_lists_ranges_and_steps(self) -> None:
        cron = CronExpression.parse("*/15 8-17/2 1,15 * 1-5")
        self.assertEqual(cron.minutes, frozenset({0, 15, 30, 45}))
        self.assertEqual(cron.hours, frozenset({8, 10, 12, 14, 16}))
        self.assertEqual(cron.days_of_month, frozenset({1, 15}))
        self.assertEqual(cron.days_of_week, frozenset({1, 2, 3, 4, 5}))

    def test_sunday_zero_and_seven_are_equivalent(self) -> None:
        zero = CronExpression.parse("0 0 * * 0")
        seven = CronExpression.parse("0 0 * * 7")
        self.assertEqual(zero.days_of_week, seven.days_of_week)
        self.assertTrue(zero.matches_date(date(2024, 11, 3)))  # Sunday

    def test_invalid_expressions_raise(self) -> None:
        invalid = (
            "0 2 * *",          # four fields
            "60 2 * * *",       # minute out of range
            "0 24 * * *",       # hour out of range
            "0 2 * 13 *",       # month out of range
            "0 2 * * 8",        # weekday out of range
            "0 2 */0 * *",      # zero step
            "0 2 10-2 * *",     # inverted range
            "0 2 1,,2 * *",     # empty item
        )
        for expression in invalid:
            with self.subTest(expression=expression):
                with self.assertRaises(CronExpressionError):
                    CronExpression.parse(expression)

    def test_non_string_expression_is_rejected(self) -> None:
        with self.assertRaises(CronExpressionError):
            CronExpression.parse(None)  # type: ignore[arg-type]

    def test_dom_and_dow_use_cron_or_semantics(self) -> None:
        # Dom=1 or Monday: on Jan 2, 2024, the next occurrence is Monday, Jan 8.
        now = datetime(2024, 1, 2, 10, 0, tzinfo=ZoneInfo("UTC"))
        result = next_cron_fire("0 9 1 * 1", now, "America/New_York")
        self.assertEqual(result.date(), date(2024, 1, 8))
        self.assertEqual((result.hour, result.minute), (9, 0))


class CronDstSchedulingTests(unittest.TestCase):
    ZONE = "America/New_York"

    def test_spring_gap_shifts_forward_by_gap_size(self) -> None:
        now = datetime(2024, 3, 10, 0, 0, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-10T03:30:00-04:00")
        self.assertEqual(result.astimezone(_UTC), datetime(2024, 3, 10, 7, 30, tzinfo=_UTC))

    def test_gap_projection_already_passed_moves_to_next_day(self) -> None:
        now = datetime(2024, 3, 10, 3, 45, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-11T02:30:00-04:00")

    def test_fold_returns_first_occurrence_when_both_are_future(self) -> None:
        now = datetime(2024, 11, 3, 0, 30, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-04:00")
        self.assertEqual(result.fold, 0)

    def test_fold_returns_second_occurrence_if_first_has_passed(self) -> None:
        now = datetime(2024, 11, 3, 1, 45, tzinfo=ZoneInfo(self.ZONE), fold=0)
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-05:00")
        self.assertEqual(result.fold, 1)

    def test_fold_moves_to_next_day_after_both_occurrences(self) -> None:
        now = datetime(2024, 11, 3, 1, 45, tzinfo=ZoneInfo(self.ZONE), fold=1)
        result = next_cron_fire("30 1 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-04T01:30:00-05:00")

    def test_naive_ambiguous_now_is_rejected(self) -> None:
        now = datetime(2024, 11, 3, 1, 30)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            next_cron_fire("0 2 * * *", now, self.ZONE)

    def test_naive_nonexistent_now_is_rejected(self) -> None:
        now = datetime(2024, 3, 10, 2, 30)
        with self.assertRaisesRegex(ValueError, "non-existent"):
            next_cron_fire("0 3 * * *", now, self.ZONE)

    def test_non_datetime_now_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "datetime"):
            next_cron_fire("0 3 * * *", "2024-03-10T00:00", self.ZONE)  # type: ignore[arg-type]

    def test_aware_now_in_another_zone_is_converted_by_instant(self) -> None:
        now_utc = datetime(2024, 11, 3, 5, 45, tzinfo=_UTC)
        result = next_cron_fire("30 1 * * *", now_utc, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-11-03T01:30:00-05:00")
        self.assertEqual(result.fold, 1)

    def test_occurrence_equal_to_now_is_not_returned(self) -> None:
        now = datetime(2024, 3, 11, 2, 30, tzinfo=ZoneInfo(self.ZONE))
        result = next_cron_fire("30 2 * * *", now, self.ZONE)
        self.assertEqual(result.isoformat(), "2024-03-12T02:30:00-04:00")

    def test_weekday_schedule_skips_weekend(self) -> None:
        now = datetime(2024, 3, 8, 10, 0, tzinfo=ZoneInfo(self.ZONE))  # Friday
        result = next_cron_fire("0 9 * * 1-5", now, self.ZONE)
        self.assertEqual(result.date(), date(2024, 3, 11))
        self.assertEqual((result.hour, result.minute), (9, 0))


if __name__ == "__main__":
    unittest.main()