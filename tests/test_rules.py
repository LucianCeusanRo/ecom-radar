"""Offline matching, category and score rules used when no AI is available."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import rules  # noqa: E402


class TestCategoryAndAngles(unittest.TestCase):
    def test_known_categories(self):
        self.assertEqual(rules.category("Perie pentru pisici"), "pet")
        self.assertEqual(rules.category("Spray zgarieturi auto parbriz"), "auto")
        self.assertEqual(rules.category("Organizator sertar bucatarie"), "casa")
        self.assertEqual(rules.category("Something unmatched xyz"), "general")

    def test_angles_always_return_three_hooks(self):
        for title in ("Perie pentru pisici", "Lampa LED", "Organizator sertar"):
            ang = rules.angles(title)
            with self.subTest(title=title):
                self.assertEqual(len(ang), 3)
                for item in ang:
                    self.assertTrue(item["name"])
                    self.assertTrue(item["hook"])
                    self.assertGreaterEqual(len(item["hooks"]), 1)


class TestMatch(unittest.TestCase):
    def test_strong_visual_plus_shared_words(self):
        cheap = [{
            "title": "LED Motion Sensor Light",
            "hits": 2,
            "best_rank": 1,
            "best_similarity": True,
            "price": 5,
        }]
        hit, why = rules.match(cheap, "Lampa LED cu senzor de miscare")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["title"], "LED Motion Sensor Light")
        self.assertIn("regula", why)

    def test_rejects_a_weak_visual_signal(self):
        cheap = [{
            "title": "Random gadget",
            "hits": 1,
            "best_rank": 8,
            "best_similarity": False,
            "price": 5,
        }]
        hit, why = rules.match(cheap, "Lampa LED cu senzor de miscare")
        self.assertIsNone(hit)
        self.assertIn("niciun semnal", why)


class TestCardAndContent(unittest.TestCase):
    def test_card_stays_in_the_no_ai_range(self):
        econ = config.profit_at_roas(0, 5)
        card = rules.card("Lampa LED cu senzor", econ, None, 5)
        self.assertGreaterEqual(card["nota"], 1)
        self.assertLessEqual(card["nota"], 7)
        self.assertIn("fără AI", card["nota_reason"])
        self.assertTrue(card["cta"])

    def test_content_template_has_the_shopify_fields(self):
        ang = rules.angles("Organizator sertar bucatarie")
        cv = rules.content("Organizator sertar bucatarie", ang)
        self.assertTrue(cv["title"])
        self.assertLessEqual(len(cv["title"]), 110)
        self.assertTrue(cv["hook"])
        self.assertGreaterEqual(len(cv["bullets"]), 3)
        self.assertEqual(len(cv["how_to"]), 3)
        self.assertTrue(cv["package"])


if __name__ == "__main__":
    unittest.main()
