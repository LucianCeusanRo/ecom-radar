"""Settings schema validation rejects bad values without writing files."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import settings  # noqa: E402


class TestSettingsValidate(unittest.TestCase):
    def test_current_config_values_are_valid(self):
        checked = 0
        for field in settings.SCHEMA:
            value = settings.get(vars(config), field["key"])
            # WhatsApp group JIDs come from the environment and are empty in CI
            # (and must stay empty — they are not secrets we commit).
            if field["type"] == "jid" and not value:
                continue
            converted = settings.validate(field, value)
            self.assertIsNotNone(converted)
            checked += 1
        self.assertGreater(checked, 20)

    def test_cost_max_rejects_text(self):
        field = settings.BY_KEY["COST_MAX"]
        with self.assertRaises(ValueError):
            settings.validate(field, "abc")

    def test_bool_and_range(self):
        field = settings.BY_KEY["WA_ENABLED"]
        self.assertTrue(settings.validate(field, "da"))
        self.assertFalse(settings.validate(field, "nu"))
        with self.assertRaises(ValueError):
            settings.validate(field, "maybe")
        cost = settings.BY_KEY["COST_MAX"]
        self.assertEqual(settings.validate(cost, "7,5"), 7.5)
        if "min" in cost:
            with self.assertRaises(ValueError):
                settings.validate(cost, cost["min"] - 1)

    def test_schema_covers_core_keys(self):
        keys = {f["key"] for f in settings.SCHEMA}
        for needed in ("COST_MAX", "COST_MIN", "WA_ENABLED", "DISCORD_ENABLED"):
            self.assertIn(needed, keys)
        self.assertGreaterEqual(len(settings.SCHEMA), 20)


if __name__ == "__main__":
    unittest.main()
