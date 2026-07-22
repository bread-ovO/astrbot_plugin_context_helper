from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimeRange:
    start_ms: int
    end_ms: int
    label: str


_DURATION_RE = re.compile(r"^(?:最近)?\s*(\d+)\s*(分钟|分|小时|时|天)$")
_ABSOLUTE_RE = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2})\s+(\d{1,2}:\d{2})\s*(?:至|到|~|～|-)\s*"
    r"(?:(\d{4}-\d{1,2}-\d{1,2})\s+)?(\d{1,2}:\d{2})$"
)


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def parse_time_range(expression: str, timezone: str, now: datetime | None = None) -> TimeRange:
    tz = ZoneInfo(timezone)
    current = now.astimezone(tz) if now else datetime.now(tz)
    text = expression.strip()

    duration = _DURATION_RE.fullmatch(text)
    if duration:
        amount = int(duration.group(1))
        if amount <= 0:
            raise ValueError("时长必须大于 0")
        unit = duration.group(2)
        delta = timedelta(
            minutes=amount if unit in {"分钟", "分"} else 0,
            hours=amount if unit in {"小时", "时"} else 0,
            days=amount if unit == "天" else 0,
        )
        start = current - delta
        return TimeRange(_milliseconds(start), _milliseconds(current), text)

    day_start = datetime.combine(current.date(), time.min, tz)
    if text == "今天":
        return TimeRange(_milliseconds(day_start), _milliseconds(current), text)
    if text == "昨天":
        start = day_start - timedelta(days=1)
        return TimeRange(_milliseconds(start), _milliseconds(day_start), text)

    absolute = _ABSOLUTE_RE.fullmatch(text)
    if absolute:
        start_date, start_clock, end_date, end_clock = absolute.groups()
        end_date = end_date or start_date
        start = datetime.strptime(f"{start_date} {start_clock}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        end = datetime.strptime(f"{end_date} {end_clock}", "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if end <= start:
            raise ValueError("结束时间必须晚于开始时间")
        return TimeRange(_milliseconds(start), _milliseconds(end), text)

    raise ValueError(
        "无法识别时段。示例：30分钟、2小时、今天、昨天、"
        "2026-07-22 10:00 至 12:30"
    )

