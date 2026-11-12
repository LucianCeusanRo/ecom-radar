"""Alibaba listing parser and keyword helper (no network)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import alibaba  # noqa: E402
import config  # noqa: E402


SAMPLE = (
    '{"price":"5,22-8,29 RON","productId":"1","productUrl":"//www.alibaba.com/product-detail/Cat-Steam-Brush_1.html"}'
    " Min. order: 2 pieces"
)


class TestAlibabaParser(unittest.TestCase):
    def test_parse_extracts_price_url_moq_and_title(self):
        rows = alibaba.parse(SAMPLE)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["price"], 5.22)
        self.assertEqual(row["moq"], "2")
        self.assertIn("alibaba.com/product-detail/Cat-Steam-Brush_1.html", row["url"])
        self.assertIn("Cat Steam Brush", row["title"])
        self.assertEqual(row["item_id"], "alibaba_1")

    def test_parse_deduplicates_the_same_product_id(self):
        rows = alibaba.parse(SAMPLE + " " + SAMPLE)
        self.assertEqual(len(rows), 1)

    def test_parse_empty_page(self):
        self.assertEqual(alibaba.parse("<html></html>"), [])

    def test_to_lei_uses_the_lowest_price_and_currency(self):
        self.assertEqual(alibaba.to_lei("15,22-18,29 RON"), 15.22)
        self.assertEqual(alibaba.to_lei("10 lei"), 10.0)
        self.assertAlmostEqual(alibaba.to_lei("$1.20-2.00"), 1.20 * config.USD_RON)
        self.assertAlmostEqual(alibaba.to_lei("EUR 1,10"), 1.10 * config.EUR_RON)

    def test_keywords_translate_a_romanian_title(self):
        kw = alibaba.keywords("Perie cu abur pentru pisici")
        self.assertIn("brush", kw)
        self.assertLessEqual(len(kw), 4)
        self.assertGreaterEqual(len(kw), 1)


if __name__ == "__main__":
    unittest.main()
