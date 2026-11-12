"""Romanian typography and delivery-promise cleanup."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ro_text  # noqa: E402


class TestRoText(unittest.TestCase):
    def test_cedilla_becomes_comma_and_delivery_promises_are_stripped(self):
        t = ro_text.fix("Comanda acum si primesti livrare rapida in 1-3 zile. Pret şi calitate.")
        self.assertIn("ș", t)
        self.assertNotIn("ş", t)
        self.assertNotIn("1-3 zile", t)

    def test_recurses_into_lists_and_dicts(self):
        out = ro_text.fix({"title": "şi", "bullets": ["calitate şi pret"]})
        self.assertIn("ș", out["title"])
        self.assertIn("ș", out["bullets"][0])

    def test_empty_and_non_text_pass_through(self):
        self.assertEqual(ro_text.fix(""), "")
        self.assertEqual(ro_text.fix(None), None)
        self.assertEqual(ro_text.fix(12), 12)


if __name__ == "__main__":
    unittest.main()
