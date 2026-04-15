"""Tests for run_leads evidence cell helper."""

from __future__ import annotations

import unittest

from run_leads import problems_evidence_cell


class TestProblemsEvidenceCell(unittest.TestCase):
    def test_formats_signals_and_quote(self) -> None:
        cell = problems_evidence_cell(
            [
                {
                    "signal_ids_used": ["no_booking_system"],
                    "evidence_quote": "Call us to schedule",
                }
            ]
        )
        self.assertIn("signals=no_booking_system", cell)
        self.assertIn("Call us to schedule", cell)

    def test_empty(self) -> None:
        self.assertEqual(problems_evidence_cell([]), "")


if __name__ == "__main__":
    unittest.main()
