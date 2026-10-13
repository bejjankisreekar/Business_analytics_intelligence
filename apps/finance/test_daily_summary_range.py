import datetime
from unittest import mock

from django.test import RequestFactory, SimpleTestCase

from apps.finance.views import DailySummaryRangeMixin


class _Range(DailySummaryRangeMixin):
    def __init__(self, query=""):
        self.request = RequestFactory().get("/app/daily-performance/" + query)


class DailySummaryDefaultRangeTests(SimpleTestCase):
    def _today(self, value):
        patcher = mock.patch("apps.finance.views.datetime.date", wraps=datetime.date)
        fake = patcher.start()
        self.addCleanup(patcher.stop)
        fake.today.return_value = value
        fake.fromisoformat = datetime.date.fromisoformat

    def test_defaults_to_first_of_this_month_through_today(self):
        self._today(datetime.date(2026, 10, 12))
        self.assertEqual(
            _Range().summary_range(), (datetime.date(2026, 10, 1), datetime.date(2026, 10, 12))
        )

    def test_on_the_first_the_range_is_just_that_day(self):
        self._today(datetime.date(2026, 11, 1))
        self.assertEqual(
            _Range().summary_range(), (datetime.date(2026, 11, 1), datetime.date(2026, 11, 1))
        )

    def test_explicit_dates_are_respected(self):
        self._today(datetime.date(2026, 10, 12))
        self.assertEqual(
            _Range("?from=2026-09-05&to=2026-09-20").summary_range(),
            (datetime.date(2026, 9, 5), datetime.date(2026, 9, 20)),
        )

    def test_only_a_to_date_starts_from_the_first_of_that_month(self):
        self._today(datetime.date(2026, 10, 12))
        self.assertEqual(
            _Range("?to=2026-09-20").summary_range(),
            (datetime.date(2026, 9, 1), datetime.date(2026, 9, 20)),
        )
