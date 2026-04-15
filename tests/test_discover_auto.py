"""Tests for discover() provider=auto ordering (Google before LeadFinder)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import providers


def _minimal_google_row() -> dict[str, str]:
    return {
        "business_name": "Real Plumbing Co",
        "website_url": "https://example-real-business.test",
        "address": "1 Main St",
        "phone": "",
        "category": "plumber",
        "source": "google",
        "place_id": "ChIJfake",
    }


def _minimal_lf_row() -> dict[str, str]:
    return {
        "business_name": "Synthetic LLC",
        "website_url": "https://www.synthetic-demo.test",
        "address": "",
        "phone": "",
        "category": "",
        "source": "leadfinder",
        "place_id": "",
    }


class TestDiscoverAutoOrder(unittest.TestCase):
    def test_google_runs_before_leadfinder_when_key_set(self) -> None:
        google_rows = [_minimal_google_row()]

        with patch.object(providers, "search_osm", return_value=[]):
            with patch.object(providers, "search_google", return_value=google_rows) as sg:
                with patch.object(providers, "search_leadfinder") as lf:
                    rows, used = providers.discover(
                        "plumbers in Austin TX",
                        provider="auto",
                        google_api_key="AIzaFakeKeyForTest",
                        max_results=10,
                    )
        lf.assert_not_called()
        sg.assert_called_once()
        self.assertEqual(used, "google")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "google")

    def test_leadfinder_used_when_no_google_key_and_osm_empty(self) -> None:
        lf_rows = [_minimal_lf_row()]

        with patch.object(providers, "search_osm", return_value=[]):
            with patch.object(providers, "search_google") as sg:
                with patch.object(providers, "search_leadfinder", return_value=lf_rows) as lf:
                    rows, used = providers.discover(
                        "plumbers in Austin TX",
                        provider="auto",
                        google_api_key="",
                        max_results=10,
                    )
        sg.assert_not_called()
        lf.assert_called_once()
        self.assertEqual(used, "leadfinder")
        self.assertEqual(rows[0]["source"], "leadfinder")

    def test_osm_still_first_when_it_returns_rows(self) -> None:
        osm_rows = [
            {
                "business_name": "OSM Shop",
                "website_url": "https://osm-only.test",
                "address": "2 Oak Ave",
                "phone": "",
                "category": "plumber",
                "source": "osm",
                "place_id": "",
            }
        ]

        with patch.object(providers, "search_osm", return_value=osm_rows):
            with patch.object(providers, "search_google") as sg:
                with patch.object(providers, "search_leadfinder") as lf:
                    rows, used = providers.discover(
                        "plumbers in Austin TX",
                        provider="auto",
                        google_api_key="AIzaFakeKeyForTest",
                        max_results=10,
                    )
        sg.assert_not_called()
        lf.assert_not_called()
        self.assertEqual(used, "osm")


if __name__ == "__main__":
    unittest.main()
