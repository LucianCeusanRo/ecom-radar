"""The AI JSON extractor must accept fenced or raw payloads and reject prose."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import llm  # noqa: E402


class TestExtractJson(unittest.TestCase):
    def test_fenced_block(self):
        d = llm._extract_json('Raspuns: ```json\n{"index": 2, "reason": "ok"}\n```')
        self.assertEqual(d, {"index": 2, "reason": "ok"})

    def test_raw_object_and_array(self):
        self.assertEqual(llm._extract_json('here {"a": 1} thanks'), {"a": 1})
        self.assertEqual(llm._extract_json("prefix [1, 2]"), [1, 2])

    def test_missing_json_raises(self):
        with self.assertRaises(llm.LlmError):
            llm._extract_json("sorry, no structured answer")


if __name__ == "__main__":
    unittest.main()
