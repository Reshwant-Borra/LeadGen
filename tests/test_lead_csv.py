"""Tests for lead_csv helpers."""

from __future__ import annotations

import unittest

from lead_csv import (
    dedupe_rows_by_website,
    normalize_url,
    row_from_csv_dict,
    website_dedupe_key,
)


class TestLeadCsv(unittest.TestCase):
    def test_normalize_url_adds_scheme(self) -> None:
        self.assertEqual(normalize_url("example.com"), "https://example.com")

    def test_website_dedupe_key_trailing_slash(self) -> None:
        a = website_dedupe_key("https://Example.com/path/")
        b = website_dedupe_key("http://example.com/path")
        self.assertEqual(a, b)

    def test_dedupe_rows_keeps_first(self) -> None:
        rows = [
            {"business_name": "A", "website_url": "https://x.com"},
            {"business_name": "B", "website_url": "https://x.com/"},
            {"business_name": "C", "website_url": "https://y.org"},
        ]
        out = dedupe_rows_by_website(rows)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["business_name"], "A")
        self.assertEqual(out[1]["business_name"], "C")

    def test_row_from_csv_dict_aliases(self) -> None:
        r = row_from_csv_dict(
            {
                "Business": "Acme",
                "Website": "https://acme.test",
                "source": "osm",
            }
        )
        assert r is not None
        self.assertEqual(r["business_name"], "Acme")
        self.assertEqual(r["website_url"], "https://acme.test")
        self.assertEqual(r["source"], "osm")


if __name__ == "__main__":
    unittest.main()
