"""Audience weights and rising/saturated signals, with the history DB in a temp dir."""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import market  # noqa: E402


class TestAudience(unittest.TestCase):
    def test_pet_and_home_are_positive_lingerie_is_dropped(self):
        a = market.audience("Perie cu abur pentru pisici, îndepărtează părul")
        b = market.audience("Sutien push-up fără bretele, mărimi S-XXL")
        k = market.audience("Organizator sertar bucătărie")
        self.assertGreater(a["weight"], 0)
        self.assertLessEqual(b["weight"], config.AUDIENCE_DROP_AT)
        self.assertGreater(k["weight"], 0)
        self.assertIn("pet", a["cats"] or [a["label"].lower()])


class TestRising(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        config.HISTORY_DB = Path(self.tmp.name) / "history.db"

    def tearDown(self):
        self.tmp.cleanup()

    def test_recent_campaign_with_new_ads_is_rising(self):
        today = dt.date.today()
        card = {
            "key": "selftest/products/x",
            "started": (today - dt.timedelta(days=6)).isoformat(),
            "days_running": 6,
            "ad_stats": {
                "ads": 4,
                "new7": 3,
                "first": (today - dt.timedelta(days=6)).isoformat(),
                "last": today.isoformat(),
            },
        }
        r = market.rising(card, advertisers=1)
        self.assertTrue(r["rising"])
        self.assertGreater(r["adj"], 0)

    def test_old_saturated_campaign_is_not_rising(self):
        today = dt.date.today()
        card = {
            "key": "selftest/products/old",
            "started": (today - dt.timedelta(days=120)).isoformat(),
            "days_running": 120,
            "ad_stats": {
                "ads": 4,
                "new7": 0,
                "first": (today - dt.timedelta(days=120)).isoformat(),
                "last": today.isoformat(),
            },
        }
        old = market.rising(card, advertisers=6)
        self.assertFalse(old["rising"])
        self.assertLess(old["adj"], 0)


if __name__ == "__main__":
    unittest.main()
