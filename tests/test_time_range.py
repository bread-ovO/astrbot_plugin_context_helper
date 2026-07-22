from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from time_range import parse_time_range


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 22, 14, 30, tzinfo=TZ)


class TimeRangeTests(unittest.TestCase):
    def test_relative_hours(self):
        result = parse_time_range("2小时", "Asia/Shanghai", NOW)
        self.assertEqual(result.end_ms - result.start_ms, 2 * 60 * 60 * 1000)

    def test_today(self):
        result = parse_time_range("今天", "Asia/Shanghai", NOW)
        self.assertEqual(datetime.fromtimestamp(result.start_ms / 1000, TZ).hour, 0)
        self.assertEqual(result.end_ms, int(NOW.timestamp() * 1000))

    def test_absolute_same_day(self):
        result = parse_time_range(
            "2026-07-22 10:00 至 12:30", "Asia/Shanghai", NOW
        )
        self.assertEqual(result.end_ms - result.start_ms, 150 * 60 * 1000)

    def test_invalid_range(self):
        with self.assertRaises(ValueError):
            parse_time_range("晚饭后", "Asia/Shanghai", NOW)


if __name__ == "__main__":
    unittest.main()
