"""ROAS margin math: the minimum sale price that still leaves 20% profit."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402


class TestProfitAtRoas(unittest.TestCase):
    def test_documented_min_prices(self):
        got = [round(config.profit_at_roas(0, a)["min_price"]) for a in (5, 10, 20)]
        self.assertIn(got, ([72, 85, 109], [73, 85, 109]))

    def test_higher_cost_needs_a_higher_sale_price(self):
        cheap = config.profit_at_roas(0, 5)["min_price"]
        mid = config.profit_at_roas(0, 10)["min_price"]
        dear = config.profit_at_roas(0, 20)["min_price"]
        self.assertLess(cheap, mid)
        self.assertLess(mid, dear)

    def test_min_price_hits_the_profit_floor(self):
        for ali in (5, 10, 20):
            floor = config.profit_at_roas(0, ali)["min_price"]
            at_floor = config.profit_at_roas(floor, ali)
            with self.subTest(ali=ali):
                self.assertAlmostEqual(at_floor["pct"], config.MIN_PROFIT_PCT, places=6)
                self.assertGreater(at_floor["profit"], 0)

    def test_selling_below_the_floor_misses_the_margin(self):
        floor = config.profit_at_roas(0, 10)["min_price"]
        below = config.profit_at_roas(floor - 10, 10)
        self.assertLess(below["pct"], config.MIN_PROFIT_PCT)

    def test_zero_sale_price_reports_zero_percent(self):
        row = config.profit_at_roas(0, 5)
        self.assertEqual(row["pct"], 0.0)
        self.assertGreater(row["min_price"], 0)

    def test_shipping_and_import_vat_are_in_the_cost(self):
        # Non-VAT payer: goods are ali * 1.21, plus SHIP_ALI + SHIP_RO.
        self.assertFalse(config.PLATITOR_TVA)
        row = config.profit_at_roas(100, 10)
        expected_goods = 10 * (1 + config.TVA) + config.SHIP_ALI + config.SHIP_RO
        self.assertAlmostEqual(row["costs"], expected_goods, places=6)


if __name__ == "__main__":
    unittest.main()
